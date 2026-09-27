# syntax=docker/dockerfile:1
# Exact multi-architecture manifests verified against Docker Hub 2026-09-26.
FROM node:24.21.0-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26 AS python
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.3
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26 AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    COUNTERPART_HOST=0.0.0.0 COUNTERPART_MEDIA_DIR=/data/media COUNTERPART_WEB_DIR=/app/web/dist TMPDIR=/data/scratch
RUN groupadd --gid 10001 counterpart && useradd --uid 10001 --gid 10001 --create-home counterpart \
    && mkdir -p /app/web /data/media /data/scratch && chown -R counterpart:counterpart /app /data
WORKDIR /app
COPY --from=python --chown=10001:10001 /app/.venv /app/.venv
COPY --from=web --chown=10001:10001 /build/web/dist /app/web/dist
USER 10001:10001
EXPOSE 8080
CMD ["counterpart", "serve"]
