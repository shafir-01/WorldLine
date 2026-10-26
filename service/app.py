"""WORLDLINE public verifier.

A small FastAPI service that (1) serves the built viewer (ui/dist, with its data/ directory)
and (2) exposes the evidence kernel's *verification* half as a public API.

The service holds only the kernel's PUBLIC key and the release policy. It can tell whether a
bundle was sealed by the kernel and whether it satisfies the policy; it can never seal, attest
or authorize anything on its own. Anyone can therefore check a release's evidence without
trusting the pipeline that produced it, or the host that runs this service.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from worldline.evidence import Bundle, authorize as kernel_authorize, key_id, load_policy

MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB
REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_DIR = Path(os.environ.get("WORLDLINE_POLICY_DIR", REPO_ROOT / "policies"))
PUBLIC_KEY_PATH = POLICY_DIR / "kernel_public.pem"
DEFAULT_POLICY = "release"
EVIDENCE_FILES = ("bundle.json", "authorization.json")


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


async def _read_bundle(request: Request) -> Bundle:
    """Read a JSON body of at most MAX_BODY_BYTES and load it as a Bundle (leniently: verify() judges it)."""
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
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail=f"body exceeds {MAX_BODY_BYTES} bytes")
        chunks.append(chunk)
    try:
        doc = json.loads(b"".join(chunks))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"body is not valid JSON: {exc}")
    if not isinstance(doc, dict) or not isinstance(doc.get("records"), list):
        raise HTTPException(status_code=400, detail="body must be a worldline.bundle.v1 object with a records[] array")
    try:
        return Bundle.from_dict(doc)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"malformed bundle: {exc}")


def _records_summary(bundle: Bundle) -> list[dict[str, Any]]:
    return [{"index": i, "kind": r.kind.value, "producer": r.producer, "claim": r.claim}
            for i, r in enumerate(bundle.records)]


app = FastAPI(
    title="WORLDLINE public verifier",
    description="Verify sealed WORLDLINE evidence bundles and evaluate the release policy using only the kernel's public key.",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    max_age=3600,
)


@app.exception_handler(HTTPException)
async def _http_exc(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.get("/api/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "public_key": PUBLIC_KEY_PATH.is_file(),
            "data": DATA_DIR is not None, "viewer": UI_DIST is not None}


@app.get("/api/version")
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


@app.post("/api/verify")
async def verify(request: Request) -> dict[str, Any]:
    """Verify a bundle: chain integrity, checkpoint coverage and Ed25519 signature against the kernel public key."""
    bundle = await _read_bundle(request)
    pem = _public_key_pem()
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


@app.post("/api/authorize")
async def authorize(
    request: Request,
    artifact_digest: str = Query(..., min_length=1, max_length=256,
                                 description="Artifact digest the release is about, e.g. the git commit SHA."),
    policy: str = Query(DEFAULT_POLICY, description="Policy name (file stem under policies/)."),
    now: str | None = Query(None, description="ISO-8601 time to evaluate at (default: now). "
                                              "Evidence older than the policy's max_age_s is stale."),
) -> dict[str, Any]:
    """Release gate: AUTHORIZED or DENIED from the bundle, the named policy and the kernel public key."""
    pol = load_policy(_policy_path(policy))
    bundle = await _read_bundle(request)
    now_iso = now or datetime.now(timezone.utc).isoformat()
    try:
        auth = kernel_authorize(bundle, pol, artifact_digest, _public_key_pem(), now_iso)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"bad `now`: {exc}")
    return auth.to_dict()


@app.get("/api/evidence")
def evidence() -> dict[str, Any]:
    """The bundle and authorization this deployment ships (the data the viewer renders)."""
    if DATA_DIR is None:
        raise HTTPException(status_code=404, detail="no evidence data baked into this deployment")
    out: dict[str, Any] = {"source": str(DATA_DIR)}
    for name in EVIDENCE_FILES:
        p = DATA_DIR / name
        key = name[: -len(".json")]
        try:
            out[key] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
        except ValueError:
            out[key] = None
    return out


# ------------------------------------------------------------------ viewer (static)
if UI_DIST is not None:
    if DATA_DIR is not None and not (UI_DIST / "data").is_dir():
        app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIST / "index.html")

    app.mount("/", StaticFiles(directory=str(UI_DIST), html=True), name="viewer")
