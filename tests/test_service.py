"""Tests for the public verifier service (service/app.py).

Uses the real pipeline output in experiments/out when present; otherwise generates it once with
`worldline demo --budget 40 --skip-tests`. The service only ever sees the public key.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

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
