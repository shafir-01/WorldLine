# WORLDLINE public verifier (`service/`)

Live deployment: https://worldline-verifier-301330323690.europe-north1.run.app

A FastAPI service, deployed to Google Cloud Run, that does two things:

1. Serves the built [viewer](../ui/README.md) at `/` together with the pipeline's output under `data/` (the same files GitLab Pages publishes).
2. Exposes the evidence kernel's **verification** half as a public HTTP API: anyone can POST a `bundle.json` and get back whether it is intact and kernel-signed, and whether it would pass the release policy.

The image contains **only the kernel's public key** (`policies/kernel_public.pem`) and the release policy (`policies/release.yaml`). It cannot seal, attest or authorize anything on its own; `policies/kernel_private.pem` is excluded by `.dockerignore` and deleted again in the CI job before the source upload.

## Why a public verifier

WORLDLINE's claim is that a release is gated on evidence the kernel recomputed and signed, not on an agent's say-so. That claim is only worth something if someone *outside* the pipeline can check it. With this service:

- A reviewer takes the `bundle.json` attached to a release and POSTs it to `/api/verify`. The hash chain and the Ed25519 checkpoint are re-checked against the public key. An edited, dropped, reordered or truncated record shows up as a finding (`RECORD_EDITED`, `RECORD_DROPPED`, `RECORDS_REORDERED`, `TRUNCATED`, ...); a bundle signed with another key is `UNKNOWN_SIGNER`.
- The reviewer then POSTs the same bundle to `/api/authorize?artifact_digest=<commit>` and sees exactly the `AUTHORIZED` / `DENIED` decision the CI gate produced, with every reason, satisfied requirement, stale and downgraded record. No CI access, no trust in the runner, no private key.
- `/api/evidence` returns the bundle and authorization that this very deployment was built from, so the viewer's Evidence tab and the API agree by construction.

The service is deliberately read-only and stateless; it is safe to leave unauthenticated.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | The viewer (`ui/dist`), with `data/*.json` next to it. |
| `GET` | `/api/healthz` | Liveness: `{status, public_key, data, viewer}`. |
| `GET` | `/api/readyz` | Readiness: `200 {status: "ready", checks}` when the public key parses, the release policy loads and the data dir has `index.json`; `503 {status: "unavailable", checks}` otherwise. |
| `GET` | `/api/version` | Git commit, kernel public key id, policy name, limits. |
| `POST` | `/api/verify` | Body: bundle JSON. Returns `ok`, `findings[]`, `root`, `signer_key_id`, `verifier_key_id` and a per-record summary (`index`, `kind`, `producer`, `claim`). |
| `POST` | `/api/authorize?artifact_digest=...[&policy=release][&now=ISO]` | Body: bundle JSON. Returns the kernel `Authorization` (`decision`, `reasons`, `satisfied`, `missing`, `stale`, `downgraded`, `policy`). `now` evaluates at a given time; by default the current time, so evidence older than the policy's `max_age_s` (24 h) is stale. |
| `POST`/`GET` | `/api/verify/release?tag=v0.1.3-transcend[&artifact_digest=...][&policy=release][&now=ISO]` | Fetches `bundle.json` for the tag from the GitLab generic package registry (`worldline-evidence/<tag>/`), then returns `{tag, package_url, bundle_url, artifact_digest, artifact_digest_source, evaluated_at, verify, authorize}`, where `verify` and `authorize` have exactly the `/api/verify` and `/api/authorize` shapes. The commit comes from the GitLab release (`commit.id`, else the `Commit:` line of the notes), else from `index.json` in the same package. The gate is evaluated at the bundle's seal time unless `now` is given. Only `https://gitlab.com` is fetched, 10 s deadline, cached 5 min per tag. |
| `GET` | `/api/evidence` | `{source, bundle, authorization}` from the data baked into the image, with an `ETag` (`If-None-Match` gives `304`). |
| `GET` | `/api/docs` | Swagger UI, with examples for every endpoint. |
| `GET` | `/api/openapi.json` | The OpenAPI document. |

Rules: JSON only (`415` otherwise), bodies capped at **5 MB** (`413`) and **64 levels** of nesting (`400`, checked before parsing), malformed bundles `400`, errors as `{"error": "..."}`. A body must arrive within 15 s (`408`) and verification must finish within 20 s (`503`). `POST /api/*` and `GET /api/verify/release` are rate limited per client IP with a token bucket (default 30 a minute, `WORLDLINE_RATE_LIMIT_PER_MIN`), and an empty bucket gives `429` with `Retry-After`. CORS allows `GET`/`POST` from any origin on `/api/*` only; it is a public verifier, while the viewer stays same-origin.

Every response carries:

- `X-Request-ID`: yours if you sent a sane one, else a generated one. It is also in the log line.
- `Content-Security-Policy`: scripts from self only, styles and fonts from self plus Google Fonts, `connect-src 'self'`, `frame-ancestors 'none'`. `/api/docs` alone also allows jsDelivr for Swagger UI.
- `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, a restrictive `Permissions-Policy`, `X-Frame-Options: DENY`, and `Strict-Transport-Security` when the request came over https.
- `Cache-Control`: `immutable` for the hashed `/assets/*`, `max-age=60` for `/data/*.json`, `no-cache` for `/` and `/api/evidence` (revalidate with the ETag), `no-store` for the rest of `/api/*`.

Logs are one JSON object per line on stdout in Cloud Logging's shape (`severity`, `httpRequest`, plus `request_id`, `method`, `path`, `status`, `latency_ms`, `client_ip`). Bodies and query strings are never logged. Uvicorn's own access log is turned off.

### curl examples

```bash
BASE=https://worldline-verifier-<hash>-lz.a.run.app   # or http://127.0.0.1:8081 locally

curl -s $BASE/api/version
# {"service":"worldline-verifier","git_commit":"...","kernel_public_key_id":"ed25519:...","policy":"release",...}

# Verify a bundle you obtained from a release (or from this deployment)
curl -s $BASE/api/evidence | python -c "import json,sys; json.dump(json.load(sys.stdin)['bundle'], open('bundle.json','w'))"
curl -s -X POST $BASE/api/verify -H 'content-type: application/json' --data-binary @bundle.json
# {"ok":true,"findings":[],"root":"sha256:...","signer_key_id":"ed25519:...","n_records":31,"records":[...]}

# Edit one byte of a record and verify again
python -c "import json; d=json.load(open('bundle.json')); d['records'][0]['result']['x']=1; json.dump(d, open('tampered.json','w'))"
curl -s -X POST $BASE/api/verify -H 'content-type: application/json' --data-binary @tampered.json
# {"ok":false,"findings":[{"code":"RECORD_EDITED","index":0,...},{"code":"ROOT_MISMATCH",...}],...}

# Release gate for a commit
curl -s -X POST "$BASE/api/authorize?artifact_digest=$(git rev-parse HEAD)" -H 'content-type: application/json' --data-binary @bundle.json
# {"decision":"AUTHORIZED","artifact_digest":"...","reasons":["all 5 requirement(s) met by 8 record(s)"],...}

# Same bundle, wrong artifact -> DENIED with subject_mismatch reasons
curl -s -X POST "$BASE/api/authorize?artifact_digest=deadbeef" -H 'content-type: application/json' --data-binary @bundle.json

# A published release, straight from the GitLab package registry (no download on your side)
curl -s "$BASE/api/verify/release?tag=v0.1.3-transcend"
# {"tag":"v0.1.3-transcend","artifact_digest":"1bfce462...","artifact_digest_source":"release.commit",
#  "verify":{"ok":true,"findings":[],...},"authorize":{"decision":"AUTHORIZED",...}}

# Conditional fetch of the baked-in evidence
ETAG=$(curl -sI $BASE/api/evidence | grep -i '^etag' | cut -d' ' -f2 | tr -d '\r')
curl -s -o /dev/null -w '%{http_code}\n' -H "If-None-Match: $ETAG" $BASE/api/evidence   # 304
```

## Run locally

```bash
uv sync --extra dev --extra service
cd ui && npm ci && npm run build && cd ..            # optional: without ui/dist only the API is served
uv run uvicorn service.app:app --port 8081 --reload --no-access-log
# http://127.0.0.1:8081/            viewer
# http://127.0.0.1:8081/api/docs    OpenAPI (Swagger UI); /api/openapi.json for the document
uv run pytest tests/test_service.py -q
```

Data resolution: `WORLDLINE_DATA_DIR` if set, else `ui/dist/data`, else `experiments/out`, else `ui/fixtures`. Viewer: `WORLDLINE_UI_DIST` or `ui/dist`. Policy and public key: `WORLDLINE_POLICY_DIR` or `policies/`. `GIT_COMMIT` (or `CI_COMMIT_SHA`) overrides the commit reported by `/api/version`. Every other setting (rate limit, trusted proxy hops, log level, release source and host allowlist) is listed in [docs/OPERATIONS.md](../docs/OPERATIONS.md#1-what-is-running).

## Container

`Dockerfile` at the repo root has three stages, and both base images are pinned by digest:

1. `node:20-alpine` builds `ui/`. Its `copy-data` step takes `experiments/out` when present in the build context, else `ui/fixtures`.
2. `python:3.12-slim` with `uv` runs `uv sync --frozen --extra service` from `uv.lock` into `/opt/venv`. No pip is involved, and uv stays out of the final image.
3. A clean `python:3.12-slim` gets the venv, `service/`, `ui/dist`, the policy and the public key. As uid 10001 it runs `exec uvicorn --workers $WEB_CONCURRENCY` (default 2, which fits in 512 MiB at about 57 MB idle and 80 MB peak per worker), with `--no-access-log`, `--timeout-graceful-shutdown 8` for Cloud Run's `SIGTERM`, and a `HEALTHCHECK` on `/api/readyz`.

The image is roughly 200 MB uncompressed and about 70 MB compressed, estimated from the layers. See [docs/OPERATIONS.md](../docs/OPERATIONS.md) for the runbook: deploy, roll back, key rotation, logs, alerts and cost.

```bash
docker build -t worldline-verifier --build-arg GIT_COMMIT=$(git rev-parse HEAD) .
docker run --rm -p 8080:8080 worldline-verifier
```

## Deploy to Cloud Run

One-off from a workstation (source deploy; Cloud Build builds the Dockerfile):

```bash
export GCP_PROJECT=<your-project-id>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project $GCP_PROJECT
gcloud run deploy worldline-verifier --source . --region europe-north1 --allow-unauthenticated --project $GCP_PROJECT
gcloud run services describe worldline-verifier --region europe-north1 --project $GCP_PROJECT --format 'value(status.url)'
```

Run the pipeline first (`uv run worldline demo`) so `experiments/out/bundle.json` exists and gets baked in; otherwise the image ships the synthetic fixtures.

### From GitLab CI

`.gitlab/ci/gcp.yml` defines `deploy-gcp`: stage `pages`, **manual**, protected refs only, `needs: [explore, kernel-verify, authorize]`, image `google/cloud-sdk:slim`. It authenticates with `gcloud auth activate-service-account --key-file $GCP_SA_KEY`, deletes any private key from the checkout, runs `gcloud run deploy worldline-verifier --source .` with `GIT_COMMIT=$CI_COMMIT_SHA`, prints the service URL, smoke-tests `/api/version` and writes `experiments/out/deploy.json` (`{service, region, project, url, commit, pipeline, deployed_at}`) as an artifact. Include it from `.gitlab-ci.yml`:

```yaml
include:
  - local: .gitlab/ci/gcp.yml
```

CI/CD variables (protected):

| Variable | Type | Value |
| --- | --- | --- |
| `GCP_PROJECT` | variable | GCP project id |
| `GCP_SA_KEY` | **file** | JSON key of the deploy service account |
| `GCP_REGION` | variable, optional | default `europe-north1` |
| `GCP_SERVICE` | variable, optional | default `worldline-verifier` |

### Service account and IAM roles

```bash
SA=worldline-deploy@$GCP_PROJECT.iam.gserviceaccount.com
gcloud iam service-accounts create worldline-deploy --project $GCP_PROJECT --display-name "WORLDLINE CI deploy"
for ROLE in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer roles/iam.serviceAccountUser roles/storage.admin; do
  gcloud projects add-iam-policy-binding $GCP_PROJECT --member serviceAccount:$SA --role $ROLE
done
gcloud iam service-accounts keys create gcp-sa-key.json --iam-account $SA   # upload as GCP_SA_KEY (file), then delete locally
```

| Role | Why |
| --- | --- |
| Cloud Run Admin (`roles/run.admin`) | create/update the service and set `--allow-unauthenticated` |
| Cloud Build Editor (`roles/cloudbuild.builds.editor`) | `--source` deploys submit a Cloud Build |
| Artifact Registry Writer (`roles/artifactregistry.writer`) | push the built image to the `cloud-run-source-deploy` repository |
| Service Account User (`roles/iam.serviceAccountUser`) | act as the Cloud Run runtime and Cloud Build service accounts |
| Storage Admin (`roles/storage.admin`) | upload the source tarball to the Cloud Build staging bucket |

The Cloud Build service account itself (`<project-number>-compute@developer.gserviceaccount.com` on new projects) also needs `roles/artifactregistry.writer` and `roles/logging.logWriter`; `gcloud run deploy --source` prompts for these grants on the first run.

## Security notes

- No private key in the image or the job; the service can only verify, never sign.
- Policy names are restricted to file stems inside `policies/` (`../kernel_private` is rejected).
- 5 MB body limit, 64-level JSON depth limit, JSON only, per-IP rate limit, stateless, no persistence, non-root container user.
- `/api/verify/release` fetches only `https` URLs on allowlisted hosts (`gitlab.com`), re-checks every redirect hop, caps each download at 5 MB and has a 10 s deadline, so it cannot be used to reach internal addresses.
- The rate limit uses the right-most `X-Forwarded-For` entry (the one Cloud Run's front end adds), so a client cannot dodge it by sending its own header.
- Cloud Run `--max-instances 3` bounds cost on a public endpoint; raise if needed.
