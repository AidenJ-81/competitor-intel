# 🏗️ Xi C&A 경쟁사 인텔리전스 — 웍스AI 연동판

Claude Artifact에서 만든 대시보드를 **사내 서버(Coolify) 자체 호스팅**으로 옮긴 버전입니다.

- AI 기능(파이프라인·챗봇·RFP 위험도 분석·뉴스 AI 요약) → **웍스AI**
- 뉴스·DART → **네이버 오픈API / DART 오픈API** (기존과 동일, 실시간 조회)
- 팀 공유 데이터(입찰이력·설정) → **서버 SQLite**

---

## Artifact판에서 무엇이 바뀌었나

Artifact 전용 API 두 개를 **동일한 인터페이스의 대체 객체**로 교체했습니다. 화면·계산 로직·컴포넌트 코드는 그대로입니다.

| Artifact | 교체 대상 | 연결 |
|---|---|---|
| `claude.use("db")` | `SERVER_DB` | FastAPI + SQLite |
| `claude.use("sample")` | `serverSample` | 웍스AI `/v2/chat/json` |

`SERVER_DB`는 Firestore 스타일 호출(`doc().get()/set()/update()/delete()`, `collection().onSnapshot()`)을 그대로 받아 내부에서 REST로 변환합니다. 덕분에 15곳의 호출부를 수정하지 않았습니다.

**뉴스·DART는 스냅샷이 아니라 실시간 API로 바뀌었습니다.** Artifact판은 담당자가 저장해 둔 JSON을 읽었지만, 여기서는 `news/{id}` → `/api/news`, `dart/{id}` → `/api/dart` 로 라우팅되어 네이버·DART를 직접 조회합니다. (`db_seed/` 의 JSON은 참고용 백업으로만 보관)

**실시간 구독은 폴링으로 대체**했습니다. `onSnapshot`이 20초 주기로 서버를 다시 읽습니다(`BID_POLL_MS`). Artifact의 즉시 반영만큼은 아니지만 새로고침은 필요 없습니다.

---

## AI 호출 경로

프런트엔드는 여전히 Anthropic Messages 형식으로 `/api/messages`를 호출합니다. **서버가 이를 웍스AI 형식으로 번역**하므로 프런트 수정이 없습니다.

```
프런트 {system, messages[], tools[]}
  → 서버가 단일 문자열로 평탄화 + 웹검색 도구 on/off 결정
  → POST {WRKS_BASE_URL}/v2/chat/json  {message, agentId, ...}
  → data.parts[] 에서 text 만 수집
  → 프런트에 {content:[{type:"text",text}]} 로 반환
```

- **웹검색**: 요청에 `web_search` 툴이 있으면 `enabledInternalTools: ["wrks__search_web"]`, 없으면 꺼서 불필요한 검색·지연을 막습니다.
- **무상태 호출**: `chatId`를 쓰지 않고 대화 맥락을 `message`에 담습니다. 서버에 대화가 쌓이지 않고 기존 동작과 동일합니다.
- **RFP 분석 / 뉴스 요약**: `POST /api/ai` (`json:true`면 코드펜스·머리말을 제거하고 JSON 객체만 추출)

---

## 배포 (Coolify)

기존 설정 그대로입니다. Build Pack **Dockerfile**, Port **8000**, 볼륨 **`/data`**.

환경변수:

| 변수 | 필수 | 비고 |
|---|---|---|
| `WRKS_API_KEY` | ✅ | 공용 API 키 |
| `WRKS_AGENT_ID` | ✅ | 아래 방법으로 확인 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | ✅ | 기존과 동일 |
| `DART_API_KEY` | ✅ | 기존과 동일 |
| `WRKS_KEY_EXPIRES` | 권장 | 기본 `2027-09-09` |
| `AI_PROVIDER` | 선택 | `wrks`(기본) / `anthropic` / `auto` |
| `WRKS_ACTOR_EMAIL` | 선택 | **System API Key일 때만** 동작 |

### 에이전트 ID 확인 방법

`WRKS_AGENT_ID`를 비워둔 채 먼저 배포한 뒤, 브라우저에서 **`/api/wrks/agents`** 를 엽니다. 사용 가능한 에이전트 목록이 JSON으로 나옵니다. API 키는 서버에만 있으므로 노출되지 않습니다.

```json
{"result":"ok","data":[{"id":12,"name":"공용 에이전트"}]}
```

여기서 나온 `id`를 `WRKS_AGENT_ID`에 넣고 재배포하면 됩니다.

`/api/wrks/agents/{id}` 로는 그 에이전트에 붙은 MCP 도구의 OAuth 연결 상태를 볼 수 있습니다. **`oauth.required=true`인데 `connected=false`인 도구는 대화에서 조용히 제외되고, 응답에는 그 사실이 드러나지 않습니다.** 도구가 빠진 채로 그럴듯한 답변이 나오므로, 파이프라인 결과가 이상하면 여기부터 확인하세요.

---

## API 키 만료 대응

공용 API 키는 **2027-09-09에 만료**됩니다. 만료되면 AI 기능이 멈춥니다(뉴스·DART·입찰이력은 계속 동작).

- `/api/health` 의 `wrks_key_days_left` 로 남은 일수를 확인할 수 있습니다.
- 만료 **45일 전부터** `warning` 필드에 안내 문구가 생깁니다 (`KEY_WARN_DAYS`로 조정).
- 만료 후 AI 호출은 에러로 죽지 않고, 화면에 "API 키가 만료되었습니다" 안내가 표시됩니다.
- `AI_PROVIDER=auto` + `ANTHROPIC_API_KEY` 설정 시, 웍스 장애·만료 시 Anthropic으로 자동 폴백합니다.

> 갱신 알림을 캘린더에 등록해 두세요. 관리 화면을 보면 이미 만료된 키(`수주레이더`, 2026-08-06)가 있어 실제로 발생하는 일입니다.

---

## 로컬 실행

```bash
pip install -r requirements.txt
cp .env.example .env    # 키 입력
set -a && . ./.env && set +a
DB_PATH=./local.db uvicorn main:app --reload --port 8000
```

---

## 알려진 이슈

- **인증 없음**: `/api/messages`, `/api/ai`, `/api/bids` DELETE 등이 무인증 공개 상태입니다. Coolify 앞단 Basic Auth 또는 IP 제한을 권장합니다.
- **`rank` 중복**: `KCC건설`과 `쌍용건설`이 둘 다 19위로 등록되어 있습니다(표시용, 계산 무관).
- **저장 실패가 조용함**: 프런트가 `catch{}`로 실패를 삼켜, 서버 저장이 실패해도 화면상 저장된 것처럼 보입니다.
- **React 개발 빌드 + 브라우저 Babel**: 초기 로딩이 느리고, 사내망에서 cdnjs가 차단되면 백지 화면이 됩니다.
