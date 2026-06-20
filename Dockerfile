FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY frontend/ ./frontend/

ENV PORT=8081

EXPOSE ${PORT}

CMD gunicorn --bind "0.0.0.0:${PORT}" --workers 2 --timeout 30 src.app:create_app()