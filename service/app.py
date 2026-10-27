"""WORLDLINE public verifier.

A small FastAPI service that (1) serves the built viewer (ui/dist, with its data/ directory)
and (2) exposes the evidence kernel's *verification* half as a public API.

The service holds only the kernel's PUBLIC key and the release policy. It can tell whether a
bundle was sealed by the kernel and whether it satisfies the policy; it can never seal, attest
or authorize anything on its own. Anyone can therefore check a release's evidence without
trusting the pipeline that produced it, or the host that runs this service.

Edge concerns (request ids, JSON access logs, security and cache headers, per-IP rate limit,
CORS on /api/* only) live in service/middleware.py; fetching a tagged release's evidence from
the GitLab package registry lives in service/release.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, TypeVar

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from worldline.evidence import Bundle, authorize as kernel_authorize, key_id, load_policy

from . import release as release_fetch
from .middleware import EdgeMiddleware, PathScopedCORS, TokenBucket, configure_logging

MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_JSON_DEPTH = 64               # bundles nest ~5 deep; anything near this is abuse
BODY_READ_TIMEOUT_S = 15.0        # slow-loris guard for request bodies
COMPUTE_TIMEOUT_S = 20.0          # verify/authorize wall clock before we give up with 503
RATE_LIMIT_PER_MIN = int(os.environ.get("WORLDLINE_RATE_LIMIT_PER_MIN", "30"))
TRUSTED_PROXY_HOPS = int(os.environ.get("WORLDLINE_TRUSTED_PROXY_HOPS", "1"))

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_DIR = Path(os.environ.get("WORLDLINE_POLICY_DIR", REPO_ROOT / "policies"))
PUBLIC_KEY_PATH = POLICY_DIR / "kernel_public.pem"
DEFAULT_POLICY = "release"
EVIDENCE_FILES = ("bundle.json", "authorization.json")

log = logging.getLogger("worldline.service")


def _env_path(name: str) -> Path | None:
    v = os.environ.get(name)
    return Path(v) if v else None


def _first_existing(*candidates: Path | None, check: str) -> Path | None:
    for c in candidates:
        if c is not None and c.is_dir() and (c / check).exists():
            return c
    return None


# Built viewer: ui/dist (or wherever the image put it).
UI_DIST = _first_existing(_env_path("WORLDLINE_UI_DIST"), REPO_ROOT / "ui" / "dist", check="index.html")
# Pipeline output served as data/ and /api/evidence: prefer what the viewer ships, then the raw
# pipeline output, then the synthetic fixtures.
DATA_DIR = _first_existing(
    _env_path("WORLDLINE_DATA_DIR"),
    (UI_DIST / "data") if UI_DIST else None,
    REPO_ROOT / "experiments" / "out",
    REPO_ROOT / "ui" / "fixtures",
    check="bundle.json",
)


def _public_key_pem() -> str:
    try:
        return PUBLIC_KEY_PATH.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - deployment error
        raise HTTPException(status_code=500, detail=f"kernel public key not found at {PUBLIC_KEY_PATH}") from exc


def _policy_path(name: str) -> Path:
    # Policy names are file stems inside POLICY_DIR; no path components, never the key files.
    if not name or any(ch in name for ch in "/\\.") or name.startswith("kernel_"):
        raise HTTPException(status_code=400, detail=f"unknown policy {name!r}")
    p = POLICY_DIR / f"{name}.yaml"
    if not p.is_file():
        raise HTTPException(status_code=400, detail=f"unknown policy {name!r}")
    return p


def _git_commit() -> str | None:
    env = os.environ.get("GIT_COMMIT") or os.environ.get("CI_COMMIT_SHA")
    if env:
        return env
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, cwd=str(REPO_ROOT))
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if DATA_DIR and (DATA_DIR / "index.json").is_file():
        try:
            return json.loads((DATA_DIR / "index.json").read_text(encoding="utf-8")).get("code_commit")
        except (OSError, ValueError):
            return None
    return None


# --------------------------------------------------------------------------- body parsing

# Strings are matched whole (so brackets inside them are skipped); brackets are counted.
_JSON_TOKENS = re.compile(rb'"(?:[^"\\]|\\.)*"|[\[\]{}]', re.DOTALL)


def json_depth_exceeds(raw: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    """True if ``raw`` nests arrays/objects deeper than ``limit``. Runs before json.loads so a
    hostile 5 MB ``[[[[...`` never reaches the parser (or Python's recursion limit)."""
    if raw.count(b"[") + raw.count(b"{") <= limit:
        return False
    depth = 0
    for m in _JSON_TOKENS.finditer(raw):
        tok = m.group()
        if tok in (b"[", b"{"):
            depth += 1
            if depth > limit:
                return True
        elif tok in (b"]", b"}"):
            depth -= 1
    return False


def parse_bundle(raw: bytes) -> Bundle:
    """Load a bundle leniently from JSON bytes (verify() judges its integrity, not this)."""
    if json_depth_exceeds(raw):
        raise HTTPException(status_code=400, detail=f"JSON nests deeper than {MAX_JSON_DEPTH} levels")
    try:
        doc = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise HTTPException(status_code=400, detail=f"body is not valid JSON: {exc}")
    if not isinstance(doc, dict) or not isinstance(doc.get("records"), list):
        raise HTTPException(status_code=400, detail="body must be a worldline.bundle.v1 object with a records[] array")
    try:
        return Bundle.from_dict(doc)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"malformed bundle: {exc}")


async def _read_body(request: Request) -> bytes:
    ctype = request.headers.get("content-type", "")
    if ctype.split(";")[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            too_big = int(declared) > MAX_BODY_BYTES
        except ValueError:
            raise HTTPException(status_code=400, detail="bad Content-Length")
        if too_big:
            raise HTTPException(status_code=413, detail=f"body exceeds {MAX_BODY_BYTES} bytes")
    size = 0
    chunks: list[bytes] = []
    try:
        async with asyncio.timeout(BODY_READ_TIMEOUT_S):
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail=f"body exceeds {MAX_BODY_BYTES} bytes")
                chunks.append(chunk)
    except TimeoutError:
        raise HTTPException(status_code=408, detail=f"request body not received within {BODY_READ_TIMEOUT_S:.0f}s")
    return b"".join(chunks)


async def _read_bundle(request: Request) -> Bundle:
    """Read a JSON body of at most MAX_BODY_BYTES and load it as a Bundle."""
    raw = await _read_body(request)
    return await _compute(parse_bundle, raw)


T = TypeVar("T")


async def _compute(fn: Callable[..., T], *args: Any) -> T:
    """Run CPU-bound kernel work off the event loop, with a wall-clock limit."""
    try:
        return await asyncio.wait_for(run_in_threadpool(fn, *args), COMPUTE_TIMEOUT_S)
    except TimeoutError:
        raise HTTPException(status_code=503, detail=f"verification exceeded {COMPUTE_TIMEOUT_S:.0f}s")


def _records_summary(bundle: Bundle) -> list[dict[str, Any]]:
    return [{"index": i, "kind": r.kind.value, "producer": r.producer, "claim": r.claim}
            for i, r in enumerate(bundle.records)]


def _verify_payload(bundle: Bundle, pem: str) -> dict[str, Any]:
    vr = bundle.verify(pem)
    return {
        "ok": vr.ok,
        "findings": [f.to_dict() for f in vr.findings],
        "root": vr.verified_root,
        "signer_key_id": vr.signer_key_id,
        "verifier_key_id": key_id(pem),
        "n_records": len(bundle.records),
        "records": _records_summary(bundle),
    }


def _authorize_payload(bundle: Bundle, policy_name: str, digest: str, pem: str, now_iso: str) -> dict[str, Any]:
    pol = load_policy(_policy_path(policy_name))
    try:
        auth = kernel_authorize(bundle, pol, digest, pem, now_iso)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"bad `now`: {exc}")
    return auth.to_dict()


# ------------------------------------------------------------------------------ OpenAPI

_BUNDLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "worldline.bundle.v1",
    "description": "A sealed evidence bundle exactly as the pipeline writes it (`experiments/out/bundle.json` "
                   "or the `bundle.json` release asset). Loaded leniently: integrity is judged by verification.",
    "required": ["records"],
    "properties": {
        "format": {"type": "string", "example": "worldline.bundle.v1"},
        "records": {"type": "array", "items": {"type": "object"}},
        "links": {"type": "array", "items": {"type": "object"}},
        "checkpoint": {"type": "object", "nullable": True},
    },
}
_BUNDLE_EXAMPLE: dict[str, Any] = {
    "format": "worldline.bundle.v1",
    "records": [{
        "id": "sha256:3c69073a668c469987709cb295cb8b0ac0dff789e9d9eae0ceda282f2d1763cb",
        "kind": "EMPIRICALLY_VALIDATED", "claim": "tests:unit", "producer": "agent:pipeline",
        "subject": "f9cd9c8926530d76b139c3f85433c2ffc65e3d52", "procedure": "pytest --junitxml",
        "inputs_hash": "sha256:c8042b81721442bd9da5df4508efdec325ccc52569ea73cd27caa8edca411fbf",
        "result": {"tests": 144, "passed": 144, "failures": 0, "errors": 0, "skipped": 0},
        "assumptions": ["bounded horizon", "declared delivery policies", "synthetic scenario"],
        "derived_at": "2026-10-26T18:42:46Z",
    }],
    "links": [{"prev_hash": "sha256:" + "0" * 64,
               "hash": "sha256:b93fbf2d5450b49c21fa8cab56775ade4cbeaeb74d7903bb97acc1a4d49a9cfd"}],
    "checkpoint": {"first_index": 0, "last_index": 0, "root": "sha256:b93fbf2d...", "sealed_at": "2026-10-26T18:42:47Z",
                   "signature": "19d9b18c...", "signer_key_id": "ed25519:9488a68d97e78e9442330d8b689eaf6c"},
}
_BUNDLE_BODY = {"requestBody": {"required": True, "content": {"application/json": {
    "schema": _BUNDLE_SCHEMA, "example": _BUNDLE_EXAMPLE}}}}

_VERIFY_EXAMPLE = {
    "ok": True, "findings": [],
    "root": "sha256:ed4d6252c03b4c42d728080f24c31363b18cf78732890d0004410a5e9bb8be4b",
    "signer_key_id": "ed25519:9488a68d97e78e9442330d8b689eaf6c",
    "verifier_key_id": "ed25519:9488a68d97e78e9442330d8b689eaf6c",
    "n_records": 31,
    "records": [{"index": 0, "kind": "EMPIRICALLY_VALIDATED", "producer": "agent:pipeline", "claim": "tests:unit"}],
}
_VERIFY_TAMPERED_EXAMPLE = {
    "ok": False,
    "findings": [{"code": "RECORD_EDITED", "index": 0, "detail": "record 0 content does not match its id"},
                 {"code": "ROOT_MISMATCH", "index": None, "detail": "recomputed root differs from checkpoint"}],
    "root": None, "signer_key_id": "ed25519:9488a68d97e78e9442330d8b689eaf6c",
    "verifier_key_id": "ed25519:9488a68d97e78e9442330d8b689eaf6c", "n_records": 31, "records": [],
}
_AUTH_EXAMPLE = {
    "decision": "AUTHORIZED", "artifact_digest": "f9cd9c8926530d76b139c3f85433c2ffc65e3d52", "policy": "release",
    "evaluated_at": "2026-10-26T18:42:47.147920+00:00",
    "reasons": ["all 5 requirement(s) met by 15 record(s)"],
    "satisfied": [{"claim_pattern": "tests:unit", "claim": "tests:unit", "kind": "ATTESTED", "producer": "kernel",
                   "record_id": "sha256:98f41b5e...", "subject": "f9cd9c8926530d76b139c3f85433c2ffc65e3d52",
                   "assumptions": ["bounded horizon"]}],
    "missing": [], "stale": [], "downgraded": [],
}
_AUTH_DENIED_EXAMPLE = {**_AUTH_EXAMPLE, "decision": "DENIED", "artifact_digest": "deadbeef", "satisfied": [],
                        "reasons": ["subject_mismatch: tests:unit record is about f9cd9c89..., not deadbeef"],
                        "missing": ["tests:unit"]}
_ERR = {"application/json": {"schema": {"type": "object", "properties": {"error": {"type": "string"}}},
                             "example": {"error": "body exceeds 5242880 bytes"}}}
_COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Malformed JSON or bundle, too deep, bad policy name or `now`", "content": _ERR},
    408: {"description": "Body not received in time", "content": _ERR},
    413: {"description": "Body over 5 MB", "content": _ERR},
    415: {"description": "Content-Type is not application/json", "content": _ERR},
    429: {"description": "Rate limited (per client IP); see `Retry-After`", "content": _ERR},
}
_RELEASE_EXAMPLE = {
    "tag": "v0.1.3-transcend",
    "package_url": "https://gitlab.com/api/v4/projects/88121109/packages/generic/worldline-evidence/v0.1.3-transcend",
    "bundle_url": "https://gitlab.com/api/v4/projects/88121109/packages/generic/worldline-evidence/v0.1.3-transcend/bundle.json",
    "artifact_digest": "1bfce46216da6cf05085684a26f7074b9cbf6ad9",
    "artifact_digest_source": "release.commit",
    "evaluated_at": "2026-10-27T09:02:38Z",
    "verify": _VERIFY_EXAMPLE,
    "authorize": {**_AUTH_EXAMPLE, "artifact_digest": "1bfce46216da6cf05085684a26f7074b9cbf6ad9"},
}

TAGS = [
    {"name": "verify", "description": "Check sealed evidence and evaluate the release policy with the public key only."},
    {"name": "evidence", "description": "The evidence this deployment was built from."},
    {"name": "ops", "description": "Liveness, readiness and build information."},
]


# ------------------------------------------------------------------------------ the app

@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info("worldline-verifier starting", extra={"json_fields": {
        "event": "startup", "git_commit": _git_commit(), "data_dir": str(DATA_DIR) if DATA_DIR else None,
        "viewer": UI_DIST is not None, "rate_limit_per_min": RATE_LIMIT_PER_MIN}})
    yield
    # Uvicorn turns SIGTERM into a graceful shutdown: stop accepting, drain in-flight requests
    # (bounded by --timeout-graceful-shutdown), then run this.
    log.info("worldline-verifier stopped", extra={"json_fields": {"event": "shutdown"}})


configure_logging()

app = FastAPI(
    title="WORLDLINE public verifier",
    summary="Check a WORLDLINE release's evidence without trusting the pipeline that produced it.",
    description=(
        "The service holds only the evidence kernel's **public** Ed25519 key and the release policy. "
        "It re-checks a sealed bundle's hash chain and checkpoint signature (`/api/verify`), re-runs the "
        "release gate (`/api/authorize`), and can do both for a published tag straight from the GitLab "
        "package registry (`/api/verify/release`). It can never seal, attest or authorize anything itself.\n\n"
        "Errors are `{\"error\": \"...\"}`. Bodies are JSON only, at most 5 MB and 64 levels deep. "
        "`POST` endpoints are rate limited per client IP (default 30/min; `429` with `Retry-After`). "
        "Every response carries `X-Request-ID` (yours, if you sent one)."
    ),
    version="0.2.0",
    openapi_tags=TAGS,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
    lifespan=_lifespan,
)
app.state.limiter = TokenBucket(RATE_LIMIT_PER_MIN, 60.0)
# Added last = outermost: CORS (only on /api/*) wraps the edge middleware, so even 429s carry
# CORS headers and browsers can read them.
app.add_middleware(EdgeMiddleware, limiter=app.state.limiter, trusted_proxy_hops=TRUSTED_PROXY_HOPS)
app.add_middleware(
    PathScopedCORS,
    prefix="/api/",
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After", "ETag"],
    max_age=3600,
)


@app.exception_handler(HTTPException)
async def _http_exc(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail}, headers=exc.headers)


@app.get("/api/healthz", tags=["ops"], summary="Liveness")
def healthz() -> dict[str, Any]:
    """Cheap liveness probe: the process is up and serving."""
    return {"status": "ok", "public_key": PUBLIC_KEY_PATH.is_file(),
            "data": DATA_DIR is not None, "viewer": UI_DIST is not None}


def _readiness() -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {}
    try:
        checks["public_key"] = key_id(PUBLIC_KEY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any failure means not ready
        checks["public_key"] = f"error: {type(exc).__name__}"
    try:
        checks["policy"] = load_policy(POLICY_DIR / f"{DEFAULT_POLICY}.yaml").name
    except Exception as exc:  # noqa: BLE001
        checks["policy"] = f"error: {type(exc).__name__}"
    data_dir = DATA_DIR
    checks["data"] = str(data_dir) if data_dir is not None and (data_dir / "index.json").is_file() \
        else "error: no index.json in data dir"
    ok = not any(isinstance(v, str) and v.startswith("error:") for v in checks.values())
    return ok, checks


@app.get("/api/readyz", tags=["ops"], summary="Readiness",
         responses={503: {"description": "A dependency is missing; see `checks`"}})
def readyz() -> JSONResponse:
    """Ready when the kernel public key parses, the release policy loads and the data dir has `index.json`."""
    ok, checks = _readiness()
    return JSONResponse(status_code=200 if ok else 503,
                        content={"status": "ready" if ok else "unavailable", "checks": checks})


@app.get("/api/version", tags=["ops"], summary="Build and policy information")
def version() -> dict[str, Any]:
    policy = load_policy(POLICY_DIR / f"{DEFAULT_POLICY}.yaml")
    return {
        "service": "worldline-verifier",
        "git_commit": _git_commit(),
        "kernel_public_key_id": key_id(_public_key_pem()),
        "policy": policy.name,
        "policy_requirements": len(policy.required),
        "policy_max_age_s": policy.max_age_s,
        "max_body_bytes": MAX_BODY_BYTES,
        "data_dir": str(DATA_DIR) if DATA_DIR else None,
    }


@app.post(
    "/api/verify", tags=["verify"], summary="Verify a sealed evidence bundle",
    openapi_extra=_BUNDLE_BODY,
    responses={200: {"description": "Verification result (`ok` false lists the findings)", "content": {
        "application/json": {"examples": {"intact": {"value": _VERIFY_EXAMPLE},
                                          "tampered": {"value": _VERIFY_TAMPERED_EXAMPLE}}}}},
               **_COMMON_ERRORS},
)
async def verify(request: Request) -> dict[str, Any]:
    """Verify a bundle: chain integrity, checkpoint coverage and Ed25519 signature against the kernel public key.

    An edited, dropped, reordered or truncated record shows up as a finding (`RECORD_EDITED`,
    `RECORD_DROPPED`, `RECORDS_REORDERED`, `TRUNCATED`, ...); a bundle signed with another key is
    `UNKNOWN_SIGNER`."""
    bundle = await _read_bundle(request)
    return await _compute(_verify_payload, bundle, _public_key_pem())


@app.post(
    "/api/authorize", tags=["verify"], summary="Evaluate the release gate for an artifact",
    openapi_extra=_BUNDLE_BODY,
    responses={200: {"description": "The kernel's Authorization", "content": {
        "application/json": {"examples": {"authorized": {"value": _AUTH_EXAMPLE},
                                          "denied": {"value": _AUTH_DENIED_EXAMPLE}}}}},
               **_COMMON_ERRORS},
)
async def authorize(
    request: Request,
    artifact_digest: str = Query(..., min_length=1, max_length=256,
                                 description="Artifact digest the release is about, e.g. the git commit SHA.",
                                 examples=["f9cd9c8926530d76b139c3f85433c2ffc65e3d52"]),
    policy: str = Query(DEFAULT_POLICY, description="Policy name (file stem under policies/)."),
    now: str | None = Query(None, description="ISO-8601 time to evaluate at (default: now). "
                                              "Evidence older than the policy's max_age_s is stale.",
                            examples=["2026-10-26T18:42:47Z"]),
) -> dict[str, Any]:
    """Release gate: AUTHORIZED or DENIED from the bundle, the named policy and the kernel public key."""
    _policy_path(policy)  # reject unknown policies before reading the body
    bundle = await _read_bundle(request)
    now_iso = now or datetime.now(timezone.utc).isoformat()
    return await _compute(_authorize_payload, bundle, policy, artifact_digest, _public_key_pem(), now_iso)


async def _verify_release(tag: str, artifact_digest: str | None, policy: str, now: str | None) -> dict[str, Any]:
    _policy_path(policy)
    try:
        ev = await release_fetch.fetch_release(tag, MAX_BODY_BYTES)
    except release_fetch.ReleaseError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    digest = artifact_digest or ev.artifact_digest
    if not digest:
        raise HTTPException(status_code=422, detail=f"could not determine the commit of tag {tag!r} from the "
                                                    "release or index.json; pass artifact_digest")
    bundle = await _compute(parse_bundle, ev.bundle_raw)
    sealed_at = bundle.checkpoint.sealed_at if bundle.checkpoint is not None else None
    now_iso = now or sealed_at or datetime.now(timezone.utc).isoformat()
    pem = _public_key_pem()
    return {
        "tag": ev.tag,
        "package_url": ev.package_url,
        "bundle_url": ev.bundle_url,
        "artifact_digest": digest,
        "artifact_digest_source": "query" if artifact_digest else ev.artifact_digest_source,
        "evaluated_at": now_iso,
        "verify": await _compute(_verify_payload, bundle, pem),
        "authorize": await _compute(_authorize_payload, bundle, policy, digest, pem, now_iso),
    }


_RELEASE_DOC = (
    "Fetch `bundle.json` for a tag from the GitLab generic package registry "
    "(`worldline-evidence/<tag>/`), then verify it and evaluate the release gate against the tag's "
    "commit. The commit comes from the GitLab release (`commit.id`, else the `Commit:` line in the notes), "
    "else from `index.json` in the same package; `artifact_digest` overrides it.\n\n"
    "By default the gate is evaluated **at the bundle's seal time** (what the CI gate saw); pass `now` to "
    "evaluate at another time, where evidence older than the policy's `max_age_s` is stale.\n\n"
    "Only `https://gitlab.com` is fetched (allowlist), with a 10 s deadline; results are cached for 5 min per tag."
)
_RELEASE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"description": "`verify` and `authorize` in the same shapes as `/api/verify` and `/api/authorize`",
          "content": {"application/json": {"example": _RELEASE_EXAMPLE}}},
    400: {"description": "Invalid tag or policy, or a malformed bundle", "content": _ERR},
    404: {"description": "No bundle published for that tag", "content": _ERR},
    422: {"description": "The tag's commit could not be determined", "content": _ERR},
    429: {"description": "Rate limited", "content": _ERR},
    502: {"description": "Upstream (GitLab) error", "content": _ERR},
    504: {"description": "Upstream took longer than 10 s", "content": _ERR},
}
_TAG_Q = Query(..., min_length=1, max_length=128, description="Release tag.", examples=["v0.1.3-transcend"])
_DIGEST_Q = Query(None, min_length=1, max_length=256, description="Override the artifact digest (default: the tag's commit).")
_POLICY_Q = Query(DEFAULT_POLICY, description="Policy name (file stem under policies/).")
_NOW_Q = Query(None, description="ISO-8601 time to evaluate at (default: the bundle's seal time).")


@app.post("/api/verify/release", tags=["verify"], summary="Verify and authorize a published release",
          description=_RELEASE_DOC, responses=_RELEASE_RESPONSES)
async def verify_release(tag: str = _TAG_Q, artifact_digest: str | None = _DIGEST_Q,
                         policy: str = _POLICY_Q, now: str | None = _NOW_Q) -> dict[str, Any]:
    return await _verify_release(tag, artifact_digest, policy, now)


@app.get("/api/verify/release", tags=["verify"], summary="Verify and authorize a published release (GET)",
         description=_RELEASE_DOC, responses=_RELEASE_RESPONSES)
async def verify_release_get(tag: str = _TAG_Q, artifact_digest: str | None = _DIGEST_Q,
                             policy: str = _POLICY_Q, now: str | None = _NOW_Q) -> dict[str, Any]:
    return await _verify_release(tag, artifact_digest, policy, now)


_evidence_cache: dict[str, Any] = {"key": None, "body": b"", "etag": ""}


def _evidence_body() -> tuple[bytes, str]:
    assert DATA_DIR is not None
    stamp: list[Any] = [str(DATA_DIR)]
    for name in EVIDENCE_FILES:
        p = DATA_DIR / name
        try:
            st = p.stat()
            stamp.append((name, st.st_mtime_ns, st.st_size))
        except OSError:
            stamp.append((name, None))
    key = repr(stamp)
    if _evidence_cache["key"] != key:
        out: dict[str, Any] = {"source": str(DATA_DIR)}
        for name in EVIDENCE_FILES:
            p = DATA_DIR / name
            k = name[: -len(".json")]
            try:
                out[k] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
            except ValueError:
                out[k] = None
        body = json.dumps(out, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        _evidence_cache.update(key=key, body=body, etag='"' + hashlib.sha256(body).hexdigest()[:32] + '"')
    return _evidence_cache["body"], _evidence_cache["etag"]


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    return any(t.strip().removeprefix("W/") == etag for t in header.split(","))


@app.get("/api/evidence", tags=["evidence"], summary="Evidence baked into this deployment",
         responses={200: {"description": "`{source, bundle, authorization}`; carries an `ETag`"},
                    304: {"description": "Not modified (`If-None-Match` matched)"}})
def evidence(request: Request) -> Response:
    """The bundle and authorization this deployment ships (the data the viewer renders)."""
    if DATA_DIR is None:
        raise HTTPException(status_code=404, detail="no evidence data baked into this deployment")
    body, etag = _evidence_body()
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag})
    return Response(content=body, media_type="application/json", headers={"ETag": etag})


# ------------------------------------------------------------------ viewer (static)
if UI_DIST is not None:
    if DATA_DIR is not None and not (UI_DIST / "data").is_dir():
        app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIST / "index.html")

    app.mount("/", StaticFiles(directory=str(UI_DIST), html=True), name="viewer")
