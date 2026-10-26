FROM node:22-bookworm-slim AS frontend
WORKDIR /build
RUN corepack enable && corepack prepare pnpm@11.25.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.12-slim
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /build/dist /app/frontend
RUN groupadd --gid 10001 diamond && useradd --uid 10001 --gid diamond --no-create-home diamond && mkdir -p /data && chown diamond:diamond /data
ENV MLB_PUBLIC_DEMO=1 MLB_START_WORKER=0 MLB_FRONTEND_DIR=/app/frontend DATABASE_URL=sqlite:////data/mlb.db PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER diamond
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)"
CMD ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","1"]
