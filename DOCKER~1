FROM python:3.11-slim

WORKDIR /app

# 의존성 설치 (캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 복사
COPY . .

EXPOSE 8000

# 0.0.0.0 바인딩 필수 (Coolify 컨테이너 환경)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
