FROM node:20-alpine

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY package*.json ./
RUN npm install --omit=dev

# 앱 소스 복사
COPY server.js ./
COPY public ./public

ENV PORT=3000
EXPOSE 3000

CMD ["node", "server.js"]
