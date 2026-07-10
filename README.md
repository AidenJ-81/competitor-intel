# Xi C&A 경쟁사 인텔리전스 대시보드

건설사 영업2팀용 경쟁사 분석 대시보드입니다. 도급순위 50개사 데이터, 입찰이력 기반 위협도 자동계산, 그리고 Anthropic API(웹검색 포함)를 활용한 뉴스·DART 공시·파이프라인 수집 및 AI 채팅 기능을 제공합니다.

## 구조

```
xi-cna-dashboard/
├── public/
│   └── index.html      # 대시보드 (React, 브라우저에서 Babel 컴파일)
├── server.js           # Express: 정적 서빙 + Anthropic API 프록시
├── package.json
├── Dockerfile          # Coolify 빌드용
├── .env.example        # 필요한 환경변수
├── .gitignore
└── .dockerignore
```

## 동작 방식

- 브라우저는 Anthropic API를 **직접 호출하지 않습니다.** 모든 AI 요청은 `/api/messages`로 가고, 서버(`server.js`)가 환경변수의 API 키를 붙여 `api.anthropic.com`으로 프록시합니다. → **API 키가 브라우저에 노출되지 않습니다.**
- 입찰이력·모니터링 경쟁사 목록은 브라우저 `localStorage`에 저장됩니다.

## 로컬 실행

```bash
npm install
cp .env.example .env      # .env를 열어 ANTHROPIC_API_KEY 입력
export $(cat .env | xargs) # 또는 dotenv 사용
npm start
# http://localhost:3000
```

## GitHub 등록

```bash
cd xi-cna-dashboard
git init
git add .
git commit -m "Xi C&A 경쟁사 대시보드 초기 커밋"
git branch -M main
git remote add origin https://github.com/<계정>/<레포명>.git
git push -u origin main
```

## Coolify 배포

1. Coolify에서 **New Resource → Application → 해당 GitHub 레포 연결**
2. **Build Pack: `Dockerfile`** 선택 (레포 루트의 Dockerfile 자동 인식)
3. **Environment Variables** 에 추가:
   - `ANTHROPIC_API_KEY` = 발급받은 키
4. **Port: `3000`** (Dockerfile의 EXPOSE와 일치)
5. (선택) Health Check Path: `/health`
6. Deploy

배포 후 대시보드는 접속되지만 **AI 기능(뉴스/DART/파이프라인/채팅)은 `ANTHROPIC_API_KEY`가 설정되어야 작동**합니다. 키 설정 여부는 `https://<도메인>/health` 의 `keyConfigured` 값으로 확인할 수 있습니다.

## 모델 변경

`public/index.html` 상단의 `const MODEL="claude-sonnet-4-6";` 한 줄만 바꾸면 됩니다. 모델을 못 찾는다는 오류가 나면 이 값을 현재 유효한 모델 문자열(예: `claude-sonnet-5`)로 교체하세요. 유효 모델 목록은 https://docs.claude.com 에서 확인할 수 있습니다.

## 주의

- 웹검색 도구(`web_search_20250305`)는 API 요청당 추가 과금이 발생할 수 있습니다.
- 이 대시보드는 인증(로그인) 기능이 없습니다. 사내 전용이라면 Coolify 앞단에 접근 제한(Basic Auth, VPN, IP 화이트리스트 등)을 두는 것을 권장합니다.
