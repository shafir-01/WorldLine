# WORLDLINE public verifier for Cloud Run (see service/README.md).
#
# Stage 1: build the viewer with the pipeline's data (experiments/out if present, else ui/fixtures).
FROM node:20-alpine AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
# copy-data looks for ../experiments/out and falls back to ui/fixtures when the directory
# is absent from the build context instead of failing the build.
COPY experiments/ /src/experiments/
RUN npm run build

# Stage 2: runtime. Only the public key and the policy are baked in; the private key is .dockerignored.
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY worldline/ ./worldline/
COPY policies/release.yaml policies/kernel_public.pem ./policies/
COPY service/ ./service/
RUN pip install --no-cache-dir ".[service]" && rm -rf /root/.cache
COPY --from=ui /src/ui/dist ./ui/dist
ARG GIT_COMMIT=""
ENV GIT_COMMIT=$GIT_COMMIT WORLDLINE_UI_DIST=/app/ui/dist WORLDLINE_POLICY_DIR=/app/policies
RUN useradd --system --uid 10001 verifier && chown -R verifier /app
USER verifier
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn service.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=*"]
