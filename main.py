import os
import re
import html as html_lib
from typing import Optional

import httpx
from anthropic import Anthropic
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="건설사 분석 AI 대시보드")

# ── 환경변수 ──────────────────────────────────────────────────────────────────
DART_API_KEY        = os.getenv("DART_API_KEY", "")
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")

# ── DART 기업코드 캐시 ────────────────────────────────────────────────────────
_corp_cache: dict[str, str] = {}

async def get_corp_code(company: str) -> str:
    """기업명으로 DART 고유번호(corp_code) 조회 — list.json 검색 사용"""
    if company in _corp_cache:
        return _corp_cache[company]

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={"crtfc_key": DART_API_KEY, "corp_name": company, "page_count": 5},
        )
    data = r.json()

    if data.get("status") == "000" and data.get("list"):
        # 정확히 이름이 일치하는 항목 우선
        for item in data["list"]:
            if item.get("corp_name") == company:
                _corp_cache[company] = item["corp_code"]
                return item["corp_code"]
        code = data["list"][0]["corp_code"]
        _corp_cache[company] = code
        return code

    raise HTTPException(404, f"기업코드를 찾을 수 없습니다: {company}")


# ── 유틸 ──────────────────────────────────────────────────────────────────────
def strip_html(text: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", text))

def to_100m(value_str: str) -> int:
    """원 단위 문자열 → 억원 정수"""
    try:
        return int(str(value_str).replace(",", "")) // 100_000_000
    except Exception:
        return 0


# ── 헬스체크 ─────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "dart":   bool(DART_API_KEY),
        "naver":  bool(NAVER_CLIENT_ID),
        "claude": bool(ANTHROPIC_API_KEY),
    }


# ── 뉴스 (네이버 검색 API) ────────────────────────────────────────────────────
@app.get("/api/news")
async def get_news(company: str, count: int = Query(10, le=50)):
    if not NAVER_CLIENT_ID:
        raise HTTPException(500, "NAVER_CLIENT_ID 환경변수 미설정")

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={"query": f"{company} 건설", "display": count, "sort": "date"},
            headers={
                "X-Naver-Client-Id": NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            },
        )
    data = r.json()

    items = [
        {
            "company":  company,
            "title":    strip_html(item["title"]),
            "summary":  strip_html(item["description"]),
            "link":     item["link"],
            "date":     item["pubDate"][:16],
            "sentiment": "neu",   # 확장 시 Claude로 감성 분류 가능
        }
        for item in data.get("items", [])
    ]
    return {"items": items}


# ── DART 재무제표 ─────────────────────────────────────────────────────────────
@app.get("/api/dart/financial")
async def get_financial(company: str, year: int = 2023):
    if not DART_API_KEY:
        raise HTTPException(500, "DART_API_KEY 환경변수 미설정")

    corp_code = await get_corp_code(company)

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
            params={
                "crtfc_key":  DART_API_KEY,
                "corp_code":  corp_code,
                "bsns_year":  str(year),
                "reprt_code": "11011",   # 사업보고서
                "fs_div":     "CFS",     # 연결재무제표
            },
        )
    data = r.json()

    if data.get("status") != "000":
        raise HTTPException(404, f"재무 데이터 없음: {data.get('message', '')}")

    rows = data.get("list", [])

    def find(account: str, sj: str | None = None) -> int:
        for row in rows:
            if account in row.get("account_nm", ""):
                if sj and row.get("sj_div") != sj:
                    continue
                return to_100m(row.get("thstrm_amount", "0"))
        return 0

    return {
        "company":          company,
        "year":             year,
        "revenue":          find("매출액",     "IS"),
        "operating_profit": find("영업이익",   "IS"),
        "net_income":       find("당기순이익", "IS"),
        "total_assets":     find("자산총계",   "BS"),
        "total_liabilities":find("부채총계",   "BS"),
        "total_equity":     find("자본총계",   "BS"),
    }


# ── DART 공시 목록 ────────────────────────────────────────────────────────────
@app.get("/api/dart/disclosures")
async def get_disclosures(company: str, count: int = Query(10, le=30)):
    if not DART_API_KEY:
        raise HTTPException(500, "DART_API_KEY 환경변수 미설정")

    corp_code = await get_corp_code(company)

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={"crtfc_key": DART_API_KEY, "corp_code": corp_code, "page_count": count},
        )
    data = r.json()

    items = [
        {
            "date":  item.get("rcept_dt", ""),
            "type":  item.get("report_nm", ""),
            "title": item.get("report_nm", ""),
            "url":   f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item.get('rcept_no','')}",
        }
        for item in data.get("list", [])
    ]
    return {"items": items}


# ── AI 챗봇 (Claude) ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = """당신은 한국 건설업 경쟁 분석 전문 AI입니다.
도급순위 상위 건설사들의 수주 동향, 재무 현황, 입찰 전략, 리스크를 분석합니다.
답변은 핵심만 간결하게 한국어로 작성하고, 수치와 근거를 포함하세요."""

class ChatRequest(BaseModel):
    message: str
    history: Optional[list] = []

@app.post("/api/chat")
async def chat(body: ChatRequest):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY 환경변수 미설정")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = (body.history or [])[-10:] + [{"role": "user", "content": body.message}]

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    return {"response": resp.content[0].text}


# ── 프론트엔드 (최상위 index.html) ───────────────────────────────────────────
@app.get("/")
async def root():
    return FileResponse("index.html")
