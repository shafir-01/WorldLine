"""triage_board: a non-security specimen with the same latent defect class as port_alert
(a decision on stale information when a correction arrives late), different kinds, names
and timings; and the port_alert genome challenged against it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worldline.core import Verdict
from worldline.explorer.explore import decision_dependencies, minimize, rule_evaluator
from worldline.genome import FailureGenome, challenge, extract
from worldline.interventions import Intervention
from worldline.invariants import check_all, load_invariants
from worldline.worlds import load_scenario, run
from worldline.worlds.predicate import parse_predicate
from worldline.worlds.schema import validate_file

S = 1_000_000_000
ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scenarios" / "triage_board.yaml"
TARGET = "decision.consistent_with_truth"
GENOME_FIXTURE = ROOT / "experiments" / "out" / "genome.json"


@pytest.fixture(scope="module")
def inv():
    return load_invariants(ROOT / "invariants")


@pytest.fixture(scope="module")
def sc():
    return load_scenario(PATH)


@pytest.fixture(scope="module")
def root(sc):
    return run(sc)


def verdicts(trace, sc, inv):
    chosen = {k: v for k, v in inv.items() if k in sc.invariants}
    return {v.invariant_id: v for v in check_all(trace, chosen, scenario=sc, evaluate_rule=rule_evaluator(sc), now_ns=0)}


def options(trace) -> dict[str, str]:
    return {d.payload["rule_id"]: d.payload["option"] for d in trace.decisions}


def delay_correction(by_s: int):
    return (Intervention("delay", {"event_id": "correction-001", "role": "bed_manager"}, {"by_ns": by_s * S}),)


# ---------------------------------------------------------------- authoring contract

def test_validates_clean():
    r = validate_file(PATH, invariants=ROOT / "invariants")
    assert r.ok and r.warnings == [], r.messages()


def test_specimen_header_convention():
    text = PATH.read_text(encoding="utf-8")
    assert "\nspecimen: true\n" in text
    assert "# SPECIMEN: deliberately defective laboratory scenario. Frozen." in text
    assert text.index("# SPECIMEN:") < text.index("\nspecimen: true")


def test_shape(sc):
    assert [r.id for r in sc.roles] == ["triage_nurse", "bed_manager", "evaluator"]
    nurse, bed = sc.role("triage_nurse"), sc.role("bed_manager")
    assert bed.delivery.ordering == "network" and nurse.delivery.ordering == "fifo"
    assert bed.delivery.base_latency_ns > nurse.delivery.base_latency_ns
    rules = {r.id: r for a in sc.actors for r in a.decision_rules}
    assert "not_corrected" in rules["open_resus_or_standard"].when
    assert "exists(" in rules["rapid_response_or_observe"].when
    assert parse_predicate(rules["rapid_response_or_observe"].when).kinds == {"vitals_alert", "sepsis_screen"}
    corr = next(i for i in sc.injects if i.kind == "correction")
    target = next(i for i in sc.injects if i.id == corr.payload["corrects"])
    assert target.kind == "acuity_assessment" and corr.payload["esi"] > target.payload["esi"]  # a downgrade
    assert 0 < rules["open_resus_or_standard"].at_ns - corr.at_ns <= 120 * S                    # decision shortly after
    # different domain vocabulary from the security specimens
    assert not {i.kind for i in sc.injects} & {"sighting", "threat_report", "alert", "ack", "lost_link"}


# ---------------------------------------------------------------- determinism and baseline

def test_deterministic(sc, root):
    again = run(load_scenario(PATH))
    assert again.digest == root.digest
    assert json.dumps(again.to_dict(), sort_keys=True, default=str) == \
        json.dumps(root.to_dict(), sort_keys=True, default=str)


def test_baseline_decisions_and_verdicts(sc, root, inv):
    assert options(root) == {"open_resus_or_standard": "standard_bed",
                             "rapid_response_or_observe": "call_rapid_response"}
    vs = verdicts(root, sc, inv)
    assert set(vs) == set(sc.invariants)
    assert all(v.result == Verdict.PASS for v in vs.values()), {k: v.result for k, v in vs.items()}


def test_correction_reaches_bed_manager_before_decision(root):
    got = next(e for e in root.views["bed_manager"] if e.id == "correction-001")
    assert 690 * S < got.received_time_ns < 780 * S


def test_clinical_detail_never_reaches_bed_manager(root):
    seen = {e.id for e in root.views["bed_manager"]}
    private = {e.id for e in root.truth if "clinical_private" in e.policy_labels}
    assert private and not (seen & private)
    assert private <= {e.id for e in root.views["triage_nurse"]} | {"lab-002"}   # lab-002 falls in the outage
    assert "lab-002" not in {e.id for e in root.views["triage_nurse"]}


# ---------------------------------------------------------------- the latent defect

def test_late_correction_flips_decision_and_fails_truth_consistency(sc, root, inv):
    tr = run(sc, delay_correction(60))
    assert tr.digest != root.digest
    assert options(tr) == {"open_resus_or_standard": "open_resus_bay",       # stale acuity: two "urgent" patients
                           "rapid_response_or_observe": "call_rapid_response"}  # the other rule is untouched
    vs = verdicts(tr, sc, inv)
    assert vs[TARGET].result == Verdict.FAIL
    assert "correction-001" in vs[TARGET].witness
    assert "open_resus_bay" in vs[TARGET].explanation and "standard_bed" in vs[TARGET].explanation
    assert vs["epistemic.no_future_knowledge"].result == Verdict.PASS
    assert run(sc, delay_correction(60)).digest == tr.digest


def test_small_delay_that_still_lands_before_decision_is_harmless(sc, inv):
    tr = run(sc, delay_correction(20))
    assert options(tr)["open_resus_or_standard"] == "standard_bed"
    assert verdicts(tr, sc, inv)[TARGET].result == Verdict.PASS


def test_ddmin_isolates_the_correction_delay(sc, inv):
    noisy = delay_correction(120) + (
        Intervention("delay", {"event_id": "arrival-104", "role": "bed_manager"}, {"by_ns": 30 * S}),
        Intervention("clock_skew", {}, {"source_id": "bed_board", "offset_ns": 5 * S}),
    )
    m = minimize(sc, inv, noisy, TARGET)
    assert [d["target"].get("event_id") for d in m.minimal] == ["correction-001"]


# ---------------------------------------------------------------- the port_alert genome against this world

def _check_challenge(r):
    assert r.scenario_id == "triage_board"
    assert r.preconditions_met is True and all(r.preconditions_report.values())
    assert r.target_rule == "open_resus_or_standard"
    assert [i["target"] for i in r.instantiated_schedule] == [{"event_id": "correction-001", "role": "bed_manager"}]
    assert r.reproduced is True
    fails = [v for v in r.verdicts if v["result"] == Verdict.FAIL.value]
    assert [v["invariant_id"] for v in fails] == [TARGET] and "correction-001" in fails[0]["witness"]


@pytest.mark.skipif(not GENOME_FIXTURE.exists(), reason="experiments/out/genome.json not generated")
def test_committed_genome_reproduces_on_triage(sc, inv):
    g = FailureGenome.from_dict(json.loads(GENOME_FIXTURE.read_text(encoding="utf-8")))
    assert g.origin["scenario_id"] == "port_alert"
    r = challenge(g, sc, inv)
    _check_challenge(r)
    assert challenge(g, sc, inv).to_dict() == r.to_dict()


def test_freshly_extracted_genome_reproduces_on_triage(sc, inv):
    port = load_scenario(ROOT / "scenarios" / "port_alert.yaml")
    m = minimize(port, inv, (Intervention("delay", {"event_id": "correction-001", "role": "harbour_ops"},
                                          {"by_ns": 300 * S}),), TARGET)
    minimal = [Intervention.from_dict(d) for d in m.minimal]
    tr = run(port, tuple(minimal))
    v = next(x for x in check_all(tr, {TARGET: inv[TARGET]}, scenario=port, evaluate_rule=rule_evaluator(port),
                                  now_ns=0) if x.result == Verdict.FAIL)
    g = extract(tr, minimal, v, port)
    _check_challenge(challenge(g, sc, inv))
    assert set(decision_dependencies(sc)) == {"open_resus_or_standard", "rapid_response_or_observe"}
