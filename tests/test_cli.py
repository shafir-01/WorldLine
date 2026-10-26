"""End-to-end CLI tests: the whole chain in a temporary directory, with a small budget."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worldline.cli.main import app
from worldline.evidence import Bundle, EvidenceRecord

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = str(ROOT / "scenarios" / "port_alert.yaml")
CHALLENGE = str(ROOT / "scenarios" / "convoy_lostlink.yaml")
IMMUNE = str(ROOT / "scenarios" / "convoy_lostlink_immune.yaml")
INVARIANTS = str(ROOT / "invariants")
POLICY = str(ROOT / "policies" / "release.yaml")
COMMIT = "deadbeef" * 5
TARGET = "decision.consistent_with_truth"
JUNIT = ('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="3" failures="0" '
         'errors="0" skipped="0" time="0.1"><testcase classname="t" name="a" time="0.01"/></testsuite></testsuites>')

runner = CliRunner()


def invoke(*args: str):
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    return result


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def out(tmp_path_factory) -> Path:
    """Runs the agent-side chain once for the module: explore -> minimize -> extract -> challenge -> report."""
    out = tmp_path_factory.mktemp("out")
    invoke("explore", SCENARIO, "--budget", "20", "--seed", "42", "--out", out, "--invariants", INVARIANTS)
    invoke("minimize", out / "exploration.json", "--out", out, "--invariant", TARGET)
    invoke("genome", "extract", out / "minimal_schedule.json", "--out", out / "genome.json")
    invoke("genome", "challenge", out / "genome.json", CHALLENGE, "--seed", "42", "--out", out / "challenge.json",
           "--immune", IMMUNE, "--invariants", INVARIANTS)
    (out / "report.xml").write_text(JUNIT, encoding="utf-8")
    invoke("report", out, "--code-commit", COMMIT)
    return out


@pytest.fixture(scope="module")
def keys(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("keys")
    r = invoke("keygen", "--out-dir", d)
    assert "key id ed25519:" in r.output
    assert (d / "kernel_private.pem").exists() and (d / "kernel_public.pem").exists()
    return d


@pytest.fixture(scope="module")
def bundle(out: Path, keys: Path) -> Path:
    r = invoke("verify", out, "--signing-key", keys / "kernel_private.pem", "--public-key", keys / "kernel_public.pem",
               "--out", out / "bundle.json")
    assert "verification ok=True" in r.output
    return out / "bundle.json"


# ---------------------------------------------------------------- run

def test_run_writes_root_trace_and_verdicts(tmp_path):
    r = invoke("run", SCENARIO, "--out", tmp_path, "--invariants", INVARIANTS)
    assert "root trace digest sha256:" in r.output
    tr = load(tmp_path / "traces" / "root.json")
    vs = load(tmp_path / "verdicts" / "root.json")
    assert tr["branch_id"] == "root" and tr["digest"].startswith("sha256:")
    assert {v["invariant_id"] for v in vs} >= {TARGET, "epistemic.no_future_knowledge"}
    assert all(v["result"] == "PASS" for v in vs)


# ---------------------------------------------------------------- explore

def test_explore_outputs(out: Path):
    e = load(out / "exploration.json")
    assert set(e["runs"]) == {"guided", "random"} and e["budget"] == 20 and e["seed"] == 42
    for s, row in e["comparison"].items():
        assert set(row) == {"n_nodes", "n_failing", "failing_rate_per_100", "wall_ms", "first_decision_fail_index"}
        assert row["n_nodes"] == e["runs"][s]["n_nodes"]
    assert e["runs"]["guided"]["n_failing"] > 0
    # every failing branch has a trace + verdicts pair with a filesystem-safe name
    for bid, files in e["trace_files"].items():
        assert "/" not in Path(files["trace"]).name and ":" not in Path(files["trace"]).name
        assert (out / files["trace"]).exists() and (out / files["verdicts"]).exists()
    failing = {b for r in e["runs"].values() for b in r["failing"]}
    assert failing <= set(e["trace_files"])


def test_explore_rejects_unknown_strategy(tmp_path):
    r = runner.invoke(app, ["explore", SCENARIO, "--budget", "2", "--seed", "1", "--out", str(tmp_path),
                            "--strategy", "bogus"])
    assert r.exit_code == 1


# ---------------------------------------------------------------- minimize

def test_minimize_outputs(out: Path):
    m = load(out / "minimal_schedule.json")
    assert m["target_invariant"] == TARGET
    assert 1 <= len(m["minimal"]) <= len(m["original"])
    assert m["one_minimal_verified"] is True and m["oracle_calls"] >= 1
    assert m["witness"] and m["causal_diff"]["differs"] is True
    assert m["causal_diff"]["decisions"], "the minimal counterexample must change at least one decision"
    tr = load(out / "traces" / "minimal.json")
    assert tr["digest"] == m["trace_digest"]
    vs = {v["invariant_id"]: v for v in load(out / "verdicts" / "minimal.json")}
    assert vs[TARGET]["result"] == "FAIL" and vs[TARGET]["witness"] == m["witness"]


# ---------------------------------------------------------------- genome

def test_genome_extract_is_abstract(out: Path):
    g = load(out / "genome.json")
    assert g["id"].startswith("genome:") and g["violated_invariant"] == TARGET
    assert g["mechanism"] and all("event_id" not in json.dumps(step["selector"]) for step in g["mechanism"])
    assert "has_decision_rule" in g["preconditions"]
    assert "not across heterogeneous codebases" in g["claim_ceiling"]
    assert g["mechanism_sentence"].endswith(f"so that {TARGET} fails.")


def test_genome_challenge_outputs(out: Path):
    c = load(out / "challenge.json")
    assert c["primary"]["scenario_id"] == "convoy_lostlink"
    assert [x["scenario_id"] for x in c["controls"]] == ["convoy_lostlink_immune"]
    assert c["success"] == (c["primary"]["reproduced"] and not c["controls"][0]["reproduced"])
    if c["primary"]["reproduced"]:
        assert (out / "traces" / "challenge_convoy_lostlink.json").exists()
        assert load(out / "traces" / "challenge_convoy_lostlink.json")["digest"] == c["primary"]["counterexample_digest"]


# ---------------------------------------------------------------- report

def test_report_index_and_agent_evidence(out: Path):
    idx = load(out / "index.json")
    assert idx["code_commit"] == COMMIT and "exploration.json" in idx["files"]
    assert idx["digests"]["root_trace_digest"] == load(out / "traces" / "root.json")["digest"]
    recs = [EvidenceRecord.from_dict(d) for d in load(out / "agent_evidence.json")]
    claims = {r.claim: r for r in recs}
    assert all(r.producer == "agent:pipeline" and r.subject == COMMIT for r in recs)
    assert all(r.kind.value in ("EMPIRICALLY_VALIDATED", "FAILED", "UNKNOWN") for r in recs), "agents never attest"
    assert claims["tests:unit"].kind.value == "EMPIRICALLY_VALIDATED" and claims["tests:unit"].result["tests"] == 3
    assert claims[f"invariant:{TARGET}"].kind.value == "EMPIRICALLY_VALIDATED"
    assert claims[f"counterexample:invariant:{TARGET}"].kind.value == "FAILED"
    assert claims[f"counterexample:invariant:{TARGET}"].result["witness"]
    assert set(claims["explore:guided_vs_random"].result) == {"guided", "random"}
    assert "wall_ms" not in claims["explore:guided_vs_random"].result["guided"]
    assert claims["minimize:one_minimal"].result["one_minimal_verified"] is True
    assert "genome:challenge" in claims and "artifact:manifest" in claims
    assert "artifact:digest" not in claims, "artifact:digest is a kernel-only claim under the policy"


# ---------------------------------------------------------------- kernel

def test_verify_attests_recomputed_claims(bundle: Path, keys: Path):
    b = Bundle.from_json(bundle.read_text(encoding="utf-8"))
    vr = b.verify((keys / "kernel_public.pem").read_text(encoding="utf-8"))
    assert vr.ok and not vr.findings
    kernel = [r for r in b.records if r.producer == "kernel"]
    attested = {r.claim for r in kernel if r.kind.value == "ATTESTED"}
    assert {f"invariant:{TARGET}", f"counterexample:invariant:{TARGET}", "explore:guided_vs_random",
            "minimize:one_minimal", "genome:challenge", "artifact:digest", "tests:unit"} <= attested
    assert not any(r.claim == "integrity:agent_report_mismatch" for r in kernel)
    digest_rec = next(r for r in kernel if r.claim == "artifact:digest")
    assert digest_rec.subject == COMMIT and digest_rec.result["root_trace_digest"].startswith("sha256:")


def test_verify_accepts_pem_text_as_signing_key(out: Path, keys: Path, tmp_path):
    pem = (keys / "kernel_private.pem").read_text(encoding="utf-8")
    r = invoke("verify", out, "--signing-key", pem, "--public-key", keys / "kernel_public.pem",
               "--out", tmp_path / "bundle.json")
    assert "verification ok=True" in r.output


def test_authorize_authorized(bundle: Path, keys: Path, tmp_path):
    r = invoke("authorize", bundle, "--policy", POLICY, "--public-key", keys / "kernel_public.pem",
               "--artifact-digest", COMMIT, "--out", tmp_path / "auth.json")
    assert r.output.startswith("AUTHORIZED")
    a = load(tmp_path / "auth.json")
    assert a["decision"] == "AUTHORIZED" and not a["missing"] and a["artifact_digest"] == COMMIT


def test_authorize_wrong_artifact_denied(bundle: Path, keys: Path, tmp_path):
    r = runner.invoke(app, ["authorize", str(bundle), "--policy", POLICY, "--public-key", str(keys / "kernel_public.pem"),
                            "--artifact-digest", "other", "--out", str(tmp_path / "auth.json")])
    assert r.exit_code == 1 and "DENIED" in r.output
    assert any(x.startswith("subject_mismatch") for x in load(tmp_path / "auth.json")["reasons"])


def test_authorize_tampered_bundle_denied(bundle: Path, keys: Path, tmp_path):
    d = load(bundle)
    rec = next(r for r in d["records"] if r["claim"].startswith("invariant:"))
    rec["result"] = {**rec["result"], "tampered": True}
    tampered = tmp_path / "bundle_tampered.json"
    tampered.write_text(json.dumps(d), encoding="utf-8")
    r = runner.invoke(app, ["authorize", str(tampered), "--policy", POLICY, "--public-key", str(keys / "kernel_public.pem"),
                            "--artifact-digest", COMMIT, "--out", str(tmp_path / "denied.json")])
    assert r.exit_code == 1
    a = load(tmp_path / "denied.json")
    assert a["decision"] == "DENIED"
    assert any("record_edited" in x for x in a["reasons"]), a["reasons"]


def test_authorize_revokes_on_invalidated_assumption(bundle: Path, keys: Path, tmp_path):
    r = runner.invoke(app, ["authorize", str(bundle), "--policy", POLICY, "--public-key", str(keys / "kernel_public.pem"),
                            "--artifact-digest", COMMIT, "--out", str(tmp_path / "rev.json"),
                            "--invalidate-assumption", "synthetic scenario"])
    assert r.exit_code == 1
    a = load(tmp_path / "rev.json")
    assert a["decision"] == "DENIED" and any(x.startswith("assumption_invalidated") for x in a["reasons"])


def test_verify_flags_agent_mismatch(out: Path, keys: Path, tmp_path):
    """An agent that misreports a verdict gets a FAILED integrity record, and the gate denies."""
    import shutil
    forged = tmp_path / "forged"
    shutil.copytree(out, forged)
    recs = load(forged / "agent_evidence.json")
    target = next(r for r in recs if r["claim"] == f"counterexample:invariant:{TARGET}")
    target["result"] = {**target["result"], "result": "PASS", "witness": []}
    target["kind"] = "EMPIRICALLY_VALIDATED"
    target.pop("id")  # the agent re-signs its forged record with a consistent id
    target["id"] = EvidenceRecord.from_dict(target).id
    (forged / "agent_evidence.json").write_text(json.dumps(recs), encoding="utf-8")
    r = invoke("verify", forged, "--signing-key", keys / "kernel_private.pem", "--public-key", keys / "kernel_public.pem",
               "--out", forged / "bundle.json")
    assert "mismatches 1" in r.output
    r2 = runner.invoke(app, ["authorize", str(forged / "bundle.json"), "--policy", POLICY,
                             "--public-key", str(keys / "kernel_public.pem"), "--artifact-digest", COMMIT,
                             "--out", str(forged / "auth.json")])
    assert r2.exit_code == 1 and "integrity:agent_report_mismatch" in r2.output
