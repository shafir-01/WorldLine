# Operations runbook: WORLDLINE public verifier

How to deploy, roll back, observe and pay for the public verifier (`service/`, `Dockerfile`) on Google Cloud Run. The service is stateless and read-only: it holds the kernel's **public** key and the release policy, never the signing key. Losing every instance loses nothing.

Live: https://worldline-verifier-301330323690.europe-north1.run.app

Every command below assumes these shell variables (bash; in PowerShell use `$env:NAME = "..."` and `$env:NAME` instead):

```bash
export PROJECT=<gcp-project-id>
export REGION=europe-north1
export SERVICE=worldline-verifier
export URL=$(gcloud run services describe $SERVICE --region $REGION --project $PROJECT --format 'value(status.url)')
```

## 1. What is running

| Piece | Value |
| --- | --- |
| Image | `Dockerfile`: `node:20-alpine` builds `ui/`, a `python:3.12-slim` + `uv` stage installs the locked deps into `/opt/venv`, a clean `python:3.12-slim` runtime copies the venv, `service/`, the public key, the policy and `ui/dist`. Both base images are pinned by digest. |
| Process | `uvicorn service.app:app --workers $WEB_CONCURRENCY` (default 2) as uid 10001, started with `exec`, so it is PID 1 and gets Cloud Run's `SIGTERM` directly. |
| Shutdown | On `SIGTERM` uvicorn stops accepting, drains in-flight requests for up to 8 s (`--timeout-graceful-shutdown 8`; Cloud Run allows 10 s), logs `{"event":"shutdown"}` and exits. |
| Memory | Measured locally: about 57 MB RSS per worker at idle and 80 MB peak while verifying a 4.9 MB bundle. Two workers with several concurrent large requests stay well under 512 MiB. |
| Image size | Estimated from the layers (docker was not available to measure): about 125 MB `python:3.12-slim`, about 65 MB `/opt/venv` (FastAPI, pydantic-core, cryptography, uvloop, httptools, PyYAML, rich, precompiled `.pyc`), about 10 MB viewer and data. Roughly **200 MB uncompressed, about 70 MB compressed** in Artifact Registry. Measure the real value with `docker image ls worldline-verifier` or `gcloud artifacts docker images list $REGION-docker.pkg.dev/$PROJECT/cloud-run-source-deploy --include-tags`. |
| Probes | `/api/healthz` (liveness, always 200 while the process serves), `/api/readyz` (200 only when the public key parses, `policies/release.yaml` loads and the data dir has `index.json`; 503 otherwise). The Dockerfile `HEALTHCHECK` hits `/api/readyz`, but Cloud Run ignores `HEALTHCHECK`. Configure Cloud Run probes as in section 2.3. |
| Limits | 5 MB bodies (`413`), JSON nesting at most 64 levels (`400`), 15 s to receive a body (`408`), 20 s of verification work (`503`), 10 s for upstream release fetches (`504`). `POST /api/*` and `GET /api/verify/release` share a token bucket of `WORLDLINE_RATE_LIMIT_PER_MIN` (default 30) per client IP, after which they return `429` with `Retry-After`. |

Environment variables the service reads:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT` | `8080` | Listen port (Cloud Run sets it). |
| `WEB_CONCURRENCY` | `2` | Uvicorn worker processes. |
| `GIT_COMMIT` | (build arg) | Commit reported by `/api/version`. |
| `WORLDLINE_RATE_LIMIT_PER_MIN` | `30` | Token bucket size and refill per minute, per client IP, per worker process. |
| `WORLDLINE_TRUSTED_PROXY_HOPS` | `1` | Which `X-Forwarded-For` entry (from the right) is the client. Cloud Run's front end appends the caller's address, so `1` is correct there. Set `2` if you put an external HTTPS load balancer in front. |
| `WORLDLINE_LOG_LEVEL` | `INFO` | `DEBUG` also logs probe traffic (`/api/healthz`, `/api/readyz`). |
| `WORLDLINE_RELEASE_HOSTS` | `gitlab.com` | Comma-separated host allowlist for `/api/verify/release`. |
| `WORLDLINE_RELEASE_API`, `WORLDLINE_RELEASE_PROJECT`, `WORLDLINE_RELEASE_PACKAGE` | `https://gitlab.com/api/v4`, `88121109`, `worldline-evidence` | Where release evidence is fetched from. The API host must be in the allowlist. |
| `WORLDLINE_UI_DIST`, `WORLDLINE_DATA_DIR`, `WORLDLINE_POLICY_DIR` | set by the image | Viewer, evidence data and policy/key locations. |

The rate limit is in memory, so it applies per worker process: with 3 instances and 2 workers one IP can make at most 6 x 30 = 180 POSTs a minute in the worst case. It is abuse control, not a quota.

## 2. Deploy

### 2.1 From GitLab CI (normal path)

1. Push to a protected branch or tag. The pipeline runs `explore`, `kernel-verify` and `authorize`.
2. In the pipeline, start the manual **`deploy-gcp`** job (`.gitlab/ci/gcp.yml`). It deletes any private key from the checkout, runs `gcloud run deploy --source .` with `GIT_COMMIT=$CI_COMMIT_SHA`, smoke-tests `/api/version` and saves `experiments/out/deploy.json`.
3. Check the new revision (section 2.4).

### 2.2 From a workstation

```bash
uv run worldline demo                      # so experiments/out/ is baked in instead of the fixtures
rm -f policies/kernel_private.pem          # never upload the signing key (.dockerignore excludes it too)
gcloud run deploy $SERVICE --source . --region $REGION --project $PROJECT \
  --allow-unauthenticated --port 8080 \
  --memory 512Mi --cpu 1 --concurrency 40 --timeout 60 \
  --min-instances 0 --max-instances 3 \
  --set-env-vars "GIT_COMMIT=$(git rev-parse HEAD)" \
  --labels "worldline-commit=$(git rev-parse --short HEAD)"
```

`--concurrency 40` (default 80) keeps a burst of 5 MB bodies on one 512 MiB instance bounded; `--timeout 60` caps any single request well below Cloud Run's 300 s default.

### 2.3 Startup and liveness probes (once per service)

Cloud Run does not use the Dockerfile `HEALTHCHECK`. Point its probes at the service's endpoints:

```bash
gcloud run services update $SERVICE --region $REGION --project $PROJECT \
  --startup-probe=httpGet.path=/api/readyz,httpGet.port=8080,initialDelaySeconds=0,periodSeconds=2,timeoutSeconds=2,failureThreshold=15 \
  --liveness-probe=httpGet.path=/api/healthz,httpGet.port=8080,periodSeconds=30,timeoutSeconds=3,failureThreshold=3
```

If your gcloud version rejects those flags (`gcloud components update`, or try `gcloud beta run services update`), set them in YAML instead:

```bash
gcloud run services describe $SERVICE --region $REGION --project $PROJECT --format export > service.yaml
# under spec.template.spec.containers[0] add:
#   startupProbe:  {httpGet: {path: /api/readyz, port: 8080}, periodSeconds: 2, timeoutSeconds: 2, failureThreshold: 15}
#   livenessProbe: {httpGet: {path: /api/healthz, port: 8080}, periodSeconds: 30, timeoutSeconds: 3, failureThreshold: 3}
gcloud run services replace service.yaml --region $REGION --project $PROJECT
```

With the startup probe on `/api/readyz`, a revision built without its public key, policy or data never receives traffic. The previous revision keeps serving.

### 2.4 Verify a deploy

```bash
curl -fsS $URL/api/readyz;  echo
curl -fsS $URL/api/version; echo        # git_commit must be the commit you deployed
curl -fsSI $URL/ | grep -iE 'content-security-policy|strict-transport-security|x-request-id'
curl -fsS "$URL/api/verify/release?tag=v0.1.3-transcend" | python -c "import json,sys; d=json.load(sys.stdin); print(d['verify']['ok'], d['authorize']['decision'])"
# True AUTHORIZED
```

## 3. Roll back

Every deploy creates an immutable revision. To roll back, move traffic to an older revision. Nothing is rebuilt.

```bash
# 1. Find the last good revision (newest first)
gcloud run revisions list --service $SERVICE --region $REGION --project $PROJECT \
  --format 'table(metadata.name, metadata.creationTimestamp, metadata.labels."worldline-commit", status.conditions[0].status)'

# 2. Send 100% of traffic to it
gcloud run services update-traffic $SERVICE --region $REGION --project $PROJECT \
  --to-revisions worldline-verifier-00042-abc=100

# 3. Confirm
gcloud run services describe $SERVICE --region $REGION --project $PROJECT --format 'value(status.traffic)'
curl -fsS $URL/api/version
```

Traffic now stays pinned to that revision, and a later `gcloud run deploy` creates a new revision **without routing traffic to it**. Once a fixed revision is deployed and checked, return to "latest":

```bash
gcloud run services update-traffic $SERVICE --region $REGION --project $PROJECT --to-latest
```

For a canary, split traffic instead (`--to-revisions NEW=10,OLD=90`), watch the alerts in section 6 and then go `--to-latest`.

## 4. Rotate the kernel key

The kernel's Ed25519 private key lives only in the protected, file-type CI variable `WORLDLINE_KERNEL_KEY`. Its public half is committed at `policies/kernel_public.pem` and baked into the image. Rotate on any suspicion of compromise, and at least yearly.

```powershell
# 1. New key pair (policies/kernel_private.pem is gitignored and .dockerignored)
uv run worldline keygen --force
uv run python -c "from worldline.evidence import key_id; print(key_id(open('policies/kernel_public.pem').read()))"   # note the new key id

# 2. Replace the CI secret, then delete the private key locally
glab variable update WORLDLINE_KERNEL_KEY --protected --type file --value "$(Get-Content -Raw policies/kernel_private.pem)"
Remove-Item policies/kernel_private.pem

# 3. Commit only the public key, through a merge request (policies/ is code-owner protected)
git add policies/kernel_public.pem
git commit -m "Rotate kernel signing key"
```

4. Run the pipeline on the protected branch. `kernel-verify` seals with the new key and `authorize` must say AUTHORIZED.
5. Run `deploy-gcp`. Check that `/api/version` shows the new `kernel_public_key_id`.

What changes for verifiers:

- Bundles sealed with the **old** key now return `UNKNOWN_SIGNER` from this service, including `/api/verify/release` for tags released before the rotation. That is intended after a compromise. For a routine rotation, tell people to verify old releases offline against the public key at that tag (`https://gitlab.com/shafir2/WorldLine/-/raw/<tag>/policies/kernel_public.pem`), as each release's notes already explain.
- If the old key leaked, also re-tag and re-release whatever must stay verifiable, and record the old key id as revoked in `SECURITY.md`.

## 5. Logs

The service writes one JSON object per line to stdout. Cloud Run sends them to Cloud Logging as `jsonPayload`, lifts `severity`, and lifts `httpRequest` into the entry's HTTP request field. Access lines carry `request_id`, `method`, `path`, `status`, `latency_ms` and `client_ip`, and never contain request bodies or query strings. Every response echoes `X-Request-ID`, which is the caller's own value when it matches `[A-Za-z0-9._:@/+=-]{1,128}`, else a generated one. Ask users to quote it.

```bash
BASE='resource.type="cloud_run_revision" AND resource.labels.service_name="worldline-verifier"'

# Recent errors (5xx and unhandled exceptions)
gcloud logging read "$BASE AND severity>=ERROR" --project $PROJECT --freshness 1h --limit 50 --format json

# One request, by the id a user reported
gcloud logging read "$BASE AND jsonPayload.request_id=\"abc-123\"" --project $PROJECT --freshness 7d --format json

# Slow requests
gcloud logging read "$BASE AND jsonPayload.latency_ms>1000" --project $PROJECT --freshness 1d \
  --format 'table(timestamp, jsonPayload.method, jsonPayload.path, jsonPayload.status, jsonPayload.latency_ms)'

# Who is being rate limited
gcloud logging read "$BASE AND jsonPayload.status=429" --project $PROJECT --freshness 1d \
  --format 'value(jsonPayload.client_ip)' | sort | uniq -c | sort -rn | head

# Startups and shutdowns (cold starts, SIGTERM drains)
gcloud logging read "$BASE AND jsonPayload.event=(\"startup\" OR \"shutdown\")" --project $PROJECT --freshness 1d --limit 20

# Live tail
gcloud beta run services logs tail $SERVICE --region $REGION --project $PROJECT
```

Probe requests (`/api/healthz`, `/api/readyz`) are logged at DEBUG and so are hidden by default. Set `WORLDLINE_LOG_LEVEL=DEBUG` to see them.

## 6. Alerts

Create one notification channel, then four alert policies on Cloud Run's built-in metrics, plus an uptime check on `/api/readyz`. These commands are not idempotent (re-running one creates a duplicate), so run each once and manage the result in the console (Monitoring > Alerting) afterwards.

```bash
# Notification channel (email)
gcloud beta monitoring channels create --project $PROJECT \
  --display-name "WORLDLINE owner" --type email --channel-labels email_address=shafirkhajo@gmail.com
CHANNEL=$(gcloud beta monitoring channels list --project $PROJECT \
  --filter 'displayName="WORLDLINE owner"' --format 'value(name)')
```

**6.1 5xx error rate above 5 % for 5 minutes**

```bash
cat > alert-error-rate.json <<EOF
{
  "displayName": "worldline-verifier: 5xx rate > 5%",
  "combiner": "OR",
  "conditions": [{
    "displayName": "5xx / all requests > 0.05",
    "conditionThreshold": {
      "filter": "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\"",
      "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE", "crossSeriesReducer": "REDUCE_SUM"}],
      "denominatorFilter": "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND metric.type=\"run.googleapis.com/request_count\"",
      "denominatorAggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE", "crossSeriesReducer": "REDUCE_SUM"}],
      "comparison": "COMPARISON_GT",
      "thresholdValue": 0.05,
      "duration": "300s",
      "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["$CHANNEL"],
  "documentation": {"content": "See docs/OPERATIONS.md sections 3 (roll back) and 5 (logs: severity>=ERROR).", "mimeType": "text/markdown"}
}
EOF
gcloud alpha monitoring policies create --project $PROJECT --policy-from-file alert-error-rate.json
```

**6.2 Any 5xx burst (absolute count, catches low-traffic outages)**

```bash
cat > alert-5xx.json <<EOF
{
  "displayName": "worldline-verifier: >= 5 server errors in 5 min",
  "combiner": "OR",
  "conditions": [{
    "displayName": "5xx count",
    "conditionThreshold": {
      "filter": "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\"",
      "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_DELTA", "crossSeriesReducer": "REDUCE_SUM"}],
      "comparison": "COMPARISON_GT",
      "thresholdValue": 4,
      "duration": "0s",
      "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["$CHANNEL"]
}
EOF
gcloud alpha monitoring policies create --project $PROJECT --policy-from-file alert-5xx.json
```

**6.3 p95 latency above 2 s for 10 minutes**

```bash
cat > alert-latency.json <<EOF
{
  "displayName": "worldline-verifier: p95 latency > 2s",
  "combiner": "OR",
  "conditions": [{
    "displayName": "request_latencies p95 > 2000 ms",
    "conditionThreshold": {
      "filter": "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND metric.type=\"run.googleapis.com/request_latencies\"",
      "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_PERCENTILE_95", "crossSeriesReducer": "REDUCE_MAX"}],
      "comparison": "COMPARISON_GT",
      "thresholdValue": 2000,
      "duration": "600s",
      "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["$CHANNEL"]
}
EOF
gcloud alpha monitoring policies create --project $PROJECT --policy-from-file alert-latency.json
```

**6.4 Sustained rate limiting (possible abuse), via a log-based metric**

```bash
gcloud logging metrics create worldline_rate_limited --project $PROJECT \
  --description "429s from the WORLDLINE verifier" \
  --log-filter 'resource.type="cloud_run_revision" AND resource.labels.service_name="worldline-verifier" AND jsonPayload.status=429'

cat > alert-429.json <<EOF
{
  "displayName": "worldline-verifier: > 100 rate-limited requests in 10 min",
  "combiner": "OR",
  "conditions": [{
    "displayName": "429 count",
    "conditionThreshold": {
      "filter": "resource.type=\"cloud_run_revision\" AND metric.type=\"logging.googleapis.com/user/worldline_rate_limited\"",
      "aggregations": [{"alignmentPeriod": "600s", "perSeriesAligner": "ALIGN_DELTA", "crossSeriesReducer": "REDUCE_SUM"}],
      "comparison": "COMPARISON_GT",
      "thresholdValue": 100,
      "duration": "0s",
      "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["$CHANNEL"]
}
EOF
gcloud alpha monitoring policies create --project $PROJECT --policy-from-file alert-429.json
```

**6.5 Uptime check on readiness, and an alert when it fails**

```bash
HOST=${URL#https://}
gcloud monitoring uptime create worldline-readyz --project $PROJECT \
  --resource-type uptime-url --resource-labels host=$HOST,project_id=$PROJECT \
  --protocol https --path /api/readyz --period 5 --timeout 10
CHECK_ID=$(gcloud monitoring uptime list-configs --project $PROJECT \
  --filter 'displayName="worldline-readyz"' --format 'value(name.basename())')

cat > alert-uptime.json <<EOF
{
  "displayName": "worldline-verifier: /api/readyz failing",
  "combiner": "OR",
  "conditions": [{
    "displayName": "uptime check failed",
    "conditionThreshold": {
      "filter": "resource.type=\"uptime_url\" AND metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id=\"$CHECK_ID\"",
      "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_NEXT_OLDER", "crossSeriesReducer": "REDUCE_COUNT_FALSE", "groupByFields": ["resource.label.project_id"]}],
      "comparison": "COMPARISON_GT",
      "thresholdValue": 1,
      "duration": "600s",
      "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["$CHANNEL"]
}
EOF
gcloud alpha monitoring policies create --project $PROJECT --policy-from-file alert-uptime.json
```

Each uptime probe from a region is a cold-start risk on `--min-instances 0`. The first probe after an idle period can take one to two seconds, which is why the timeout is 10 s.

List or remove what you created:

```bash
gcloud alpha monitoring policies list --project $PROJECT --format 'table(name, displayName, enabled)'
gcloud alpha monitoring policies delete <policy-name> --project $PROJECT
```

## 7. Cost on the free tier

The service scales to zero (`--min-instances 0`) and has request-based billing, so an idle service costs nothing. Free allowances, per billing account per month, from the Google Cloud pricing pages at the time of writing (check them before relying on these numbers):

| Product | Free each month | What this service uses |
| --- | --- | --- |
| Cloud Run (request-based) | 2 M requests, 180,000 vCPU-s, 360,000 GiB-s | A typical verify is 5 to 50 ms of CPU. Billable time is rounded up to 100 ms per request, and concurrent requests share an instance. That is roughly **1.8 M non-overlapping requests** before the CPU allowance runs out. |
| Cloud Build | 2,500 build-minutes (e2-standard-2) | A source deploy is about 3 to 5 min (npm ci, vite build, uv sync). |
| Artifact Registry | 0.5 GB storage | About 70 MB per image, so keep at most about 6 revisions' images (cleanup policy below). |
| Cloud Logging | 50 GiB ingestion | About 0.5 KB per access line, so about 100 M requests. |
| Cloud Monitoring | Built-in Cloud Run metrics are free. 1 M uptime-check executions. | A 5-minute uptime check from the default regions is about 50,000 executions a month. |

Worst case, under a sustained attack and with all 3 instances busy around the clock, at Tier 1 list prices (about $0.000024 per vCPU-s and $0.0000025 per GiB-s): 3 x 2.6 M s x (1 x 0.000024 + 0.5 x 0.0000025) is about **$200 a month** before the free allowance. `--max-instances 3`, the per-IP rate limit and the 5 MB / 60 s caps are what bound it. Set a budget alert so you hear about it within a day:

```bash
gcloud billing budgets create --billing-account <BILLING_ACCOUNT_ID> \
  --display-name "worldline monthly" --budget-amount 5USD \
  --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0
```

Keep Artifact Registry inside the free 0.5 GB:

```bash
cat > ar-cleanup.json <<'EOF'
[
  {"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 5}},
  {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"olderThan": "2592000s"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --project $PROJECT --location $REGION --policy ar-cleanup.json --no-dry-run
```

## 8. Incident checklist

1. `curl -fsS $URL/api/readyz` shows which dependency is missing (`checks.public_key`, `checks.policy`, `checks.data`).
2. `gcloud logging read "$BASE AND severity>=ERROR"`. Use the `request_id` from a user report to find the exact request.
3. If the last deploy is to blame, roll back (section 3) first and debug after.
4. If someone is abusing the endpoint, look at the `429` client IPs (section 5). Lower `WORLDLINE_RATE_LIMIT_PER_MIN` with `gcloud run services update $SERVICE --update-env-vars WORLDLINE_RATE_LIMIT_PER_MIN=10`, or set `--max-instances 1`.
5. If GitLab is down, `/api/verify/release` returns 502 or 504. `/api/verify` and `/api/authorize` with an uploaded bundle are unaffected.
