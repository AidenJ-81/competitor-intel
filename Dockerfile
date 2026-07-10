FROM node:20-alpine

WORKDIR /app

# 패키지 설치
COPY package*.json ./
RUN npm install --omit=dev

# 앱 소스 복사
COPY server.js ./
COPY index.html ./

ENV PORT=3000
EXPOSE 3000

CMD ["node", "server.js"]
