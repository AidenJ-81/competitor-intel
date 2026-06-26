# 🏗️ ConstructAI — 건설사 분석 AI 대시보드

도급순위 50위 건설사를 대상으로 한 경쟁사 분석 플랫폼입니다.  
DART 공시 데이터, 네이버 뉴스, Claude AI를 연동하여 실시간 경쟁 인텔리전스를 제공합니다.

---

## 주요 기능

| 탭 | 기능 |
|---|---|
| 대시보드 | 위협도 랭킹, 공종별 경쟁 강도, 즐겨찾기 |
| 경쟁사 프로파일 | 역량 레이더 차트, 강점/약점 분석 |
| 파이프라인 경합 | 수주가능성 %, 경쟁사 겹침 현황 |
| 뉴스 크롤러 | 네이버 API 기반 실시간 뉴스 수집 |
| DART 공시 | 재무제표, 공시 목록 (금융감독원 API) |
| AI 분석가 | Claude 기반 경쟁사 전략 질의응답 |

---

## 기술 스택

- **Backend:** Python, FastAPI, uvicorn
- **Frontend:** HTML/CSS/JS, Chart.js
- **APIs:** DART Open API, 네이버 검색 API, Anthropic Claude API
- **배포:** Docker, Coolify

---

## 파일 구조

```
├── index.html        # 프론트엔드 (단일 파일)
├── main.py           # FastAPI 백엔드
├── requirements.txt  # Python 의존성
├── Dockerfile        # 컨테이너 빌드
└── .env.example      # 환경변수 템플릿
```

---

## 로컬 실행

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 환경변수 설정
cp .env.example .env
# .env 파일에 실제 API 키 입력

# 3. 서버 실행
uvicorn main:app --reload

# 브라우저에서 http://localhost:8000 접속
```

---

## Coolify 배포

1. 이 레포지토리를 GitHub에 push
2. Coolify → New Resource → GitHub Repository
3. Build Pack: **Dockerfile** / Port: **8000**
4. Environment Variables에 아래 키 입력 후 Deploy

---

## 필요한 API 키

| 키 | 발급처 | 비용 |
|---|---|---|
| `DART_API_KEY` | [opendart.fss.or.kr](https://opendart.fss.or.kr) | 무료 |
| `NAVER_CLIENT_ID` | [developers.naver.com](https://developers.naver.com) | 무료 |
| `NAVER_CLIENT_SECRET` | 위와 동일 | 무료 |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | 사용량 과금 |
