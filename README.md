# 🏗️ ConstructAI — 건설사 분석 AI 대시보드

도급순위 50위 건설사를 대상으로 한 경쟁사 분석 플랫폼입니다.
DART 공시, 네이버 뉴스, Claude AI를 연동한 실시간 경쟁 인텔리전스를 제공합니다.

FastAPI(Python) 백엔드 + React(단일 index.html) 프런트엔드 구성으로, **기존 Coolify 설정(포트 8000, Dockerfile 빌드팩)을 그대로 재사용**합니다.

---

## 주요 기능

| 탭 | 데이터 출처 | 성격 |
|---|---|---|
| 대시보드 | 입찰이력 기반 계산 | 위협도 랭킹·즐겨찾기 |
| 경쟁사 프로파일 | 내장 데이터 | 역량 레이더·강점/약점 |
| 입찰 이력 | 사용자 입력(localStorage) | 위협도 자동계산 |
| 파이프라인 | Claude 웹검색 | 발주 예정 프로젝트 발굴 |
| 뉴스 크롤러 | **네이버 뉴스 API** | 실제 기사 + 원문 링크 |
| DART 공시 | **DART 오픈API** | 공시목록·재무 실수치 |
| AI 분석가 | Claude | 대화형 전략 질의응답 |

정확도가 중요한 공시·재무·뉴스는 공식 API에서 원본을, 정형 API가 없는 파이프라인만 AI 웹검색으로 발굴합니다.

---

## 파일 구조

```
├── index.html        # 프런트엔드 (React, 브라우저 Babel 컴파일)
├── main.py           # FastAPI 백엔드 (3개 API + 헬스체크)
├── requirements.txt  # Python 의존성
├── Dockerfile        # 컨테이너 빌드 (포트 8000)
└── .env.example      # 환경변수 템플릿
```

## API 엔드포인트

- `GET  /api/news?company=` → 네이버 뉴스 `{articles}`
- `GET  /api/dart?company=` → DART 공시목록+재무 `{disclosures, financials, summary}`
- `POST /api/messages`      → Anthropic Messages API 패스스루 (파이프라인·챗봇)
- `GET  /api/health`        → 키 설정 상태

브라우저는 외부 API를 직접 호출하지 않습니다. 모든 키는 서버 환경변수에만 존재합니다.

---

## 로컬 실행

```bash
pip install -r requirements.txt
cp .env.example .env      # .env에 4종 키 입력
set -a && . ./.env && set +a
uvicorn main:app --reload --port 8000
# http://localhost:8000
```

`/api/health` 로 키 인식 여부 확인:
`{"status":"ok","keys":{"anthropic":true,"dart":true,"naver":true}}`

---

## Coolify 배포 (기존 설정 + 볼륨 1개 추가)

1. GitHub 레포 내용을 이 폴더로 교체 후 push
2. Coolify → 기존 Application (또는 New → GitHub Repository)
3. Build Pack: **Dockerfile** / Port: **8000**  ← 기존과 동일, 변경 불필요
4. **Persistent Storage(볼륨) 추가** → 마운트 경로 `/data`  ← ★ 입찰이력 보존에 필수
5. Environment Variables 에 4종 키 입력 후 Deploy:
   `DART_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `ANTHROPIC_API_KEY`

> 키를 넣지 않아도 서버·대시보드·위협도 계산은 정상 동작하며, 키가 빠진 탭만 안내 메시지를 표시합니다.

---

## 데이터 저장 (팀 공유)

입찰 이력과 모니터링 경쟁사 목록은 **서버의 SQLite 파일에 저장**되어 팀 10명이 공유합니다. (이전 버전은 각자 브라우저 localStorage라 공유가 안 됐음)

- 저장 위치: `DB_PATH` 환경변수, 기본값 `/data/constructai.db`
- **Coolify에서 `/data` 경로에 퍼시스턴트 볼륨을 반드시 마운트**해야 재배포·재시작에도 데이터가 유지됩니다. 볼륨을 안 붙이면 컨테이너 파일시스템이 배포마다 초기화되어 데이터가 사라집니다.
- 입찰 이력은 **건별 추가/삭제**(POST `/api/bids`, DELETE `/api/bids/{id}`)라 여러 명이 동시에 입력해도 서로 덮어쓰지 않습니다.
- 참고: 다른 사람이 방금 추가한 이력은 **새로고침(재접속) 시** 반영됩니다(실시간 자동 동기화는 아님). 10명 규모 사내 도구엔 충분하지만, 실시간 반영이 필요하면 알려주세요.

저장 관련 API: `GET/POST /api/bids`, `DELETE /api/bids/{id}`, `GET/PUT /api/settings/{key}`

---

## 필요한 API 키

| 키 | 발급처 | 비용 |
|---|---|---|
| `DART_API_KEY` | opendart.fss.or.kr | 무료 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | developers.naver.com | 무료 |
| `ANTHROPIC_API_KEY` | console.anthropic.com | 사용량 과금 |

---

## DART 회사명 매칭 주의

DART는 회사명이 아니라 8자리 고유번호(corp_code)로 조회합니다. 서버가 corpCode.xml 전체를 받아 회사명→고유번호를 자동 해석하는데(첫 요청 시 1회 다운로드 후 캐시), **표시명과 DART 등록명이 다르면** 매칭이 실패할 수 있습니다.

- `main.py` 상단 `DART_NAME_ALIASES` 에 `"표시명": "DART등록명"` 추가로 해결 (틀린 별칭은 잘못된 데이터가 아니라 "못 찾음" 오류 → 안전)
- 또는 `/api/dart?corp_code=00126371` 로 고유번호 직접 지정
- `SGC이앤씨`, `지에스이앤알` 별칭은 추정값 → 실제 안 잡히면 DART에서 정확한 등록명 확인 후 수정

## 참고

- 이전 Python 버전 대비 수정된 점: ① DART 고유번호 조회를 corpCode.xml 방식으로 교체(안정성) ② 재무 기준연도 자동 폴백(하드코딩 2023 제거) ③ 챗봇은 `/api/messages` 패스스루로 통합(모델 문자열은 프런트에서 지정).
- Claude 웹검색(파이프라인)은 검색당 추가 과금. DART·네이버는 무료(쿼터 내).
- 로그인 기능은 없습니다. 사내용이면 Coolify 앞단에 접근 제한 권장.
