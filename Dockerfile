# WORLDLINE public verifier for Cloud Run (see service/README.md and docs/OPERATIONS.md).
#
# Base images are pinned by digest (multi-arch index digests, resolved 2026-10-27). To refresh:
#   docker buildx imagetools inspect python:3.12-slim   (and node:20-alpine)
# then replace the @sha256 below and rebuild.
#   python:3.12-slim  sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f
#   node:20-alpine    sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293
#   uv                ghcr.io/astral-sh/uv:0.13.5 (build stage only; not in the final image)

# ---- Stage 1: build the viewer with the pipeline's data (experiments/out if present, else ui/fixtures).
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
# copy-data looks for ../experiments/out and falls back to ui/fixtures when the directory
# is absent from the build context instead of failing the build.
COPY experiments/ /src/experiments/
RUN npm run build

# ---- Stage 2: resolve and install Python deps from uv.lock into /opt/venv (no pip involved).
FROM python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f AS deps
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3.12 UV_NO_CACHE=1
WORKDIR /app
# Dependencies first (cached unless pyproject/uv.lock change), then the project itself.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --extra service --no-install-project
COPY worldline/ ./worldline/
# The wheel bundles these as package data (see pyproject force-include); they must exist before the project builds.
COPY scenarios/ ./scenarios/
COPY invariants/ ./invariants/
COPY policies/release.yaml policies/kernel_public.pem ./policies/
RUN uv sync --frozen --no-dev --extra service --no-editable

# ---- Stage 3: runtime. Only the public key and the policy are baked in; the private key is .dockerignored.
FROM python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 PATH=/opt/venv/bin:$PATH \
    WEB_CONCURRENCY=2
WORKDIR /app
COPY --from=deps /opt/venv /opt/venv
COPY policies/release.yaml policies/kernel_public.pem ./policies/
COPY service/ ./service/
COPY --from=ui /src/ui/dist ./ui/dist
ARG GIT_COMMIT=""
ENV GIT_COMMIT=$GIT_COMMIT WORLDLINE_UI_DIST=/app/ui/dist WORLDLINE_POLICY_DIR=/app/policies
# The app tree stays root-owned and read-only for the service user; nothing is written at runtime.
RUN useradd --system --no-create-home --uid 10001 verifier
USER 10001
EXPOSE 8080
# Docker/Compose only: Cloud Run ignores HEALTHCHECK and uses its own probes (docs/OPERATIONS.md).
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:%s/api/readyz' % os.environ.get('PORT','8080'), timeout=2).status == 200 else 1)"]
# `exec` makes uvicorn PID 1, so Cloud Run's SIGTERM reaches it directly: it stops accepting,
# drains in-flight requests for up to 8 s (Cloud Run allows 10 s), then exits.
# WEB_CONCURRENCY=2 workers: ~57 MB RSS idle and ~80 MB peak per worker on a 5 MB bundle, so two
# fit comfortably in 512Mi. Uvicorn's access log is off; service/middleware.py logs JSON instead.
CMD ["sh", "-c", "exec uvicorn service.app:app --host 0.0.0.0 --port \"${PORT}\" --workers \"${WEB_CONCURRENCY}\" --proxy-headers --forwarded-allow-ips='*' --no-access-log --timeout-graceful-shutdown 8 --timeout-keep-alive 5"]
