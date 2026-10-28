# The whole app in one image: the API, the background workers and the dashboard it serves
# (ADR-0012). Build from the repository root:
#
#   docker build -t agentiq .
#   docker run --env-file .env -p 8000:8000 agentiq
#
# Render builds this file from render.yaml; see docs/deployment.md.

# --- 1. The dashboard -----------------------------------------------------------------
FROM node:22-alpine AS dashboard
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Empty: the dashboard calls the API on its own origin.
ENV VITE_API_URL=""
RUN npm run build

# --- 2. The API -----------------------------------------------------------------------
FROM python:3.12-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home --uid 10001 agentiq
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY --chown=agentiq backend/ ./
COPY --from=dashboard --chown=agentiq /app/frontend/dist /app/frontend/dist
# The knowledge index and downloaded models live in data/.
RUN mkdir -p data && chown agentiq . data
USER agentiq
# lexicon: no sentiment model (a 135 MB smaller image and less memory; ADR-0006 fallback).
ARG SENTIMENT_PROVIDER=model
ENV SENTIMENT_PROVIDER=${SENTIMENT_PROVIDER}
# The embedding and sentiment models go into the image, so a cold start never downloads.
RUN python -m app.cli prefetch-models
ENV ENVIRONMENT=production \
    LOG_FORMAT=json \
    FRONTEND_DIST=frontend/dist \
    PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/v1/health/live', timeout=4)"
CMD ["sh", "docker/start.sh"]
