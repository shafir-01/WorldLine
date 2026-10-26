"""Invariants: PASS / FAIL (exact witness) / UNKNOWN for every core invariant, plus loading."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from worldline.core import Event, EventLog, Verdict, content_hash
from worldline.invariants import Invariant, VerdictRecord, check, check_all, load_invariants

S = 1_000_000_000
ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- fake Trace (duck-typed per ARCHITECTURE.md)

@dataclass
class FakeTrace:
    truth: EventLog
    views: dict[str, EventLog] = field(default_factory=dict)
    decisions: list[Event] = field(default_factory=list)
    scenario_id: str = "fake"
    seed: int = 1
    branch_id: str = "root"
    fault_schedule_hash: str = ""
    digest: str = ""

    def __post_init__(self):
        self.digest = content_hash([self.truth.digest, sorted(v.digest for v in self.views.values()),
                                    [d.content_hash for d in self.decisions]])


def ev(i, t, kind="sighting", source="radar", **kw):
    return Event(id=f"e{i}", scenario_id="fake", source_id=source, event_time_ns=t * S, kind=kind, **kw)


def decision(i, t, actor="ops1", role="harbour_ops", rule="r1", option="hold", known=()):
    return Event(id=f"d{i}", scenario_id="fake", source_id=actor, event_time_ns=t * S, kind="decision",
                 payload={"actor": actor, "role": role, "rule_id": rule, "option": option,
                          "known_event_ids": list(known), "at_ns": t * S})


def log(*events):
    lg = EventLog()
    lg.extend(events)
    return lg


def view_of(truth, latency_s=1, exclude=(), extra=()):
    return log(*[e.with_receipt(e.event_time_ns + latency_s * S) for e in truth if e.id not in exclude], *extra)


@pytest.fixture(scope="module")
def invs():
    return load_invariants(ROOT / "invariants")


def rule_two_sightings(rule_id, events):
    """Deterministic stand-in for the worlds rule evaluator: >=2 sightings with confidence>=0.6 -> alert."""
    n = sum(1 for e in events if e.kind == "sighting" and e.payload.get("confidence", 0) >= 0.6)
    return "alert" if n >= 2 else "hold"


def base_truth():
    return [ev(1, 10, payload={"confidence": 0.7}), ev(2, 20, payload={"confidence": 0.9}),
            ev(3, 30, kind="weather", source="met", payload={"capacity": 5})]


# ---------------------------------------------------------------- loading

def test_load_core_yaml_from_dir_and_file(invs):
    expected = {"epistemic.no_future_knowledge", "epistemic.label_isolation", "response.alert_ack",
                "absence.no_corrupted_in_decision", "decision.consistent_with_truth", "always.non_negative_capacity"}
    assert expected <= set(invs)
    assert load_invariants(ROOT / "invariants" / "core.yaml").keys() == invs.keys()
    assert all(isinstance(i, Invariant) and i.version == "1.0" for i in invs.values())
    with pytest.raises(ValueError):
        Invariant(id="x", version="1", kind="sometimes")


def test_verdict_record_carries_clock_and_digest(invs):
    tr = FakeTrace(truth=log(*base_truth()))
    rec = check(tr, invs["always.non_negative_capacity"], now_ns=123)
    assert isinstance(rec, VerdictRecord)
    assert rec.derived_at_ns == 123 and rec.trace_digest == tr.digest
    assert rec.invariant_id == "always.non_negative_capacity" and rec.invariant_version == "1.0"
    assert rec.to_dict()["result"] == "PASS"


def test_required_signals_absent_gives_unknown_naming_them():
    inv = Invariant(id="t", version="1", kind="always", params={"field": "payload.x"}, required_signals=["telemetry"])
    rec = check(FakeTrace(truth=log(*base_truth())), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["telemetry"] and rec.witness == []


# ---------------------------------------------------------------- always

def test_always_pass_fail_unknown(invs):
    inv = invs["always.non_negative_capacity"]
    assert check(FakeTrace(truth=log(*base_truth())), inv).result is Verdict.PASS
    bad = FakeTrace(truth=log(*base_truth(), ev(4, 40, kind="weather", source="met", payload={"capacity": -1}),
                              ev(5, 50, kind="weather", source="met", payload={"capacity": -2})))
    rec = check(bad, inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["e4"]
    rec = check(FakeTrace(truth=log(ev(1, 10, payload={"confidence": 0.7}))), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["field:payload.capacity"]


# ---------------------------------------------------------------- response

def test_response_alert_ack(invs):
    inv = invs["response.alert_ack"]
    alert = ev(1, 10, kind="alert", source="ops", payload={"level": 2})
    noise = ev(2, 100, kind="weather", source="met", payload={})
    ack = ev(3, 200, kind="ack", source="ops1", payload={"acknowledges": "e1"})
    late_ack = ev(4, 400, kind="ack", source="ops1", payload={"acknowledges": "e1"})
    truth = log(alert, noise, ack)
    assert check(FakeTrace(truth=truth, views={"harbour_ops": view_of(truth)}), inv).result is Verdict.PASS
    # late ack: FAIL witness = alert + latest event received before the deadline (10+1+300 = 311s)
    truth2 = log(alert, noise, late_ack)
    rec = check(FakeTrace(truth=truth2, views={"harbour_ops": view_of(truth2)}), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["e1", "e2"]
    # no ack and nothing else received: witness is just the alert
    truth3 = log(alert)
    rec = check(FakeTrace(truth=truth3, views={"harbour_ops": view_of(truth3)}), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["e1"]
    # ack present but linked to another alert does not count
    truth4 = log(alert, ev(3, 200, kind="ack", source="ops1", payload={"acknowledges": "e9"}))
    assert check(FakeTrace(truth=truth4, views={"harbour_ops": view_of(truth4)}), inv).result is Verdict.FAIL
    # UNKNOWN: no alert anywhere -> required signal missing
    rec = check(FakeTrace(truth=log(noise), views={"harbour_ops": view_of(log(noise))}), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["alert"]
    # UNKNOWN: alert exists in truth but the role has no view
    rec = check(FakeTrace(truth=truth, views={"coast_guard": view_of(truth)}), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["view:harbour_ops"]
    # alert in truth but dropped before reaching the role: nothing to acknowledge -> UNKNOWN names the kind
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": view_of(truth, exclude=("e1",))}), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["alert"]


# ---------------------------------------------------------------- epistemic.no_future_knowledge

def test_epistemic_no_future_knowledge(invs):
    inv = invs["epistemic.no_future_knowledge"]
    truth = log(*base_truth())
    ok = FakeTrace(truth=truth, views={"harbour_ops": view_of(truth)},
                   decisions=[decision(1, 25, known=("e1", "e2"))])
    assert check(ok, inv).result is Verdict.PASS
    # a view event received before it happened (e.g. negative clock skew)
    skewed = log(ev(1, 10, payload={}).with_receipt(9 * S), ev(2, 20, payload={}).with_receipt(21 * S))
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": skewed}), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["e1"]
    # a decision citing an event its role had not yet received (e3 arrives at 31s, decision at 25s)
    bad = FakeTrace(truth=truth, views={"harbour_ops": view_of(truth)},
                    decisions=[decision(1, 25, known=("e1", "e3"))])
    rec = check(bad, inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["d1", "e3"]
    # decision citing an event the role never received at all
    bad2 = FakeTrace(truth=truth, views={"harbour_ops": view_of(truth, exclude=("e2",))},
                     decisions=[decision(1, 25, known=("e1", "e2"))])
    assert check(bad2, inv).witness == ["d1", "e2"]
    # UNKNOWN: nothing observed, nothing decided
    rec = check(FakeTrace(truth=truth), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["role_views"]


# ---------------------------------------------------------------- epistemic.label_isolation

def test_label_isolation(invs):
    inv = invs["epistemic.label_isolation"]
    private = ev(1, 10, source="ops", payload={}, policy_labels=("harbour_private",))
    public = ev(2, 20, payload={})
    truth = log(private, public)
    ok = FakeTrace(truth=truth, views={"coast_guard": view_of(truth, exclude=("e1",)), "harbour_ops": view_of(truth)})
    assert check(ok, inv).result is Verdict.PASS
    rec = check(FakeTrace(truth=truth, views={"coast_guard": view_of(truth)}), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["e1"]
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": view_of(truth)}), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["view:coast_guard"]


# ---------------------------------------------------------------- absence.no_corrupted_in_decision

def test_no_corrupted_in_decision(invs):
    inv = invs["absence.no_corrupted_in_decision"]
    truth = log(*base_truth())
    clean = view_of(truth)
    ok = FakeTrace(truth=truth, views={"harbour_ops": clean}, decisions=[decision(1, 25, known=("e1", "e2"))])
    assert check(ok, inv).result is Verdict.PASS
    # corrupted copy as produced by interventions.corrupt: quality 0.0, self-citing provenance, same id
    corrupted = ev(2, 20, payload={"confidence": 0.01}, quality=0.0, provenance_ids=("e2",)).with_receipt(21 * S)
    dirty = view_of(truth, exclude=("e2",), extra=(corrupted,))
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": dirty}, decisions=[decision(1, 25, known=("e1", "e2"))]), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["d1", "e2"]
    # corrupted event present in the view but not relied upon -> PASS
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": dirty}, decisions=[decision(1, 25, known=("e1",))]), inv)
    assert rec.result is Verdict.PASS
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": dirty}), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["decision"]


def test_generic_absence_over_role_view():
    inv = Invariant(id="absence.cg_no_private", version="1", kind="absence",
                    params={"role": "coast_guard", "match": {"label": "harbour_private"}})
    private = ev(1, 10, payload={}, policy_labels=("harbour_private",))
    truth = log(private, ev(2, 20, payload={}))
    rec = check(FakeTrace(truth=truth, views={"coast_guard": view_of(truth)}), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["e1"]
    assert check(FakeTrace(truth=truth, views={"coast_guard": view_of(truth, exclude=("e1",))}), inv).result is Verdict.PASS
    assert check(FakeTrace(truth=truth, views={}), inv).missing_data == ["view:coast_guard"]


# ---------------------------------------------------------------- decision.consistent_with_truth

def test_decision_consistent_with_truth(invs):
    inv = invs["decision.consistent_with_truth"]
    truth = log(*base_truth())
    # actor saw both sightings by 25s -> rule says alert; recorded alert -> PASS
    ok = FakeTrace(truth=truth, views={"harbour_ops": view_of(truth)},
                   decisions=[decision(1, 25, option="alert", known=("e1", "e2"))])
    assert check(ok, inv, evaluate_rule=rule_two_sightings).result is Verdict.PASS
    # e2 delayed by 10s: at 25s the actor only knows e1, decides hold; truth says alert -> FAIL, witness = [e2]
    delayed = log(ev(1, 10, payload={"confidence": 0.7}).with_receipt(11 * S),
                  ev(2, 20, payload={"confidence": 0.9}).with_receipt(30 * S),
                  ev(3, 30, kind="weather", source="met", payload={"capacity": 5}).with_receipt(31 * S))
    stale = FakeTrace(truth=truth, views={"harbour_ops": delayed},
                      decisions=[decision(1, 25, option="hold", known=("e1",))])
    rec = check(stale, inv, evaluate_rule=rule_two_sightings)
    assert rec.result is Verdict.FAIL and rec.witness == ["e2"]
    assert "stale" not in rec.missing_data and "e2" in rec.explanation
    # a scenario object exposing evaluate_rule works too
    class Scn:
        evaluate_rule = staticmethod(rule_two_sightings)
    assert check(stale, inv, scenario=Scn()).result is Verdict.FAIL
    # wrong decision with no missing information: witness falls back to the decision id
    wrong = FakeTrace(truth=truth, views={"harbour_ops": view_of(truth)},
                      decisions=[decision(1, 25, option="hold", known=("e1", "e2"))])
    assert check(wrong, inv, evaluate_rule=rule_two_sightings).witness == ["d1"]
    # UNKNOWN paths
    rec = check(stale, inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["decision_rule_evaluator"]
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": delayed}), inv, evaluate_rule=rule_two_sightings)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["decision"]


# ---------------------------------------------------------------- check_all

def test_check_all_runs_every_core_invariant(invs):
    truth = log(*base_truth())
    tr = FakeTrace(truth=truth, views={"harbour_ops": view_of(truth), "coast_guard": view_of(truth)},
                   decisions=[decision(1, 25, option="alert", known=("e1", "e2"))])
    recs = check_all(tr, invs, evaluate_rule=rule_two_sightings, now_ns=5)
    assert [r.invariant_id for r in recs] == sorted(invs)
    by_id = {r.invariant_id: r for r in recs}
    assert by_id["response.alert_ack"].result is Verdict.UNKNOWN  # no alerts in this world
    assert all(by_id[k].result is Verdict.PASS for k in by_id if k != "response.alert_ack")
    assert all(r.derived_at_ns == 5 and r.trace_digest == tr.digest for r in recs)
    assert check_all(tr, list(invs.values())) == check_all(tr, invs)
