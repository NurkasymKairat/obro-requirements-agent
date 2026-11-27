# Веб-интерфейс агента: API (FastAPI) + собранный фронт.
# Сборка из корня репозитория: docker build -f deploy/Dockerfile .

# ---- 1. Фронт ----
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- 2. Приложение ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CACHE_DIR=/data/cache
WORKDIR /app

COPY requirements.txt requirements-web.txt ./
RUN pip install -r requirements-web.txt

# только то, что нужно во время работы: ядро агента, промпты, API, собранный фронт
COPY agent/ agent/
COPY prompts/ prompts/
COPY api/ api/
COPY --from=web /web/dist web/dist

# непривилегированный пользователь; каталог кеша ответов модели — под именованный том
RUN useradd --uid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data/cache \
 && chown -R app:app /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
