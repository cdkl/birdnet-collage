FROM python:3.12-slim

RUN apt-get update && apt-get install -y fonts-dejavu-core fonts-liberation2 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY frontend/ ./frontend/

EXPOSE 8081

CMD ["gunicorn", "--bind", "0.0.0.0:8081", "--workers", "2", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-", "src.app:create_app()"]