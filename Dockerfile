FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data

# DB_PATH is provided via the environment (Railway variable / .env). For
# persistence across redeploys, attach a Railway Volume and point DB_PATH at it.
# Note: Railway does not support the Dockerfile VOLUME instruction.

CMD ["python3", "bot.py"]
