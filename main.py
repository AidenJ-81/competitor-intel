"""
ConstructAI — 건설사 경쟁사 분석 AI 대시보드 (FastAPI 백엔드)
─────────────────────────────────────────────────────────────
프런트엔드(React, index.html)가 호출하는 엔드포인트:
  · GET  /api/news?company=      → 네이버 뉴스 (실제 기사 + 링크 + 키워드 분류)
  · GET  /api/dart?company=      → DART 공시목록 + 재무 실수치 + 요약
  · POST /api/messages           → Anthropic Messages API 패스스루 (파이프라인 웹검색 + 챗봇)
  · GET  /api/health             → 키 설정 상태
모든 외부 API 키는 서버 환경변수에만 존재하며 브라우저로 노출되지 않는다.
"""
import os
import io
import re
import zipfile
import html as html_lib
import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import urlparse
from email.utils import parsedate_to_datetime

import json
import sqlite3

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

app = FastAPI(title="ConstructAI — 건설사 분석 AI 대시보드")

# ── 환경변수 ──────────────────────────────────────────────────
DART_API_KEY        = os.getenv("DART_API_KEY", "")
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_VERSION   = os.getenv("ANTHROPIC_VERSION", "2023-06-01")

# ── 웍스AI (사내 AI 게이트웨이) ────────────────────────────────
# AI_PROVIDER: "wrks"(웍스만) | "anthropic"(Anthropic만) | "auto"(웍스 우선, 실패 시 Anthropic 폴백)
AI_PROVIDER      = os.getenv("AI_PROVIDER", "auto").lower()
WRKS_API_KEY     = os.getenv("WRKS_API_KEY", "")
WRKS_AGENT_ID    = os.getenv("WRKS_AGENT_ID", "")
WRKS_BASE_URL    = os.getenv("WRKS_BASE_URL", "https://gateway-api.wrks.ai").rstrip("/")
WRKS_ACTOR_EMAIL = os.getenv("WRKS_ACTOR_EMAIL", "")   # System API Key일 때만 동작
WRKS_MODEL_ID    = os.getenv("WRKS_MODEL_ID", "")      # 미지정 시 에이전트 기본 모델

# 공용 API 키 만료일(YYYY-MM-DD). 만료 임박 시 /api/health 와 화면 배너로 경고한다.
# 발급 화면 기준 공용 API = 2027-09-09 만료.
WRKS_KEY_EXPIRES = os.getenv("WRKS_KEY_EXPIRES", "2027-09-09")
KEY_WARN_DAYS    = int(os.getenv("KEY_WARN_DAYS", "45"))

def key_days_left():
    """공용 API 키 만료까지 남은 일수. 파싱 실패 시 None."""
    try:
        y, m, d = (int(x) for x in WRKS_KEY_EXPIRES.split("-"))
        return (date(y, m, d) - date.today()).days
    except Exception:
        return None

# 데이터 저장 경로 (Coolify 퍼시스턴트 볼륨을 이 경로에 마운트하면 재배포에도 유지)
DB_PATH = os.getenv("DB_PATH", "/data/constructai.db")

# ── SQLite 저장소 (팀 공유: 입찰이력 + 설정) ─────────────────
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")   # 동시 접근 안정성
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = _connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS bids (
        id         TEXT PRIMARY KEY,
        data       TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()

init_db()

# ── 유틸 ──────────────────────────────────────────────────────
def strip_html(text: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()

def to_eok(value_str) -> int | None:
    """원 단위 문자열 → 억원 정수 (0/파싱실패는 None)"""
    try:
        n = int(str(value_str).replace(",", ""))
        return round(n / 100_000_000) if n != 0 else None
    except Exception:
        return None

def norm_name(s: str) -> str:
    return re.sub(r"[\s&()·.\-_]", "", s or "").lower()

# ── 뉴스 키워드 분류 (결정론적) ──────────────────────────────
def classify_news(text: str):
    category = "기타"
    if re.search(r"수주|낙찰|계약|착공|준공|공사|턴키|시공권", text):
        category = "수주"
    elif re.search(r"실적|영업이익|매출|적자|흑자|재무|부채|신용등급|손실|현금", text):
        category = "재무"
    elif re.search(r"대표이사|사장|인사|선임|임원|CEO|회장|부회장|사임", text):
        category = "인사"
    elif re.search(r"전략|진출|투자|인수|합병|M&A|MOU|협약|신사업|증설", text):
        category = "전략"

    severity = "low"
    if re.search(r"적자|부도|소송|사고|붕괴|제재|횡령|영업정지|법정관리|워크아웃|하자|중대재해|압수|수사", text):
        severity = "high"
    elif re.search(r"수주|낙찰|계약|인수|합병|증설|실적|투자", text):
        severity = "medium"
    return category, severity

# ── /api/news : 네이버 뉴스 ──────────────────────────────────
@app.get("/api/news")
async def get_news(company: str):
    if not company.strip():
        raise HTTPException(400, "company 파라미터가 필요합니다.")
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise HTTPException(500, "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수 미설정")

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={"query": f"{company} 건설", "display": 8, "sort": "date"},
            headers={
                "X-Naver-Client-Id": NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            },
        )
    data = r.json()
    if r.status_code != 200 or data.get("errorCode"):
        raise HTTPException(502, f"네이버 API 오류: {data.get('errorMessage', r.status_code)}")

    articles = []
    for it in data.get("items", []):
        title = strip_html(it.get("title", ""))
        summary = strip_html(it.get("description", ""))
        cat, sev = classify_news(title + " " + summary)
        link = it.get("originallink") or it.get("link", "")
        try:
            host = urlparse(link).hostname or ""
            source = host[4:] if host.startswith("www.") else (host or "네이버뉴스")
        except Exception:
            source = "네이버뉴스"
        try:
            d = parsedate_to_datetime(it["pubDate"]).date().isoformat()
        except Exception:
            d = it.get("pubDate", "")[:10]
        articles.append({
            "title": title, "summary": summary, "date": d,
            "source": source, "link": link,
            "category": cat, "severity": sev,
        })
    return {"articles": articles}

# ── DART 고유번호(corp_code) 캐시 & 해석 ─────────────────────
_corp_map: dict | None = None

# 표시명 ≠ DART 등록명인 경우의 별칭 (필요시 여기만 수정).
# 별칭은 실제 고유번호 목록에 대해 다시 조회되므로, 틀린 별칭은
# 잘못된 데이터가 아니라 "못 찾음" 오류로 이어진다(안전).
DART_NAME_ALIASES = {
    "SGC E&C": "SGC이앤씨",
    "GS이앤알": "지에스이앤알",
}

async def load_corp_map() -> dict:
    """DART 전체 고유번호(corpCode.xml zip)를 1회 다운로드 후 캐시."""
    global _corp_map
    if _corp_map is not None:
        return _corp_map
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            "https://opendart.fss.or.kr/api/corpCode.xml",
            params={"crtfc_key": DART_API_KEY},
        )
    content = r.content
    if content[:2] != b"PK":  # zip 시그니처 확인 (에러 응답 방어)
        raise HTTPException(502, "DART 키가 유효하지 않거나 고유번호 응답이 zip이 아닙니다.")
    zf = zipfile.ZipFile(io.BytesIO(content))
    xml_name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
    xml_bytes = zf.read(xml_name)

    m: dict = {}
    for _, elem in ET.iterparse(io.BytesIO(xml_bytes), events=("end",)):
        if elem.tag == "list":
            code = (elem.findtext("corp_code") or "").strip()
            nm = (elem.findtext("corp_name") or "").strip()
            stock = (elem.findtext("stock_code") or "").strip()
            if code and nm:
                m[nm] = {"corp_code": code, "stock_code": stock, "name": nm}
            elem.clear()
    _corp_map = m
    return m

def resolve_corp(m: dict, name: str):
    target = DART_NAME_ALIASES.get(name, name)
    if target in m:
        return m[target]
    norm = norm_name(target)
    cands = []
    for v in m.values():
        n = norm_name(v["name"])
        if n == norm:
            return v
        if n and (norm in n or n in norm):
            cands.append(v)
    # 상장사(종목코드 보유) 우선, 그다음 이름 짧은 순
    cands.sort(key=lambda v: (0 if v["stock_code"] else 1, len(v["name"])))
    return cands[0] if cands else None

def disclosure_type(nm: str) -> str:
    if "사업보고서" in nm: return "사업보고서"
    if re.search(r"분기보고서|반기보고서", nm): return "분기보고서"
    if "주요사항" in nm: return "주요사항보고"
    if re.search(r"임원|선임|사외이사|대표이사|감사", nm): return "임원선임"
    if re.search(r"공시|정정|기재정정|자율공시", nm): return "수시공시"
    return "기타"

# ── DART 재무제표 (연도 폴백) ────────────────────────────────
async def fetch_financials(corp_code: str):
    years = [date.today().year - 1, date.today().year - 2]
    async with httpx.AsyncClient(timeout=15) as client:
        for year in years:
            r = await client.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
                params={
                    "crtfc_key": DART_API_KEY, "corp_code": corp_code,
                    "bsns_year": str(year), "reprt_code": "11011",
                },
            )
            data = r.json()
            if data.get("status") != "000" or not data.get("list"):
                continue
            rows = data["list"]

            def pick(matcher):
                for div in ("CFS", "OFS"):  # 연결 우선, 없으면 별도
                    for row in rows:
                        if row.get("fs_div") == div and matcher(row.get("account_nm", "")):
                            v = to_eok(row.get("thstrm_amount"))
                            if v is not None:
                                return v
                return None

            rev = pick(lambda n: re.search(r"매출액|영업수익", n))
            op  = pick(lambda n: n.startswith("영업이익"))
            net = pick(lambda n: "당기순이익" in n)
            liab = pick(lambda n: "부채총계" in n)
            eq  = pick(lambda n: "자본총계" in n)
            debt_ratio = round(liab / eq * 100) if (liab is not None and eq) else None

            fmt = lambda v: f"{v:,}억" if v is not None else "—"
            financials = {
                "revenue": fmt(rev), "op_profit": fmt(op), "net_profit": fmt(net),
                "debt_ratio": f"{debt_ratio}%" if debt_ratio is not None else "—",
                "year": str(year),
            }
            parts = []
            if rev is not None: parts.append(f"매출 {rev:,}억")
            if op is not None: parts.append(f"영업이익 {op:,}억")
            if net is not None: parts.append(f"순이익 {net:,}억")
            if debt_ratio is not None: parts.append(f"부채비율 {debt_ratio}%")
            summary = f"{year}년 기준 {', '.join(parts)} (DART 전자공시 기준)." if parts else ""
            return financials, summary
    return None, ""

# ── /api/dart : 공시목록 + 재무 ──────────────────────────────
@app.get("/api/dart")
async def get_dart(company: str = "", corp_code: str = ""):
    if not company.strip() and not corp_code.strip():
        raise HTTPException(400, "company 파라미터가 필요합니다.")
    if not DART_API_KEY:
        raise HTTPException(500, "DART_API_KEY 환경변수 미설정")

    code = corp_code.strip()
    if not code:
        m = await load_corp_map()
        hit = resolve_corp(m, company)
        if not hit:
            raise HTTPException(404, f"DART에서 '{company}' 고유번호를 찾지 못했습니다. corp_code로 직접 조회하세요.")
        code = hit["corp_code"]

    today = date.today()
    bgn = today.replace(year=today.year - 1)
    fmt_date = lambda d: d.strftime("%Y%m%d")

    async with httpx.AsyncClient(timeout=10) as client:
        lr = await client.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": DART_API_KEY, "corp_code": code,
                "bgn_de": fmt_date(bgn), "end_de": fmt_date(today), "page_count": 15,
            },
        )
    ldata = lr.json()

    disclosures = []
    if ldata.get("status") == "000" and ldata.get("list"):
        for d in ldata["list"][:12]:
            rcept = d.get("rcept_dt", "")
            disclosures.append({
                "title": d.get("report_nm", ""),
                "date": f"{rcept[:4]}-{rcept[4:6]}-{rcept[6:8]}" if len(rcept) == 8 else rcept,
                "type": disclosure_type(d.get("report_nm", "")),
                "link": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={d.get('rcept_no', '')}",
            })
    elif ldata.get("status") not in ("000", "013"):  # 013 = 데이터 없음(정상)
        raise HTTPException(502, f"DART 공시목록 오류: {ldata.get('message', ldata.get('status'))}")

    financials, summary = await fetch_financials(code)
    return {"disclosures": disclosures, "financials": financials, "summary": summary, "corp_code": code}

# ── 웍스AI 클라이언트 ────────────────────────────────────────
class WrksError(Exception):
    """웍스AI 호출 실패. expired=True 면 API 키 만료/인증 문제."""
    def __init__(self, message, expired=False):
        super().__init__(message)
        self.message = message
        self.expired = expired

def _wrks_headers():
    h = {"API-KEY": WRKS_API_KEY, "Content-Type": "application/json"}
    # Actor 헤더는 System API Key 에서만 동작한다. 공용 API가 System 타입이 아니면
    # 서버가 거부할 수 있으므로 환경변수로 명시했을 때만 붙인다.
    if WRKS_ACTOR_EMAIL:
        h["X-Actor-User-Email"] = WRKS_ACTOR_EMAIL
    return h

# 웍스 내부 도구 키
TOOL_WEB_SEARCH = "wrks__search_web"
ALL_INTERNAL_TOOLS = [
    "wrks__run_code", "wrks__generate_image", TOOL_WEB_SEARCH,
    "wrks__render_visualization", "wrks__summarize_document",
]

async def wrks_chat(message: str, websearch: bool = False, timeout: int = 180) -> str:
    """
    웍스AI 에이전트에 단발 질의하고 텍스트만 돌려준다.
    chatId 를 쓰지 않는 무상태 호출 — 대화 맥락은 message 안에 직접 담는다.
    (대화가 서버에 계속 쌓이는 것을 막고, 기존 프런트 동작과 동일하게 유지)
    """
    if not WRKS_API_KEY:
        raise WrksError("WRKS_API_KEY 환경변수가 설정되지 않았습니다.")
    if not WRKS_AGENT_ID:
        raise WrksError("WRKS_AGENT_ID 환경변수가 설정되지 않았습니다. "
                        "/api/wrks/agents 로 사용 가능한 에이전트 ID를 확인하세요.")

    payload = {"message": message, "agentId": str(WRKS_AGENT_ID)}
    if WRKS_MODEL_ID:
        try:
            payload["modelId"] = int(WRKS_MODEL_ID)
        except ValueError:
            pass
    # 웹검색이 필요 없는 요청에서는 내부 웹검색 도구를 꺼서 불필요한 호출·지연을 막는다.
    if websearch:
        payload["enabledInternalTools"] = [TOOL_WEB_SEARCH]
    else:
        payload["disabledInternalTools"] = [TOOL_WEB_SEARCH]

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{WRKS_BASE_URL}/v2/chat/json",
                                  json=payload, headers=_wrks_headers())
    except httpx.HTTPError as e:
        raise WrksError(f"웍스AI 연결 실패: {e}")

    if r.status_code in (401, 403):
        raise WrksError("웍스AI 인증 실패 — API 키가 만료되었거나 유효하지 않습니다.", expired=True)
    if r.status_code >= 400:
        raise WrksError(f"웍스AI 오류 (HTTP {r.status_code})")

    try:
        data = r.json()
    except Exception:
        raise WrksError("웍스AI 응답을 해석할 수 없습니다.")

    if data.get("result") == "error":
        code = data.get("code", "")
        msg = data.get("message") or f"웍스AI 오류 ({code})"
        # E2304/E2305 = 에이전트 없음/권한 없음 → 설정 문제로 안내
        if code in ("E2304", "E2305"):
            msg = f"에이전트 접근 불가 ({code}). WRKS_AGENT_ID 설정을 확인하세요."
        raise WrksError(msg, expired=code in ("E1001", "E1002"))

    d = data.get("data") or {}
    # parts 에서 텍스트만 모은다. 도구가 만든 파일 등 다른 파트 타입은 무시한다.
    parts = d.get("parts") or []
    texts = [p.get("text", "") for p in parts
             if isinstance(p, dict) and p.get("type") == "text"]
    text = "".join(texts).strip()
    if not text:
        text = (d.get("message") or "").strip()
    if not text:
        raise WrksError("웍스AI가 빈 응답을 반환했습니다.")
    return text

def _flatten_anthropic(body: dict) -> tuple:
    """
    Anthropic Messages 형식 요청을 웍스AI용 단일 문자열로 평탄화한다.
    반환: (message, websearch_needed)
    """
    system = (body.get("system") or "").strip()
    msgs = body.get("messages") or []
    websearch = any(
        (t or {}).get("type", "").startswith("web_search")
        for t in (body.get("tools") or [])
    )

    lines = []
    if system:
        lines.append(f"[역할·지침]\n{system}\n")
    if len(msgs) > 1:
        lines.append("[이전 대화]")
        for m in msgs[:-1]:
            who = "사용자" if m.get("role") == "user" else "assistant"
            c = m.get("content")
            if isinstance(c, list):   # Anthropic content 블록 배열 대응
                c = "".join(b.get("text", "") for b in c if isinstance(b, dict))
            lines.append(f"{who}: {c}")
        lines.append("")
    last = msgs[-1] if msgs else {}
    c = last.get("content", "")
    if isinstance(c, list):
        c = "".join(b.get("text", "") for b in c if isinstance(b, dict))
    lines.append(f"[요청]\n{c}")
    return "\n".join(lines), websearch

async def _anthropic_passthrough(body_bytes: bytes):
    """AI_PROVIDER=anthropic 또는 웍스 실패 시 폴백."""
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": ANTHROPIC_VERSION,
            },
        )
    return Response(content=r.content, status_code=r.status_code,
                    media_type="application/json")

def _as_anthropic(text: str):
    """웍스 응답을 프런트가 기대하는 Anthropic 형태로 감싼다."""
    return {"content": [{"type": "text", "text": text}]}

def _as_error(msg: str):
    """
    프런트는 파이프라인에서 data.error.message 를, 챗봇에서 content[0].text 를 읽는다.
    두 곳 모두에 같은 안내가 뜨도록 양쪽에 넣는다.
    """
    return {"error": {"message": msg}, "content": [{"type": "text", "text": f"⚠️ {msg}"}]}

# ── /api/messages : Anthropic 형식 요청을 웍스AI로 중계 ───────
@app.post("/api/messages")
async def messages(req: Request):
    raw = await req.body()
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(400, "잘못된 JSON 요청")

    if AI_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            return _as_error("ANTHROPIC_API_KEY 환경변수 미설정")
        return await _anthropic_passthrough(raw)

    message, websearch = _flatten_anthropic(body)
    try:
        text = await wrks_chat(message, websearch=websearch)
        return _as_anthropic(text)
    except WrksError as e:
        # auto 모드: 웍스가 죽었을 때 Anthropic 키가 있으면 자동 폴백
        if AI_PROVIDER == "auto" and ANTHROPIC_API_KEY:
            try:
                return await _anthropic_passthrough(raw)
            except Exception:
                pass
        return _as_error(e.message)

# ── /api/ai : Artifact sample API 대체 (RFP 분석 · 뉴스 요약) ─
@app.post("/api/ai")
async def ai(req: Request):
    """
    body: {prompt: str, json: bool, websearch: bool}
    json=true 면 모델 출력에서 JSON 을 추출해 {json: {...}} 로 반환한다.
    """
    body = await req.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt 가 필요합니다.")
    want_json = bool(body.get("json"))
    websearch = bool(body.get("websearch"))

    if want_json:
        prompt += "\n\n※ 설명·머리말·코드펜스 없이 JSON 객체 하나만 출력하세요."

    try:
        text = await wrks_chat(prompt, websearch=websearch)
    except WrksError as e:
        return {"error": {"message": e.message}}

    if not want_json:
        return {"text": text}

    parsed = _extract_json(text)
    if parsed is None:
        return {"error": {"message": "AI 응답에서 JSON을 찾지 못했습니다."}, "raw": text[:500]}
    return {"json": parsed}

def _extract_json(text: str):
    """모델이 코드펜스나 설명을 섞어 보내도 JSON 객체를 뽑아낸다."""
    t = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:      esc = False
            elif ch == "\\": esc = True
            elif ch == '"':  in_str = False
            continue
        if ch == '"':   in_str = True
        elif ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None

# ── /api/wrks/agents : 에이전트 ID 확인용 (키는 노출 안 됨) ───
@app.get("/api/wrks/agents")
async def wrks_agents():
    if not WRKS_API_KEY:
        return {"error": {"message": "WRKS_API_KEY 환경변수 미설정"}}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{WRKS_BASE_URL}/v2/agents", headers=_wrks_headers())
    except httpx.HTTPError as e:
        return {"error": {"message": f"연결 실패: {e}"}}
    if r.status_code in (401, 403):
        return {"error": {"message": "인증 실패 — API 키를 확인하세요."}}
    try:
        return r.json()
    except Exception:
        return {"error": {"message": f"응답 해석 실패 (HTTP {r.status_code})"}}

# ── /api/wrks/agents/{id} : MCP 도구 인증 상태 확인 ───────────
@app.get("/api/wrks/agents/{agent_id}")
async def wrks_agent_detail(agent_id: str):
    if not WRKS_API_KEY:
        return {"error": {"message": "WRKS_API_KEY 환경변수 미설정"}}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{WRKS_BASE_URL}/v2/agents/{agent_id}",
                                 headers=_wrks_headers())
        return r.json()
    except Exception as e:
        return {"error": {"message": str(e)}}

# ── 입찰 이력 (팀 공유, 건별 저장) ───────────────────────────
@app.get("/api/bids")
async def list_bids():
    conn = _connect()
    rows = conn.execute("SELECT data FROM bids ORDER BY created_at").fetchall()
    conn.close()
    return {"bids": [json.loads(r["data"]) for r in rows]}

@app.post("/api/bids")
async def add_bid(req: Request):
    bid = await req.json()
    bid_id = str(bid.get("id") or "")
    if not bid_id:
        raise HTTPException(400, "bid.id 가 필요합니다.")
    conn = _connect()
    conn.execute("INSERT OR REPLACE INTO bids (id, data) VALUES (?, ?)",
                 (bid_id, json.dumps(bid, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return {"bid": bid}

@app.patch("/api/bids/{bid_id}")
async def patch_bid(bid_id: str, req: Request):
    """
    기존 입찰 건에 필드를 병합한다 (RFP 분석 결과 저장용).
    전체 덮어쓰기가 아니라 병합이므로, 다른 사람이 같은 건의 다른 필드를
    수정 중이어도 그 값이 날아가지 않는다.
    """
    patch = await req.json()
    conn = _connect()
    row = conn.execute("SELECT data FROM bids WHERE id = ?", (bid_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "해당 입찰 건이 없습니다.")
    bid = json.loads(row["data"])
    bid.update(patch)
    conn.execute("UPDATE bids SET data = ? WHERE id = ?",
                 (json.dumps(bid, ensure_ascii=False), bid_id))
    conn.commit()
    conn.close()
    return {"bid": bid}

@app.delete("/api/bids/{bid_id}")
async def delete_bid(bid_id: str):
    conn = _connect()
    conn.execute("DELETE FROM bids WHERE id = ?", (bid_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ── 설정 (팀 공유 키-값: 예) 모니터링 경쟁사 목록) ───────────
@app.get("/api/settings/{key}")
async def get_setting(key: str):
    conn = _connect()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return {"value": json.loads(row["value"]) if row else None}

@app.put("/api/settings/{key}")
async def put_setting(key: str, req: Request):
    body = await req.json()
    conn = _connect()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (key, json.dumps(body.get("value"), ensure_ascii=False)))
    conn.commit()
    conn.close()
    return {"ok": True}

# ── 헬스체크 ─────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    days = key_days_left()
    db_ok, db_err = True, None
    try:
        conn = _connect()
        conn.execute("SELECT 1").fetchone()
        conn.close()
    except Exception as e:
        db_ok, db_err = False, str(e)

    warn = None
    if days is not None:
        if days < 0:
            warn = f"웍스AI 공용 API 키가 {WRKS_KEY_EXPIRES}자로 만료되었습니다. 재발급이 필요합니다."
        elif days <= KEY_WARN_DAYS:
            warn = f"웍스AI 공용 API 키가 {days}일 뒤({WRKS_KEY_EXPIRES}) 만료됩니다. 재발급을 요청하세요."

    return {
        "status": "ok",
        "provider": AI_PROVIDER,
        "keys": {
            "wrks": bool(WRKS_API_KEY),
            "wrks_agent": bool(WRKS_AGENT_ID),
            "anthropic": bool(ANTHROPIC_API_KEY),
            "dart": bool(DART_API_KEY),
            "naver": bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET),
        },
        "wrks_key_expires": WRKS_KEY_EXPIRES,
        "wrks_key_days_left": days,
        "warning": warn,
        "db": {"ok": db_ok, "path": DB_PATH, "error": db_err},
    }

# ── 프런트엔드 ───────────────────────────────────────────────
@app.get("/")
async def root():
    return FileResponse("index.html")
