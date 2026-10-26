"""FailureGenome: extraction, serialization, and challenge on a second scenario and its immune twin."""
from __future__ import annotations

from pathlib import Path

import pytest

from worldline.core import EvidenceKind, Verdict
from worldline.explorer.explore import minimize, rule_evaluator
from worldline.genome.genome import (
    ChallengeResult, FailureGenome, MechanismStep, StructuralSelector, challenge, extract,
)
from worldline.interventions import Intervention
from worldline.invariants import check_all, load_invariants
from worldline.worlds import load_scenario, run

S = 1_000_000_000
ROOT = Path(__file__).resolve().parents[1]
TARGET = "decision.consistent_with_truth"


@pytest.fixture(scope="module")
def inv():
    return load_invariants(ROOT / "invariants")


@pytest.fixture(scope="module")
def port():
    return load_scenario(ROOT / "scenarios" / "port_alert.yaml")


@pytest.fixture(scope="module")
def convoy():
    return load_scenario(ROOT / "scenarios" / "convoy_lostlink.yaml")


@pytest.fixture(scope="module")
def immune():
    return load_scenario(ROOT / "scenarios" / "convoy_lostlink_immune.yaml")


def _extract(port, inv):
    sched = (
        Intervention("delay", {"event_id": "correction-001", "role": "harbour_ops"}, {"by_ns": 300 * S}),
        Intervention("revoke", {}, {"role": "coast_guard", "from_ns": 3000 * S}),
        Intervention("clock_skew", {}, {"source_id": "tide_gauge", "offset_ns": 5 * S}),
        Intervention("clock_skew", {}, {"source_id": "met_office", "offset_ns": 10 * S}),
    )
    m = minimize(port, inv, sched, TARGET)
    minimal = [Intervention.from_dict(d) for d in m.minimal]
    tr = run(port, tuple(minimal))
    vs = check_all(tr, {TARGET: inv[TARGET]}, scenario=port, evaluate_rule=rule_evaluator(port), now_ns=0)
    v = next(x for x in vs if x.result == Verdict.FAIL)
    return extract(tr, minimal, v, port), tr, minimal, v


@pytest.fixture(scope="module")
def genome(port, inv):
    return _extract(port, inv)[0]


# ---------------------------------------------------------------- extract

def test_extract_shape(port, inv):
    g, tr, minimal, v = _extract(port, inv)
    assert g.violated_invariant == TARGET
    assert len(g.mechanism) == 1
    step = g.mechanism[0]
    assert step.op == "delay"
    assert step.selector.to_dict() == {"kind": "correction", "relation": "corrects_dependency",
                                       "position": "last_before_decision", "role": "decision_role"}
    assert step.params["until"] == "after_decision" and step.params["margin_ns"] > 0
    assert "correction-001" not in str(step.to_dict()), "mechanism must not name concrete event ids"
    assert "decision_rule_depends_on_a_corrected_kind" in g.preconditions
    assert "has_decision_rule" in g.preconditions
    assert "event_received_by_decision_role_before_decision" in g.preconditions
    assert g.preconditions == sorted(set(g.preconditions))
    assert g.witness_shape["decision_rule"] == "escalate_or_monitor"
    assert g.witness_shape["decision_actor"] == "duty_officer"
    assert g.witness_shape["n_witness_events"] == len(v.witness)
    assert g.origin["scenario_id"] == "port_alert" and g.origin["trace_digest"] == tr.digest
    assert g.origin["witness"] == v.witness and g.origin["branch_id"] == tr.branch_id
    assert g.evidence_kind == EvidenceKind.EMPIRICALLY_VALIDATED
    assert g.id.startswith("genome:")


def test_extract_id_stable(port, inv):
    a, b = _extract(port, inv)[0], _extract(port, inv)[0]
    assert a.id == b.id and a.to_dict() == b.to_dict()


def test_id_depends_on_mechanism_not_origin(genome):
    d = genome.to_dict()
    d["origin"] = {"scenario_id": "elsewhere"}
    d["id"] = ""
    assert FailureGenome.from_dict(d).id == genome.id
    d["preconditions"] = d["preconditions"] + ["exists_kind:extra"]
    assert FailureGenome.from_dict(d).id != genome.id


def test_round_trip(genome):
    d = genome.to_dict()
    back = FailureGenome.from_dict(d)
    assert back == genome
    assert back.to_dict() == d
    assert d["evidence_kind"] == "EMPIRICALLY_VALIDATED"


def test_claim_ceiling(genome):
    assert "not across heterogeneous codebases" in genome.claim_ceiling
    assert "not across heterogeneous codebases" in FailureGenome.claim_ceiling


# ---------------------------------------------------------------- challenge

def test_challenge_reproduces_on_convoy(genome, convoy, inv):
    r = challenge(genome, convoy, inv)
    assert isinstance(r, ChallengeResult)
    assert r.genome_id == genome.id and r.scenario_id == "convoy_lostlink"
    assert r.preconditions_met is True and all(r.preconditions_report.values())
    assert set(r.preconditions_report) == set(genome.preconditions)
    assert r.reproduced is True
    assert r.target_rule == "reroute_or_continue"
    assert len(r.instantiated_schedule) == 1
    iv = r.instantiated_schedule[0]
    assert iv["op"] == "delay"
    assert iv["target"] == {"event_id": "retract-001", "role": "ops_center"}
    assert iv["params"]["role"] == "ops_center" and iv["params"]["by_ns"] > 0
    fails = [v for v in r.verdicts if v["result"] == Verdict.FAIL.value]
    assert [v["invariant_id"] for v in fails] == [TARGET]
    assert "retract-001" in fails[0]["witness"]
    replay = run(convoy, tuple(Intervention.from_dict(d) for d in r.instantiated_schedule))
    assert r.counterexample_digest == replay.digest
    assert r.explanation.startswith("reproduced on convoy_lostlink")
    assert r.to_dict()["reproduced"] is True


def test_challenge_immune_variant_not_reproduced(genome, immune, inv):
    r = challenge(genome, immune, inv)
    assert r.scenario_id == "convoy_lostlink_immune"
    assert r.preconditions_met is True
    assert r.reproduced is False and r.counterexample_digest is None
    assert r.target_rule == "reroute_or_continue"
    assert r.instantiated_schedule and r.instantiated_schedule[0]["target"]["event_id"] == "retract-001"
    assert all(v["result"] != Verdict.FAIL.value for v in r.verdicts)
    assert "not reproduced" in r.explanation or "no new" in r.explanation


def test_challenge_self_consistency(genome, port, inv):
    r = challenge(genome, port, inv)
    assert r.preconditions_met is True and r.reproduced is True
    assert r.target_rule == "escalate_or_monitor"
    assert r.instantiated_schedule[0]["target"] == {"event_id": "correction-001", "role": "harbour_ops"}


def test_challenge_deterministic(genome, convoy, inv):
    assert challenge(genome, convoy, inv).to_dict() == challenge(genome, convoy, inv).to_dict()


def test_challenge_unmet_precondition(genome, convoy, inv):
    g = FailureGenome(genome.violated_invariant, genome.mechanism,
                      genome.preconditions + ["exists_kind:nonexistent"], genome.witness_shape, genome.origin)
    assert g.id != genome.id
    r = challenge(g, convoy, inv)
    assert r.preconditions_met is False
    assert r.preconditions_report["exists_kind:nonexistent"] is False
    assert r.verdicts == [] and r.reproduced is False and r.counterexample_digest is None
    assert "not instantiated" in r.explanation and "preconditions_met=False" in r.explanation


def test_challenge_unknown_precondition_is_false(genome, convoy, inv):
    g = FailureGenome(TARGET, genome.mechanism, ["has_decision_rule", "made_up_precondition"], {}, {})
    r = challenge(g, convoy, inv)
    assert r.preconditions_report == {"has_decision_rule": True, "made_up_precondition": False}
    assert r.preconditions_met is False and r.verdicts == []


def test_challenge_selector_with_no_candidate(convoy, inv):
    step = MechanismStep("delay", StructuralSelector("unicorn", "any_of_kind", "last_before_decision"),
                         {"until": "after_decision", "margin_ns": S})
    g = FailureGenome(TARGET, [step], ["has_decision_rule"], {}, {})
    r = challenge(g, convoy, inv)
    assert r.preconditions_met is True
    assert r.instantiated_schedule == [] and r.target_rule is None
    assert r.reproduced is False and r.verdicts == []
    assert "no event for selector" in r.explanation
