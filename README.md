# 🏗️ ConstructAI — 건설사 분석 AI 대시보드 (Xi C&A 경쟁사 인텔리전스)

도급순위 50위 건설사를 대상으로 한 경쟁사 분석 플랫폼입니다.
DART 공시, 네이버 뉴스, Claude AI를 연동한 실시간 경쟁 인텔리전스를 제공합니다.

FastAPI(Python) 백엔드 + React(단일 index.html) 프런트엔드 구성으로, **기존 Coolify 설정(포트 8000, Dockerfile 빌드팩)을 그대로 재사용**합니다.

---

## 주요 기능

| 탭 | 데이터 출처 | 성격 |
|---|---|---|
| 대시보드 | 입찰이력 기반 계산 | 위협도 랭킹·산출근거 패널 |
| 경쟁사 프로파일 | 내장 데이터 | 역량 레이더·강점/약점·출처 링크 |
| 입찰 이력 | 사용자 입력(서버 SQLite) | 위협도 자동계산 |
| 파이프라인 | Claude 웹검색 | 발주 예정 프로젝트 발굴 |
| 뉴스 크롤러 | **네이버 뉴스 API** | 실제 기사 + 원문 링크 |
| DART 공시 | **DART 오픈API** | 공시목록·재무 실수치 |
| AI 분석가 | Claude | 대화형 전략 질의응답 |

정확도가 중요한 공시·재무·뉴스는 공식 API에서 원본을, 정형 API가 없는 파이프라인만 AI 웹검색으로 발굴합니다.

---

## 파일 구조

```
├── index.html        # 프런트엔드 (React, 브라우저 Babel 컴파일)
├── main.py           # FastAPI 백엔드 (API + 헬스체크)
├── requirements.txt  # Python 의존성
├── Dockerfile        # 컨테이너 빌드 (포트 8000)
├── .dockerignore     # .env·DB가 이미지에 들어가는 것 방지
├── .gitignore        # .env·DB가 깃에 올라가는 것 방지
└── .env.example      # 환경변수 템플릿
```

## API 엔드포인트

- `GET  /api/news?company=` → 네이버 뉴스 `{articles}`
- `GET  /api/dart?company=` → DART 공시목록+재무 `{disclosures, financials, summary}`
- `POST /api/messages`      → Anthropic Messages API 패스스루 (파이프라인·챗봇)
- `GET/POST /api/bids`, `DELETE /api/bids/{id}` → 입찰 이력 (팀 공유)
- `GET/PUT /api/settings/{key}` → 설정 (모니터링 경쟁사 목록 등)
- `GET  /api/health`        → 키 설정 상태

브라우저는 외부 API를 직접 호출하지 않습니다. 모든 키는 서버 환경변수에만 존재합니다.

---

## 로컬 실행

```bash
pip install -r requirements.txt
cp .env.example .env      # .env에 4종 키 입력
set -a && . ./.env && set +a
DB_PATH=./constructai.db uvicorn main:app --reload --port 8000
# http://localhost:8000
```

`/api/health` 로 키 인식 여부 확인:
`{"status":"ok","keys":{"anthropic":true,"dart":true,"naver":true}}`

---

## Coolify 배포 (기존 설정 그대로)

1. GitHub 레포 내용을 이 폴더로 교체 후 push
2. Coolify → 기존 Application (또는 New → GitHub Repository)
3. Build Pack: **Dockerfile** / Port: **8000**  ← 기존과 동일, 변경 불필요
4. **Persistent Storage(볼륨)** → 마운트 경로 `/data`  ← ★ 입찰이력 보존에 필수
5. Environment Variables 에 4종 키 입력 후 Deploy:
   `DART_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `ANTHROPIC_API_KEY`

> 백엔드·API 스펙이 이전 버전과 동일하므로, 이미 배포된 앱이라면 **push만 하면 재배포됩니다.** 볼륨·환경변수를 다시 설정할 필요가 없고, 기존에 입력한 입찰 이력도 그대로 유지됩니다.

> 키를 넣지 않아도 서버·대시보드·위협도 계산은 정상 동작하며, 키가 빠진 탭만 안내 메시지를 표시합니다.

---

## 데이터 저장 (팀 공유)

입찰 이력과 모니터링 경쟁사 목록은 **서버의 SQLite 파일에 저장**되어 팀 전체가 공유합니다.

- 저장 위치: `DB_PATH` 환경변수, 기본값 `/data/constructai.db`
- **Coolify에서 `/data` 경로에 퍼시스턴트 볼륨을 반드시 마운트**해야 재배포·재시작에도 데이터가 유지됩니다. 볼륨을 안 붙이면 컨테이너 파일시스템이 배포마다 초기화되어 데이터가 사라집니다.
- 입찰 이력은 **건별 추가/삭제**(POST `/api/bids`, DELETE `/api/bids/{id}`)라 여러 명이 동시에 입력해도 서로 덮어쓰지 않습니다.
- 참고: 다른 사람이 방금 추가한 이력은 **새로고침(재접속) 시** 반영됩니다(실시간 자동 동기화는 아님).

---

## 위협도 산출 방식 (100점 만점)

| 항목 | 배점 | 내용 |
|---|---|---|
| 경합 빈도 | 30 | 해당사 참여 건수 / 전체 입찰 건수 |
| 낙찰률 | 35 | 해당사 낙찰 / 참여 (베이지안 보정 적용) |
| 수주금액 | 20 | 해당사 낙찰금액 합 / 전체 낙찰금액 합 |
| 공종 겹침 | 15 | Xi C&A 주력 공종(GMP·반도체·DC·이차전지·산업플랜트) 경합 비율 |

두 가지 보정이 들어갑니다.

- **최신성 가중치(반감기 18개월)**: 오래된 입찰일수록 영향을 줄입니다. `가중치 = 0.5 ^ (경과월수 / 18)`
- **베이지안 스무딩(K=3)**: 경합 건수가 적은 경쟁사의 낙찰률이 0%/100% 극단값으로 튀는 것을 막기 위해, 가상 사례 3건을 전체 평균 낙찰률로 채웁니다. 표본이 쌓일수록 실제 값에 수렴합니다.

각 경쟁사에는 표본 크기 기준 신뢰도 배지가 붙습니다 — 경합 7건 이상 `표본 충분`, 3건 이상 `표본 보통`, 3건 미만 `표본 적음·보정됨`.

---

## 필요한 API 키

| 키 | 발급처 | 비용 |
|---|---|---|
| `DART_API_KEY` | opendart.fss.or.kr | 무료 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | developers.naver.com | 무료 |
| `ANTHROPIC_API_KEY` | console.anthropic.com | 사용량 과금 |

---

## DART 회사명 매칭 주의

DART는 회사명이 아니라 8자리 고유번호(corp_code)로 조회합니다. 서버가 corpCode.xml 전체를 받아 회사명→고유번호를 자동 해석합니다(첫 요청 시 1회 다운로드 후 캐시).

- `main.py` 상단 `DART_NAME_ALIASES` 에 `"표시명": "DART등록명"` 추가로 해결
- 또는 `/api/dart?corp_code=00126371` 로 고유번호 직접 지정
- 이름이 정확히 안 맞으면 부분일치로 폴백하는데, **동명·계열사가 잡혀 엉뚱한 회사 재무가 표시될 수 있습니다.** 숫자가 이상하면 DART에서 등록명을 확인하고 별칭을 추가하세요.

---

## 알려진 이슈 / TODO

- **`rank` 중복**: `KCC건설`과 `쌍용건설`이 둘 다 `rank:19`로 등록되어 있습니다(2026 시평 갱신 시 쌍용건설만 반영된 것으로 보임). 화면 표시(`#19`)만 영향받고 계산에는 무관하지만, 정확한 순위 확인 후 수정 필요.
- **인증 없음**: `/api/messages`(Anthropic 키 사용), `/api/bids` DELETE, `/api/settings` PUT이 무인증 공개 상태입니다. 사내용이라도 Coolify 앞단에 Basic Auth 또는 IP 제한을 거는 것을 권장합니다. Anthropic 콘솔에서 이 앱 전용 워크스페이스를 만들고 월 지출 한도를 설정해 두면 리스크가 격리됩니다.
- **저장 실패가 조용함**: 프런트에서 `fetch` 실패를 `catch{}`로 무시하므로, 서버 저장이 실패해도 화면에는 저장된 것처럼 보입니다.
- **React 개발 빌드**: CDN의 `react.development.js` + 브라우저 Babel 컴파일이라 초기 로딩이 느립니다. 사내망에서 cdnjs가 막히면 백지 화면이 됩니다.

## 참고

- Claude 웹검색(파이프라인)은 검색당 추가 과금. DART·네이버는 무료(쿼터 내).
- 프런트엔드가 모델명(`claude-sonnet-4-6`)과 `max_tokens`를 지정합니다.
