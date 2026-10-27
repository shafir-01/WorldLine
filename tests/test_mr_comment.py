"""Offline tests for scripts/mr_comment.py: Markdown rendering and idempotent upserts (no network)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("mr_comment", ROOT / "scripts" / "mr_comment.py")
mc = importlib.util.module_from_spec(_spec)
sys.modules["mr_comment"] = mc
_spec.loader.exec_module(mc)

SHA = "f9cd9c8926530d76b139c3f85433c2ffc65e3d52"
REAL_OUT = ROOT / "experiments" / "out"


def _verdict(inv: str, result: str, witness=(), missing=()) -> dict:
    return {"invariant_id": inv, "invariant_version": "1.0", "result": result, "witness": list(witness),
            "missing_data": list(missing), "explanation": "x", "trace_digest": "sha256:d", "derived_at_ns": 0}


def write_fixture(out: Path, *, authorization: dict | None = None) -> Path:
    """A small experiments/out with the same shapes the CLI writes."""
    (out / "verdicts").mkdir(parents=True)
    minimal = {
        "scenario_id": "port_alert", "scenario_path": "scenarios\\port_alert.yaml",
        "target_invariant": "decision.consistent_with_truth", "one_minimal_verified": True,
        "original": [{"op": "duplicate", "params": {"count": 1}, "target": {"event_id": "ack-001"}},
                     {"op": "delay", "params": {"by_ns": 300000000000}, "target": {"event_id": "correction-001"}}],
        "minimal": [{"op": "delay", "params": {"by_ns": 300000000000}, "target": {"event_id": "correction-001"}}],
        "witness": ["correction-001", "ais-006", "patrol-001"],
        "explanation": "decision chose 'escalate' but truth gives 'monitor'",
        "trace_digest": "sha256:6ede77e3",
        "trace_files": {"minimal": "traces/minimal.json", "minimal_verdicts": "verdicts/minimal.json"},
        "causal_diff": {"decisions": [{"actor": "duty_officer", "option_a": "monitor", "option_b": "escalate",
                                       "rule_id": "escalate_or_monitor"}]},
    }
    genome = {"id": "genome:94f0ad69dd5483d0", "evidence_kind": "EMPIRICALLY_VALIDATED",
              "violated_invariant": "decision.consistent_with_truth",
              "mechanism_sentence": "delay the last correction seen by the decision role until after the decision."}
    challenge = {"success": True, "primary": {"scenario_id": "convoy_lostlink", "reproduced": True},
                 "controls": [{"scenario_id": "convoy_lostlink_immune", "reproduced": False}]}
    index = {"claim_ceiling": "EMPIRICALLY_VALIDATED on synthetic scenarios; ATTESTED only after kernel recomputation",
             "code_commit": SHA}
    files = {"minimal_schedule.json": minimal, "genome.json": genome, "challenge.json": challenge, "index.json": index,
             "verdicts/root.json": [_verdict("decision.consistent_with_truth", "PASS"),
                                    _verdict("response.alert_ack", "PASS")],
             "verdicts/minimal.json": [_verdict("decision.consistent_with_truth", "FAIL", minimal["witness"]),
                                       _verdict("response.alert_ack", "UNKNOWN", missing=["ack"])]}
    if authorization is not None:
        files["authorization.json"] = authorization
        files["bundle.json"] = {"checkpoint": {"root": "sha256:ed4d", "signer_key_id": "ed25519:9488"}}
    for name, doc in files.items():
        (out / name).write_text(json.dumps(doc), encoding="utf-8")
    return out


AUTHORIZED = {"decision": "AUTHORIZED", "artifact_digest": SHA, "policy": "release",
              "reasons": ["all 5 requirement(s) met by 15 record(s)"],
              "satisfied": [{"claim": "tests:unit", "claim_pattern": "tests:unit"}], "missing": []}
DENIED = {"decision": "DENIED", "artifact_digest": SHA, "policy": "release",
          "reasons": ["integrity:record_edited@1: record 1 content does not match its hash"],
          "satisfied": [], "missing": ["invariant:*"]}
CTX = {"commit_sha": SHA, "pipeline_url": "https://gitlab.com/p/-/pipelines/1",
       "job_url": "https://gitlab.com/p/-/jobs/2", "seed": "42", "budget": "200"}


# ---------------------------------------------------------------- plain English

@pytest.mark.parametrize("iv, expected", [
    ({"op": "delay", "target": {"event_id": "correction-001"}, "params": {"by_ns": 300000000000}},
     "Delay `correction-001` by 300 s."),
    ({"op": "delay", "target": {"event_id": "e1"}, "params": {"by_ns": 5000000000, "role": "harbour_ops"}},
     "Delay `e1` for role `harbour_ops` by 5 s."),
    ({"op": "drop", "target": {"event_id": "e1"}, "params": {}}, "Drop `e1` for every role."),
    ({"op": "duplicate", "target": {"event_id": "ack-001"}, "params": {"count": 2}}, "Deliver `ack-001` 2 extra times."),
    ({"op": "revoke", "target": {"event_id": "e1"}, "params": {"role": "coast_guard", "from_ns": 60000000000}},
     "Revoke `e1` from role `coast_guard` from 60 s on."),
    ({"op": "clock_skew", "target": {}, "params": {"source_id": "ais", "offset_ns": -2000000000}},
     "Skew the clock of source `ais` by -2 s."),
    ({"op": "corrupt", "target": {"event_id": "s1"}, "params": {"field": "payload.confidence", "value": 0.1}},
     "Corrupt `s1`: set `payload.confidence` to `0.1`."),
])
def test_describe_intervention(iv, expected):
    assert mc.describe_intervention(iv) == expected


def test_reorder_mentions_event_and_shift():
    s = mc.describe_intervention({"op": "reorder", "target": {"event_id": "sighting-001"}, "params": {"by_ns": 300000000001}})
    assert s.startswith("Reorder `sighting-001`") and "later" in s


# ---------------------------------------------------------------- MR note

def test_mr_note_authorized(tmp_path):
    ev = mc.load_evidence(write_fixture(tmp_path, authorization=AUTHORIZED))
    note = mc.render_mr_note(ev, CTX)
    assert note.splitlines()[0] == mc.MR_MARKER
    assert "**Kernel decision: AUTHORIZED**" in note
    assert "sha256:ed4d" in note
    assert "| `decision.consistent_with_truth@1.0` | PASS | **FAIL** | `correction-001`, `ais-006`, `patrol-001` |" in note
    assert "| `response.alert_ack@1.0` | PASS | **UNKNOWN** | missing: ack |" in note
    assert "1. Delay `correction-001` by 300 s." in note
    assert "Scenario `scenarios/port_alert.yaml`" in note and "2 -> 1 intervention(s)" in note
    assert "`genome:94f0ad69dd5483d0` (EMPIRICALLY_VALIDATED)" in note
    assert "challenge passed: reproduced on `convoy_lostlink`; did not reproduce on control `convoy_lostlink_immune`" in note
    assert "https://gitlab.com/p/-/pipelines/1" in note
    assert "https://gitlab.com/p/-/jobs/2/artifacts/browse/experiments/out/" in note
    assert note.rstrip().splitlines()[-1].startswith("Claim ceiling: EMPIRICALLY_VALIDATED")


def test_mr_note_denied_lists_reasons(tmp_path):
    note = mc.render_mr_note(mc.load_evidence(write_fixture(tmp_path, authorization=DENIED)), CTX)
    assert "**Kernel decision: DENIED**" in note
    assert "record 1 content does not match its hash" in note
    assert "missing: `invariant:*`" in note


def test_mr_note_without_kernel(tmp_path):
    note = mc.render_mr_note(mc.load_evidence(write_fixture(tmp_path)), CTX)
    assert mc.NO_KERNEL in note
    assert "Kernel decision: AUTHORIZED" not in note


def test_stale_authorization_is_not_shown(tmp_path):
    stale = dict(AUTHORIZED, artifact_digest="0" * 40)
    note = mc.render_mr_note(mc.load_evidence(write_fixture(tmp_path, authorization=stale)), CTX)
    assert mc.NO_KERNEL in note and "AUTHORIZED" not in note


def test_mr_note_on_empty_output(tmp_path):
    note = mc.render_mr_note(mc.load_evidence(tmp_path), {})
    assert mc.NO_KERNEL in note
    assert "No minimal schedule" in note and "not extracted" in note
    assert "### Links" not in note


# ---------------------------------------------------------------- issue

def test_issue_title_body_and_contract(tmp_path):
    ev = mc.load_evidence(write_fixture(tmp_path))
    title, body = mc.render_issue(ev, CTX)
    assert title == "Investigate: decision.consistent_with_truth FAIL on port_alert (genome:94f0ad69dd5483d0)"
    assert body.splitlines()[0] == mc.issue_marker(ev)
    assert "`duty_officer` chose `escalate` where truth gives `monitor`" in body
    assert "uv run worldline explore scenarios/port_alert.yaml --budget 200 --seed 42" in body
    boxes = [l for l in body.splitlines() if l.startswith("- [ ] ")]
    assert len(boxes) == len(mc.REPAIR_CONTRACT) == 8
    assert any("product copy" in b for b in boxes)
    assert any("Non-vacuous" in b for b in boxes)
    assert "ATTESTED" not in "\n".join(b for b in boxes if "never" not in b)


def test_investigate_request_mentions_account():
    msg = mc.investigate_request("ai-worldline-investigate-shafir", "scenarios/port_alert.yaml", "42", "200")
    assert msg.startswith("@ai-worldline-investigate-shafir investigate scenarios/port_alert.yaml seed 42 budget 200")
    assert "@" not in mc.investigate_request(None, "s.yaml", "1", "2")


# ---------------------------------------------------------------- idempotent upserts

class FakeGitLab(mc.GitLab):
    def __init__(self, notes=None, issues=None, members=None):
        super().__init__("https://gitlab.example/api/v4", "1", "t", "PRIVATE-TOKEN")
        self.notes, self.issues, self.members = notes or [], issues or [], members or []
        self.calls: list[tuple[str, str, dict | None]] = []

    def call(self, method, path, body=None, query=None):
        self.calls.append((method, path, body))
        if method == "GET" and path.endswith("/notes"):
            return self.notes if (query or {}).get("page", 1) == 1 else []
        if method == "GET" and path == "issues":
            return self.issues
        if method == "GET" and path == "members/all":
            return self.members
        if method == "POST" and path.endswith("/notes"):
            note = {"id": 100 + len(self.notes), "body": body["body"]}
            self.notes.append(note)
            return note
        if method == "POST" and path == "issues":
            return {"iid": 7, "web_url": "https://gitlab.example/i/7"}
        return {}


def test_mr_note_created_then_updated():
    gl = FakeGitLab(notes=[{"id": 1, "body": "human comment"}])
    assert mc.upsert_mr_note(gl, "5", mc.MR_MARKER + "\nfirst") == "created note 101"
    assert mc.upsert_mr_note(gl, "5", mc.MR_MARKER + "\nsecond") == "updated note 101"
    writes = [(m, p) for m, p, _ in gl.calls if m != "GET"]
    assert writes == [("POST", "merge_requests/5/notes"), ("PUT", "merge_requests/5/notes/101")]


def test_issue_created_with_label_and_mention(tmp_path, monkeypatch):
    monkeypatch.delenv("WORLDLINE_INVESTIGATE_ACCOUNT", raising=False)
    ev = mc.load_evidence(write_fixture(tmp_path))
    gl = FakeGitLab(members=[{"username": "ai-worldline-investigate-shafir"}])
    res = mc.upsert_issue(gl, ev, CTX)
    assert res.startswith("created issue #7")
    post = next(b for m, p, b in gl.calls if m == "POST" and p == "issues")
    assert post["labels"] == mc.INVESTIGATE_LABEL
    mention = next(b for m, p, b in gl.calls if m == "POST" and p == "issues/7/notes")
    assert mention["body"].startswith("@ai-worldline-investigate-shafir ")


def test_issue_updated_when_marker_matches(tmp_path, monkeypatch):
    monkeypatch.delenv("WORLDLINE_RETRIGGER", raising=False)
    ev = mc.load_evidence(write_fixture(tmp_path))
    gl = FakeGitLab(issues=[{"iid": 3, "description": "old\n" + mc.issue_marker(ev)},
                            {"iid": 4, "description": "unrelated"}])
    assert mc.upsert_issue(gl, ev, CTX) == "updated issue #3"
    methods = [(m, p) for m, p, _ in gl.calls if m != "GET"]
    assert methods == [("PUT", "issues/3"), ("POST", "issues/3/notes")]
    note = gl.calls[-1][2]["body"]
    assert "@" not in note  # no re-trigger unless WORLDLINE_RETRIGGER=1


def test_main_dry_run_prints_without_network(tmp_path, capsys, monkeypatch):
    for k in ("WORLDLINE_BOT_TOKEN", "CI_JOB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    write_fixture(tmp_path)
    saved = tmp_path / "note.md"
    assert mc.main(["mr", "--out", str(tmp_path), "--dry-run", "--save", str(saved)]) == 0
    assert capsys.readouterr().out == saved.read_text(encoding="utf-8")


# ---------------------------------------------------------------- real pipeline output, when present

@pytest.mark.skipif(not (REAL_OUT / "minimal_schedule.json").exists(), reason="experiments/out not generated")
def test_real_experiments_out_renders():
    ev = mc.load_evidence(REAL_OUT)
    auth = ev.get("authorization") or {}
    note = mc.render_mr_note(ev, {"commit_sha": auth.get("artifact_digest", "")})
    ms = ev["minimal_schedule"]
    assert mc.MR_MARKER in note
    for line in mc.describe_schedule(ms["minimal"]):
        assert line in note
    if auth:
        assert f"**Kernel decision: {auth['decision']}**" in note
    title, body = mc.render_issue(ev, {})
    assert ms["target_invariant"] in title
    if ev.get("genome"):
        assert ev["genome"]["id"] in title
