"""Tests for the public verifier service (service/app.py).

Uses the real pipeline output in experiments/out when present; otherwise generates it once with
`worldline demo --budget 40 --skip-tests`. The service only ever sees the public key.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
import httpx  # noqa: E402

from worldline.evidence import RECORD_EDITED, key_id  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "experiments" / "out"
PUBLIC_KEY = REPO / "policies" / "kernel_public.pem"


@pytest.fixture(scope="module")
def out_dir() -> Path:
    if not (OUT / "bundle.json").is_file() or not (OUT / "index.json").is_file():
        subprocess.run([sys.executable, "-m", "worldline.cli.main", "demo", "--budget", "40",
                        "--out", str(OUT), "--skip-tests"], cwd=str(REPO), check=True)
    assert (OUT / "bundle.json").is_file()
    return OUT


@pytest.fixture(scope="module")
def client() -> TestClient:
    from service.app import app
    return TestClient(app)


@pytest.fixture(scope="module")
def bundle(out_dir: Path) -> dict:
    return json.loads((out_dir / "bundle.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def digest(out_dir: Path) -> str:
    return json.loads((out_dir / "index.json").read_text(encoding="utf-8"))["code_commit"]


def test_healthz(client: TestClient) -> None:
    r = client.get("/api/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["public_key"] is True


def test_version_reports_public_key_id_and_policy(client: TestClient) -> None:
    body = client.get("/api/version").json()
    assert body["kernel_public_key_id"] == key_id(PUBLIC_KEY.read_text(encoding="utf-8"))
    assert body["policy"] == "release"
    assert body["git_commit"]


def test_verify_ok_on_pipeline_bundle(client: TestClient, bundle: dict) -> None:
    r = client.post("/api/verify", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["findings"] == []
    assert body["root"] == bundle["checkpoint"]["root"]
    assert body["signer_key_id"] == bundle["checkpoint"]["signer_key_id"] == body["verifier_key_id"]
    assert len(body["records"]) == len(bundle["records"])
    assert {"kind", "producer", "claim"} <= set(body["records"][0])


def test_verify_detects_tampering(client: TestClient, bundle: dict) -> None:
    tampered = copy.deepcopy(bundle)
    tampered["records"][0]["result"] = {**tampered["records"][0]["result"], "tampered_by": "test"}
    body = client.post("/api/verify", json=tampered).json()
    assert body["ok"] is False
    assert RECORD_EDITED in [f["code"] for f in body["findings"]]


def test_verify_rejects_unsealed_and_non_json(client: TestClient) -> None:
    assert client.post("/api/verify", json={"records": [], "links": []}).json()["ok"] is False
    assert client.post("/api/verify", content="not json", headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/api/verify", content="x=1", headers={"content-type": "text/plain"}).status_code == 415


def test_authorize_right_digest(client: TestClient, bundle: dict, digest: str) -> None:
    # Evaluate at seal time so the test does not depend on the policy's max_age_s window.
    now = bundle["checkpoint"]["sealed_at"]
    r = client.post("/api/authorize", params={"artifact_digest": digest, "now": now}, json=bundle)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"] == "AUTHORIZED", body["reasons"]
    assert body["policy"] == "release" and body["artifact_digest"] == digest
    assert body["missing"] == []


def test_authorize_wrong_digest_denied(client: TestClient, bundle: dict) -> None:
    now = bundle["checkpoint"]["sealed_at"]
    body = client.post("/api/authorize", params={"artifact_digest": "deadbeef", "now": now}, json=bundle).json()
    assert body["decision"] == "DENIED"
    assert any(r.startswith("subject_mismatch") or r.startswith("missing") for r in body["reasons"])


def test_authorize_unknown_policy(client: TestClient, bundle: dict) -> None:
    r = client.post("/api/authorize", params={"artifact_digest": "x", "policy": "../kernel_private"}, json=bundle)
    assert r.status_code == 400


def test_oversize_body_is_413(client: TestClient) -> None:
    from service.app import MAX_BODY_BYTES
    big = b'{"records": [], "pad": "' + b"a" * (MAX_BODY_BYTES + 1) + b'"}'
    r = client.post("/api/verify", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_evidence_endpoint(client: TestClient) -> None:
    body = client.get("/api/evidence").json()
    assert body["bundle"]["format"] == "worldline.bundle.v1"
    assert body["authorization"]["decision"] in ("AUTHORIZED", "DENIED")


def test_cors_allows_any_origin(client: TestClient) -> None:
    r = client.options("/api/verify", headers={"Origin": "https://example.org",
                                               "Access-Control-Request-Method": "POST"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"


# ---------------------------------------------------------------- production hardening


@pytest.fixture(autouse=True)
def _fresh_limits() -> None:
    """Every test starts with full rate-limit buckets and an empty release cache."""
    from service import release
    from service.app import app
    app.state.limiter.reset()
    release.clear_cache()


def test_security_headers_on_viewer_and_api(client: TestClient) -> None:
    for path in ("/api/version", "/api/healthz", "/"):
        r = client.get(path)
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "connect-src 'self'" in csp
        assert "https://fonts.googleapis.com" in csp and "https://fonts.gstatic.com" in csp
        assert "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in r.headers["permissions-policy"]
        assert "strict-transport-security" not in r.headers  # plain http
    r = client.get("/api/version", headers={"x-forwarded-proto": "https"})
    assert r.headers["strict-transport-security"].startswith("max-age=")


def test_docs_and_openapi(client: TestClient) -> None:
    r = client.get("/api/docs")
    assert r.status_code == 200 and "swagger" in r.text.lower()
    assert "https://cdn.jsdelivr.net" in r.headers["content-security-policy"]
    spec = client.get("/api/openapi.json").json()
    assert spec["info"]["title"] == "WORLDLINE public verifier"
    for path in ("/api/verify", "/api/authorize", "/api/verify/release", "/api/readyz"):
        assert path in spec["paths"], path
    verify = spec["paths"]["/api/verify"]["post"]
    assert verify["requestBody"]["content"]["application/json"]["example"]["format"] == "worldline.bundle.v1"
    assert "tampered" in verify["responses"]["200"]["content"]["application/json"]["examples"]
    auth = spec["paths"]["/api/authorize"]["post"]
    assert "denied" in auth["responses"]["200"]["content"]["application/json"]["examples"]
    assert set(spec["paths"]["/api/verify/release"]) == {"get", "post"}


def test_cache_headers(client: TestClient) -> None:
    from service.app import UI_DIST
    assert client.get("/api/version").headers["cache-control"] == "no-store"
    assert client.get("/api/evidence").headers["cache-control"] == "no-cache"  # revalidate via ETag
    if UI_DIST is None:
        pytest.skip("ui/dist not built: static cache headers not testable")
    asset = next((UI_DIST / "assets").iterdir())
    assert client.get(f"/assets/{asset.name}").headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/data/index.json").headers["cache-control"] == "public, max-age=60"
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_cors_only_on_api(client: TestClient) -> None:
    origin = {"Origin": "https://example.org"}
    assert client.get("/api/version", headers=origin).headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-origin" not in client.get("/", headers=origin).headers


def test_request_id_echoed_or_generated(client: TestClient) -> None:
    r = client.get("/api/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    generated = client.get("/api/healthz").headers["x-request-id"]
    assert len(generated) == 32 and generated != "abc-123"
    # Garbage (spaces, markup, too long) is replaced, never reflected.
    assert client.get("/api/healthz", headers={"X-Request-ID": "a b<script>"}).headers["x-request-id"] != "a b<script>"
    assert len(client.get("/api/healthz", headers={"X-Request-ID": "x" * 500}).headers["x-request-id"]) == 32
    # Errors carry it too.
    assert client.post("/api/verify", content="x", headers={"content-type": "text/plain",
                                                           "X-Request-ID": "err-1"}).headers["x-request-id"] == "err-1"


def test_access_log_is_structured_and_bodyless(client: TestClient, bundle: dict) -> None:
    from service.middleware import ACCESS_LOGGER, JsonFormatter

    seen: list[logging.LogRecord] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    lg = logging.getLogger(ACCESS_LOGGER)
    h = Grab(level=logging.DEBUG)
    lg.addHandler(h)
    try:
        client.post("/api/verify", json=bundle, headers={"X-Request-ID": "log-test-1"})
    finally:
        lg.removeHandler(h)
    rec = next(r for r in seen if r.json_fields["request_id"] == "log-test-1")  # type: ignore[attr-defined]
    f = rec.json_fields  # type: ignore[attr-defined]
    assert f["method"] == "POST" and f["path"] == "/api/verify" and f["status"] == 200
    assert f["latency_ms"] >= 0 and f["httpRequest"]["status"] == 200
    line = JsonFormatter().format(rec)
    doc = json.loads(line)
    assert doc["request_id"] == "log-test-1" and doc["severity"] == "INFO"
    assert bundle["checkpoint"]["signature"] not in line  # no bodies in logs


def test_rate_limit_429_after_burst(client: TestClient) -> None:
    from service.app import RATE_LIMIT_PER_MIN
    ip = {"X-Forwarded-For": "203.0.113.7", "Origin": "https://example.org"}
    codes = [client.post("/api/verify", json={"records": []}, headers=ip).status_code
             for _ in range(RATE_LIMIT_PER_MIN)]
    assert set(codes) == {200}
    r = client.post("/api/verify", json={"records": []}, headers=ip)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert r.json()["error"].startswith("rate limit")
    assert r.headers["access-control-allow-origin"] == "*"  # browsers can read the 429
    assert r.headers["x-request-id"]
    # Another client is unaffected; ordinary GETs are never limited.
    other = {"X-Forwarded-For": "198.51.100.9"}
    assert client.post("/api/verify", json={"records": []}, headers=other).status_code == 200
    assert client.get("/api/version", headers=ip).status_code == 200


def test_rate_limit_uses_rightmost_forwarded_hop(client: TestClient) -> None:
    from service.app import RATE_LIMIT_PER_MIN
    # A client cannot dodge the limit by prepending fake addresses to X-Forwarded-For.
    for i in range(RATE_LIMIT_PER_MIN):
        client.post("/api/verify", json={"records": []}, headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.50"})
    r = client.post("/api/verify", json={"records": []}, headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.50"})
    assert r.status_code == 429


def test_token_bucket_refills() -> None:
    from service.middleware import TokenBucket
    t = [0.0]
    b = TokenBucket(2, 60.0, clock=lambda: t[0])
    assert b.take("a") == 0 and b.take("a") == 0
    assert b.take("a") == pytest.approx(30.0)
    assert b.take("b") == 0
    t[0] = 30.0
    assert b.take("a") == 0


def test_json_depth_guard(client: TestClient) -> None:
    from service.app import json_depth_exceeds
    deep = '{"records": [], "x": ' + "[" * 5000 + "]" * 5000 + "}"
    r = client.post("/api/verify", content=deep, headers={"content-type": "application/json"})
    assert r.status_code == 400 and "deeper" in r.json()["error"]
    # Brackets inside strings do not count.
    assert not json_depth_exceeds(b'{"a": "' + b"[" * 200 + b'\\" [[["}')
    assert json_depth_exceeds(b"[" * 65 + b"]" * 65)
    assert not json_depth_exceeds(b"[" * 64 + b"]" * 64)


def test_readyz_ok(client: TestClient) -> None:
    r = client.get("/api/readyz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["public_key"].startswith("ed25519:") and body["checks"]["policy"] == "release"


def test_readyz_503_when_data_missing(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import service.app as svc
    monkeypatch.setattr(svc, "DATA_DIR", tmp_path)  # no index.json there
    r = client.get("/api/readyz")
    assert r.status_code == 503
    assert r.json()["status"] == "unavailable" and r.json()["checks"]["data"].startswith("error")
    monkeypatch.setattr(svc, "DATA_DIR", None)
    assert client.get("/api/readyz").status_code == 503
    assert client.get("/api/healthz").status_code == 200  # liveness is unaffected


def test_readyz_503_when_key_unparseable(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import service.app as svc
    (tmp_path / "kernel_public.pem").write_text("not a key", encoding="utf-8")
    monkeypatch.setattr(svc, "PUBLIC_KEY_PATH", tmp_path / "kernel_public.pem")
    r = client.get("/api/readyz")
    assert r.status_code == 503 and r.json()["checks"]["public_key"].startswith("error")


def test_evidence_etag_304(client: TestClient) -> None:
    r = client.get("/api/evidence")
    etag = r.headers["etag"]
    assert etag.startswith('"') and r.json()["bundle"]["format"] == "worldline.bundle.v1"
    r2 = client.get("/api/evidence", headers={"If-None-Match": etag})
    assert r2.status_code == 304 and r2.content == b"" and r2.headers["etag"] == etag
    assert client.get("/api/evidence", headers={"If-None-Match": f'W/{etag}, "other"'}).status_code == 304
    assert client.get("/api/evidence", headers={"If-None-Match": '"stale"'}).status_code == 200


# ---------------------------------------------------------------- /api/verify/release (network mocked)

TAG = "v0.1.3-transcend"
PKG = f"https://gitlab.com/api/v4/projects/88121109/packages/generic/worldline-evidence/{TAG}"
REL = f"https://gitlab.com/api/v4/projects/88121109/releases/{TAG}"


def _mock(monkeypatch: pytest.MonkeyPatch, routes: dict) -> list[str]:
    from service import release
    calls: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        calls.append(url)
        hit = routes.get(url)
        if callable(hit):
            return await hit(req)
        return hit if hit is not None else httpx.Response(404, json={"message": "404 Not Found"})

    monkeypatch.setattr(release, "TRANSPORT", httpx.MockTransport(handler))
    return calls


def test_release_verify_authorized(client: TestClient, monkeypatch: pytest.MonkeyPatch,
                                   out_dir: Path, bundle: dict, digest: str) -> None:
    calls = _mock(monkeypatch, {
        f"{PKG}/bundle.json": httpx.Response(200, content=(out_dir / "bundle.json").read_bytes()),
        REL: httpx.Response(200, json={"tag_name": TAG, "commit": {"id": digest}, "description": "notes"}),
    })
    r = client.post("/api/verify/release", params={"tag": TAG})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tag"] == TAG and body["bundle_url"] == f"{PKG}/bundle.json"
    assert body["artifact_digest"] == digest and body["artifact_digest_source"] == "release.commit"
    assert body["evaluated_at"] == bundle["checkpoint"]["sealed_at"]
    # Same shapes as /api/verify and /api/authorize.
    assert body["verify"] == client.post("/api/verify", json=bundle).json()
    assert body["verify"]["ok"] is True
    direct_auth = client.post("/api/authorize", params={"artifact_digest": digest, "now": body["evaluated_at"]},
                              json=bundle).json()
    assert body["authorize"]["decision"] == "AUTHORIZED" == direct_auth["decision"]
    assert set(body["authorize"]) == set(direct_auth)
    assert calls and all(u.startswith("https://gitlab.com/") for u in calls)
    # GET variant, served from the per-tag cache (no new upstream calls).
    n = len(calls)
    g = client.get("/api/verify/release", params={"tag": TAG})
    assert g.status_code == 200 and g.json()["authorize"]["decision"] == "AUTHORIZED"
    assert len(calls) == n


def test_release_commit_from_notes_or_index(client: TestClient, monkeypatch: pytest.MonkeyPatch,
                                            out_dir: Path, digest: str) -> None:
    from service import release
    raw = (out_dir / "bundle.json").read_bytes()
    _mock(monkeypatch, {f"{PKG}/bundle.json": httpx.Response(200, content=raw),
                        REL: httpx.Response(200, json={"description": f"- Commit: `{digest}`\n"})})
    assert client.get("/api/verify/release", params={"tag": TAG}).json()["artifact_digest_source"] == "release_notes"
    release.clear_cache()
    _mock(monkeypatch, {f"{PKG}/bundle.json": httpx.Response(200, content=raw),
                        f"{PKG}/index.json": httpx.Response(200, content=(out_dir / "index.json").read_bytes())})
    body = client.get("/api/verify/release", params={"tag": TAG}).json()
    assert body["artifact_digest_source"] == "index.json" and body["artifact_digest"] == digest
    release.clear_cache()
    _mock(monkeypatch, {f"{PKG}/bundle.json": httpx.Response(200, content=raw)})
    assert client.get("/api/verify/release", params={"tag": TAG}).status_code == 422
    r = client.get("/api/verify/release", params={"tag": TAG, "artifact_digest": "deadbeef"})
    assert r.status_code == 200 and r.json()["authorize"]["decision"] == "DENIED"
    assert r.json()["artifact_digest_source"] == "query"


def test_release_errors(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from service.app import MAX_BODY_BYTES
    _mock(monkeypatch, {})
    assert client.get("/api/verify/release", params={"tag": TAG}).status_code == 404
    assert client.get("/api/verify/release", params={"tag": "../../etc"}).status_code == 400
    assert client.get("/api/verify/release", params={"tag": TAG, "policy": "../kernel_private"}).status_code == 400
    # Redirects are followed only to allowlisted hosts.
    calls = _mock(monkeypatch, {f"{PKG}/bundle.json": httpx.Response(302, headers={"location": "https://evil.example/b"})})
    r = client.get("/api/verify/release", params={"tag": TAG})
    assert r.status_code == 502 and "allowlist" in r.json()["error"]
    assert not any("evil.example" in c for c in calls)
    # Upstream 5xx -> 502; oversize -> 413.
    _mock(monkeypatch, {f"{PKG}/bundle.json": httpx.Response(500)})
    assert client.get("/api/verify/release", params={"tag": TAG}).status_code == 502
    _mock(monkeypatch, {f"{PKG}/bundle.json": httpx.Response(200, content=b"x" * (MAX_BODY_BYTES + 1))})
    assert client.get("/api/verify/release", params={"tag": TAG}).status_code == 413


def test_release_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from service import release

    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json={})

    monkeypatch.setattr(release, "FETCH_TIMEOUT_S", 0.1)
    _mock(monkeypatch, {f"{PKG}/bundle.json": slow})
    assert client.get("/api/verify/release", params={"tag": TAG}).status_code == 504


def test_release_get_is_rate_limited(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from service.app import RATE_LIMIT_PER_MIN
    _mock(monkeypatch, {})
    ip = {"X-Forwarded-For": "203.0.113.99"}
    for _ in range(RATE_LIMIT_PER_MIN):
        client.get("/api/verify/release", params={"tag": TAG}, headers=ip)
    assert client.get("/api/verify/release", params={"tag": TAG}, headers=ip).status_code == 429


def test_release_host_allowlist() -> None:
    from service.release import ReleaseError, check_url
    check_url("https://gitlab.com/api/v4/x")
    for bad in ("http://gitlab.com/x", "https://gitlab.com.evil.example/x", "https://user:pw@gitlab.com/x",
                "https://169.254.169.254/latest"):
        with pytest.raises(ReleaseError):
            check_url(bad)
