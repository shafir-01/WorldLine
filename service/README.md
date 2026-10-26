# WORLDLINE public verifier (`service/`)

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
| `GET` | `/api/version` | Git commit, kernel public key id, policy name, limits. |
| `POST` | `/api/verify` | Body: bundle JSON. Returns `ok`, `findings[]`, `root`, `signer_key_id`, `verifier_key_id` and a per-record summary (`index`, `kind`, `producer`, `claim`). |
| `POST` | `/api/authorize?artifact_digest=...[&policy=release][&now=ISO]` | Body: bundle JSON. Returns the kernel `Authorization` (`decision`, `reasons`, `satisfied`, `missing`, `stale`, `downgraded`, `policy`). `now` evaluates at a given time; by default the current time, so evidence older than the policy's `max_age_s` (24 h) is stale. |
| `GET` | `/api/evidence` | `{source, bundle, authorization}` from the data baked into the image. |
| `GET` | `/api/docs` | OpenAPI UI. |

Rules: JSON only (`415` otherwise), bodies capped at **5 MB** (`413`), malformed bundles `400`, errors as `{"error": "..."}`. CORS allows `GET`/`POST` from any origin; it is a public verifier.

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
```

## Run locally

```bash
uv sync --extra dev --extra service
cd ui && npm ci && npm run build && cd ..            # optional: without ui/dist only the API is served
uv run uvicorn service.app:app --port 8081 --reload
# http://127.0.0.1:8081/            viewer
# http://127.0.0.1:8081/api/docs    OpenAPI
uv run pytest tests/test_service.py -q
```

Data resolution: `WORLDLINE_DATA_DIR` if set, else `ui/dist/data`, else `experiments/out`, else `ui/fixtures`. Viewer: `WORLDLINE_UI_DIST` or `ui/dist`. Policy and public key: `WORLDLINE_POLICY_DIR` or `policies/`. `GIT_COMMIT` (or `CI_COMMIT_SHA`) overrides the commit reported by `/api/version`.

## Container

`Dockerfile` at the repo root is multi-stage: `node:20-alpine` builds `ui/` (its `copy-data` step takes `experiments/out` when present in the build context, else `ui/fixtures`), then `python:3.12-slim` installs `worldline[service]`, copies `ui/dist`, the policy and the public key, and runs `uvicorn` on `$PORT` as a non-root user.

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

`.gitlab/ci/gcp.yml` defines `deploy-gcp`: stage `authorize`, **manual**, protected refs only, `needs: [authorize, pages]`, image `google/cloud-sdk:slim`. It authenticates with `gcloud auth activate-service-account --key-file $GCP_SA_KEY`, deletes any private key from the checkout, runs `gcloud run deploy worldline-verifier --source .` with `GIT_COMMIT=$CI_COMMIT_SHA`, prints the service URL, smoke-tests `/api/version` and writes `experiments/out/deploy.json` (`{service, region, project, url, commit, pipeline, deployed_at}`) as an artifact. Include it from `.gitlab-ci.yml`:

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
- 5 MB body limit, JSON only, stateless, no persistence, non-root container user.
- Cloud Run `--max-instances 3` bounds cost on a public endpoint; raise if needed.
