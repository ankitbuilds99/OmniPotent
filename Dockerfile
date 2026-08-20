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
# Include postgres extra for production Postgres checkpoint storage, and tasks
# (dramatiq + apscheduler) because the worker runs from this same image with a
# different command — without it the worker container dies on import.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir -e ".[postgres,tasks]"

# Sample fixtures so the container is runnable out of the box.
COPY sample_repo ./sample_repo

# The built SPA — app/api/main.py mounts it at / when this dir exists.
COPY --from=frontend /web/dist ./frontend/dist

# Run as an unprivileged user. /app/data is THE state directory: SQLite DBs,
# graph checkpoints, vectors. It is the volume mount point in compose/k8s (both
# must agree with COPILOT_STATE_DIR below), so it must be writable by that user.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# Pin local state to the mounted volume instead of letting it fall out of the
# CWD: the image filesystem is ephemeral (and read-only under the k8s
# securityContext), so anything written elsewhere is lost on the next restart.
ENV COPILOT_STATE_DIR=/app/data
# Explicit paths for the two SQLite stores whose defaults are CWD-relative.
# Render overrides COPILOT_CHECKPOINT_DB with a Postgres URL (see render.yaml).
ENV COPILOT_CHECKPOINT_DB=/app/data/copilot_checkpoints.sqlite
ENV COPILOT_TENANT_DB=/app/data/copilot_tenants.sqlite

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/healthz || exit 1

# Use PORT env variable for Render compatibility (defaults to 8000)
CMD ["sh", "-c", "uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
