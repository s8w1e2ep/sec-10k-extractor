# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# lxml has manylinux wheels for both amd64 and arm64 — we don't need libxml2-dev
# build deps. Install ca-certificates so httpx can verify SEC TLS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml ./
COPY extractor ./extractor
COPY server ./server

# Cache dir for fetched filings — bound to /app/cache by default. Created on
# first fetch by extractor/fetcher.py. Survives container restarts via Zeabur
# volume mount if attached; otherwise warmed on first request per cold start.
ENV CACHE_DIR=/app/cache

# Non-root for safety. The image only writes to /app/cache (the fetcher
# creates it lazily). Owning /app to `app` keeps uvicorn from fighting
# permissions.
RUN useradd --create-home --shell /bin/bash app \
 && chown -R app:app /app
USER app

# Honour $PORT if the platform injects one (Zeabur-style); fall back to 8000.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
