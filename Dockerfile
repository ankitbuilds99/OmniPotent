# --- DevOps Copilot — production image (frontend + backend in one) --------- #

# Stage 1: build the React/Vite SPA into static assets.
FROM node:20-slim AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Empty base URL => the SPA calls the API with relative (same-origin) URLs,
# which FastAPI serves itself — so no CORS or separate host is needed.
ENV VITE_API_URL=""
# Note: Supabase config is injected at runtime via window.__ENV__ pattern
# since Docker build args aren't available during Render builds.
RUN npm run build   # -> /web/dist

# Stage 2: Python backend that also serves the built SPA.
FROM python:3.12-slim AS runtime

# git: repo MCP server's git_log tool. curl: container HEALTHCHECK.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for better layer caching. Editable install keeps the package
# rooted at /app so config.ROOT resolves sample_repo/ and frontend/dist/ correctly.
# postgres  -> Postgres checkpoint + asyncpg driver for production.
# migrations -> alembic, which deploy/kubernetes/base/migration-job.yaml runs from
#               THIS image (`alembic upgrade head`); without the extra and the two
#               COPYs below that Job dies with "alembic: not found".
COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
COPY migrations ./migrations
RUN pip install --no-cache-dir -e ".[postgres,migrations]"

# Sample fixtures so the container is runnable out of the box.
COPY sample_repo ./sample_repo

# The built SPA — app/api/main.py mounts it at / when this dir exists.
COPY --from=frontend /web/dist ./frontend/dist

# Run as an unprivileged user. /data holds the SQLite checkpoint DB (a volume in
# compose); it must be writable by that user, so chown it in the image.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

# Default to SQLite for local dev; Render sets COPILOT_CHECKPOINT_DB to Postgres URL
ENV COPILOT_CHECKPOINT_DB=/data/copilot_checkpoints.sqlite

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/healthz || exit 1

# Use PORT env variable for Render compatibility (defaults to 8000)
CMD ["sh", "-c", "uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
