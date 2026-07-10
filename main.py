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

# ── /api/messages : Anthropic 패스스루 (파이프라인 + 챗봇) ───
@app.post("/api/messages")
async def messages(req: Request):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY 환경변수 미설정")
    body = await req.body()
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": ANTHROPIC_VERSION,
            },
        )
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")

# ── 헬스체크 ─────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "keys": {
            "anthropic": bool(ANTHROPIC_API_KEY),
            "dart": bool(DART_API_KEY),
            "naver": bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET),
        },
    }

# ── 프런트엔드 ───────────────────────────────────────────────
@app.get("/")
async def root():
    return FileResponse("index.html")
