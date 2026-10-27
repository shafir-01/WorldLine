"""Extended invariants (invariants/extended.yaml): PASS / FAIL (exact witness) / UNKNOWN for each,
the `reference` kwarg, and end-to-end checks on the real port_alert and convoy_lostlink worlds."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from worldline.core import Event, EventLog, Verdict, content_hash
from worldline.explorer.explore import rule_evaluator
from worldline.interventions import Intervention
from worldline.invariants import KINDS, Invariant, check, check_all, load_invariants
from worldline.worlds import load_scenario, run

S = 1_000_000_000
ROOT = Path(__file__).resolve().parents[1]
EXTENDED = {"latency.bound", "ordering.causal", "duplicate.suppressed", "quorum.independent_sources",
            "decision.fault_attributed", "epistemic.permitted_only"}


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


@dataclass
class Role:
    id: str
    visibility: list[str]


@dataclass
class Scn:
    roles: list = field(default_factory=list)
    actors: list = field(default_factory=list)


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


def at(e, t):
    return e.with_receipt(t * S)


def view_of(truth, latency_s=1, exclude=(), extra=()):
    return log(*[e.with_receipt(e.event_time_ns + latency_s * S) for e in truth if e.id not in exclude], *extra)


@pytest.fixture(scope="module")
def invs():
    return load_invariants(ROOT / "invariants")


# ---------------------------------------------------------------- loading and compatibility

def test_extended_yaml_loads_with_unique_ids(invs):
    ext = load_invariants(ROOT / "invariants" / "extended.yaml")
    assert set(ext) == EXTENDED
    assert EXTENDED <= set(invs) and len(invs) == 12
    assert all(i.version == "1.0" for i in ext.values())
    assert {"latency", "ordering", "duplicate", "quorum"} <= set(KINDS)


def test_duplicate_id_across_files_rejected(tmp_path):
    body = "invariants:\n  - {id: x.y, version: '1', kind: always, params: {field: payload.a}}\n"
    (tmp_path / "a.yaml").write_text(body, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate invariant id"):
        load_invariants(tmp_path)


def test_reference_kwarg_is_ignored_by_other_invariants(invs):
    truth = log(ev(1, 10, payload={"confidence": 0.7}))
    tr = FakeTrace(truth=truth, views={"harbour_ops": view_of(truth)}, decisions=[decision(1, 25, known=("e1",))])
    other = FakeTrace(truth=log(), views={})
    for k, inv in invs.items():
        if k == "decision.fault_attributed":
            continue
        assert check(tr, inv, reference=other) == check(tr, inv)
    assert check_all(tr, invs, reference=other)[0].invariant_id == sorted(invs)[0]


# ---------------------------------------------------------------- latency.bound

def test_latency_bound(invs):
    inv = invs["latency.bound"]  # harbour_ops, correction, 60 s
    c1 = ev(1, 100, kind="correction", payload={"corrects": "e9"})
    c2 = ev(2, 200, kind="correction", payload={"corrects": "e8"})
    c3 = ev(3, 300, kind="correction", payload={"corrects": "e7"})
    truth = log(c1, c2, c3)
    assert check(FakeTrace(truth=truth, views={"harbour_ops": log(at(c1, 160), at(c2, 201), at(c3, 301))}),
                 inv).result is Verdict.PASS  # exactly at the bound passes
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": log(at(c1, 161), at(c2, 201), at(c3, 500))}), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["e1", "e3"]
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": log(at(ev(4, 5, payload={}), 6))}), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["correction"]
    rec = check(FakeTrace(truth=truth, views={"coast_guard": view_of(truth)}), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["view:harbour_ops"]


# ---------------------------------------------------------------- ordering.causal

def test_ordering_causal(invs):
    inv = invs["ordering.causal"]
    s3 = ev(3, 100, payload={"confidence": 0.65})
    corr = ev(5, 140, kind="correction", payload={"corrects": "e3"})
    alert = ev(1, 10, kind="alert", payload={})
    ack = ev(2, 30, kind="ack", payload={"acks": "e1"})
    truth = log(alert, ack, s3, corr)
    assert check(FakeTrace(truth=truth, views={"harbour_ops": view_of(truth)}), inv).result is Verdict.PASS
    # correction overtakes the sighting it corrects at coast_guard
    reordered = log(at(alert, 11), at(ack, 31), at(corr, 141), at(s3, 150))
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": view_of(truth), "coast_guard": reordered}), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["e3", "e5"]
    assert "coast_guard" in rec.explanation
    # the referenced event never arrives
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": view_of(truth, exclude=("e1",))}), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["e1", "e2"]
    # a copy of the cause counts as the cause
    dup = alert.replace(id="e1#dup1", provenance_ids=("e1",))
    rec = check(FakeTrace(truth=log(alert, ack), views={"r": log(at(dup, 20), at(ack, 31))}), inv)
    assert rec.result is Verdict.PASS
    # a configurable link field
    custom = Invariant(id="o", version="1", kind="ordering", params={"role": "r", "link_fields": ["in_reply_to"]})
    reply = ev(7, 50, kind="msg", payload={"in_reply_to": "e1"})
    rec = check(FakeTrace(truth=log(alert, reply), views={"r": log(at(reply, 51), at(alert, 52))}), custom)
    assert rec.result is Verdict.FAIL and rec.witness == ["e1", "e7"]
    # UNKNOWN: no link field resolves to a truth event (dangling references are not ordering evidence)
    plain = log(alert, ev(8, 40, kind="ack", payload={"acks": "nowhere"}))
    rec = check(FakeTrace(truth=plain, views={"harbour_ops": view_of(plain)}), inv)
    assert rec.result is Verdict.UNKNOWN
    assert rec.missing_data == ["payload.corrects", "payload.acks", "payload.acknowledges", "payload.in_reply_to"]
    assert check(FakeTrace(truth=truth, views={}), custom).missing_data == ["view:r"]


# ---------------------------------------------------------------- duplicate.suppressed

def test_duplicate_suppressed(invs):
    inv = invs["duplicate.suppressed"]
    s1, s2 = ev(1, 10, payload={"confidence": 0.7}), ev(2, 20, payload={"confidence": 0.9})
    truth = log(s1, s2)
    ok = FakeTrace(truth=truth, views={"harbour_ops": view_of(truth)}, decisions=[decision(1, 25, known=("e1", "e2"))])
    assert check(ok, inv).result is Verdict.PASS
    dup = s2.replace(id="e2#dup1", provenance_ids=("e2",)).with_receipt(22 * S)
    view = view_of(truth, extra=(dup,))
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": view},
                          decisions=[decision(1, 25, known=("e1", "e2", "e2#dup1"))]), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["d1", "e2", "e2#dup1"]
    # same origin through provenance only (relabelled copy without the '#dup' suffix)
    relay = s1.replace(id="relay-1", source_id="relay", provenance_ids=("e1",)).with_receipt(12 * S)
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": view_of(truth, extra=(relay,))},
                          decisions=[decision(1, 25, known=("e1", "relay-1"))]), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["d1", "e1", "relay-1"]
    # a duplicate in the view that the decision did not count is fine
    assert check(FakeTrace(truth=truth, views={"harbour_ops": view},
                           decisions=[decision(1, 25, known=("e1", "e2"))]), inv).result is Verdict.PASS
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": view}), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["decision"]


# ---------------------------------------------------------------- quorum.independent_sources

def test_quorum_independent_sources(invs):
    inv = invs["quorum.independent_sources"]  # escalate needs 2 sources of uncorrected sightings >= 0.6

    def d(option, known):
        return decision(1, 25, rule="escalate_or_monitor", option=option, known=known)

    a = ev(1, 10, source="patrol_boat_a", payload={"confidence": 0.8})
    b = ev(2, 12, source="radar_north", payload={"confidence": 0.65})
    weak = ev(3, 14, source="lookout_east", payload={"confidence": 0.4})
    truth = log(a, b, weak)
    view = view_of(truth)
    assert check(FakeTrace(truth=truth, views={"harbour_ops": view}, decisions=[d("escalate", ("e1", "e2", "e3"))]),
                 inv).result is Verdict.PASS
    # same source counted twice through a duplicate: one independent source
    dup = a.replace(id="e1#dup1", provenance_ids=("e1",)).with_receipt(13 * S)
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": view_of(truth, extra=(dup,))},
                          decisions=[d("escalate", ("e1", "e1#dup1", "e3"))]), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["d1", "e1", "e1#dup1"]
    assert "['patrol_boat_a']" in rec.explanation
    # a corrected sighting does not count when exclude_corrected is set
    corr = ev(4, 15, kind="correction", source="patrol_boat_a", payload={"corrects": "e2", "confidence": 0.1})
    truth2 = log(a, b, corr)
    rec = check(FakeTrace(truth=truth2, views={"harbour_ops": view_of(truth2)},
                          decisions=[d("escalate", ("e1", "e2", "e4"))]), inv)
    assert rec.result is Verdict.FAIL and rec.witness == ["d1", "e1"]
    # no decision chose the option: vacuous PASS, stated in the explanation
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": view}, decisions=[d("monitor", ("e1",))]), inv)
    assert rec.result is Verdict.PASS and "no decision" in rec.explanation
    # other rules are out of scope -> UNKNOWN
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": view}, decisions=[decision(1, 25, known=("e1",))]), inv)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["decision"]


# ---------------------------------------------------------------- decision.fault_attributed

def rule_two_sightings(rule_id, events):
    n = sum(1 for e in events if e.kind == "sighting" and e.payload.get("confidence", 0) >= 0.6)
    return "alert" if n >= 2 else "hold"


def test_decision_fault_attributed(invs):
    inv, core = invs["decision.fault_attributed"], invs["decision.consistent_with_truth"]
    # e0 is private to another role: missing from harbour_ops in every world, not caused by the fault
    private = ev(0, 5, kind="roster", source="admin", payload={})
    s1, s2 = ev(1, 10, payload={"confidence": 0.7}), ev(2, 20, payload={"confidence": 0.9})
    truth = log(private, s1, s2)
    root = FakeTrace(truth=truth, views={"harbour_ops": view_of(truth, exclude=("e0",))},
                     decisions=[decision(1, 25, option="alert", known=("e1", "e2"))])
    assert check(root, inv, evaluate_rule=rule_two_sightings, reference=root).result is Verdict.PASS
    delayed = log(at(s1, 11), at(s2, 40))
    branch = FakeTrace(truth=truth, views={"harbour_ops": delayed}, branch_id="root/x",
                       decisions=[decision(1, 25, option="hold", known=("e1",))])
    full = check(branch, core, evaluate_rule=rule_two_sightings)
    assert full.result is Verdict.FAIL and full.witness == ["e0", "e2"]
    # same verdict; with a reference the witness is only what the fault took away
    rec = check(branch, inv, evaluate_rule=rule_two_sightings, reference=root)
    assert rec.result is Verdict.FAIL and rec.witness == ["e2"]
    assert "e0" in rec.explanation and "not attributed" in rec.explanation
    # without a reference it behaves like consistent_with_truth and says so
    rec = check(branch, inv, evaluate_rule=rule_two_sightings)
    assert rec.result is Verdict.FAIL and rec.witness == full.witness and "no reference trace" in rec.explanation
    # the reference kwarg does not change the core invariant
    assert check(branch, core, evaluate_rule=rule_two_sightings, reference=root) == full
    # wrong decision with nothing attributable to the fault: witness falls back to the decision id
    wrong = FakeTrace(truth=truth, views={"harbour_ops": view_of(truth, exclude=("e0",))},
                      decisions=[decision(1, 25, option="hold", known=("e1", "e2"))])
    assert check(wrong, inv, evaluate_rule=rule_two_sightings, reference=root).witness == ["d1"]
    rec = check(branch, inv, reference=root)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["decision_rule_evaluator"]
    rec = check(FakeTrace(truth=truth, views={"harbour_ops": delayed}), inv, evaluate_rule=rule_two_sightings)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["decision"]


# ---------------------------------------------------------------- epistemic.permitted_only

def test_epistemic_permitted_only(invs):
    inv = invs["epistemic.permitted_only"]
    scn = Scn(roles=[Role("coast_guard", ["public", "coast_shared"]), Role("harbour_ops", ["*"])])
    pub = ev(1, 10, payload={}, policy_labels=("public",))
    priv = ev(2, 20, kind="berth_schedule", payload={}, policy_labels=("harbour_private",))
    truth = log(pub, priv)
    views = {"coast_guard": view_of(truth), "harbour_ops": view_of(truth)}
    cg = decision(1, 25, actor="cw", role="coast_guard", known=("e1",))
    ho = decision(2, 25, actor="do", role="harbour_ops", known=("e1", "e2"))
    assert check(FakeTrace(truth=truth, views=views, decisions=[cg, ho]), inv, scenario=scn).result is Verdict.PASS
    leak = decision(1, 25, actor="cw", role="coast_guard", known=("e1", "e2"))
    rec = check(FakeTrace(truth=truth, views=views, decisions=[leak, ho]), inv, scenario=scn)
    assert rec.result is Verdict.FAIL and rec.witness == ["d1", "e2"]
    # dict-shaped roles work too
    rec = check(FakeTrace(truth=truth, views=views, decisions=[leak]), inv,
                scenario=Scn(roles=[{"id": "coast_guard", "visibility": ["public"]}]))
    assert rec.witness == ["d1", "e2"]
    # UNKNOWN paths
    tr = FakeTrace(truth=truth, views=views, decisions=[cg])
    assert check(tr, inv).missing_data == ["scenario"]
    assert check(FakeTrace(truth=truth, views=views), inv, scenario=scn).missing_data == ["decision"]
    rec = check(tr, inv, scenario=Scn(roles=[Role("harbour_ops", ["*"])]))
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["role:coast_guard"]
    ghost = decision(1, 25, actor="cw", role="coast_guard", known=("e1", "e99"))
    rec = check(FakeTrace(truth=truth, views=views, decisions=[ghost]), inv, scenario=scn)
    assert rec.result is Verdict.UNKNOWN and rec.missing_data == ["event:e99"]


# ---------------------------------------------------------------- real worlds

@pytest.fixture(scope="module")
def port():
    return load_scenario(ROOT / "scenarios" / "port_alert.yaml")


@pytest.fixture(scope="module")
def convoy():
    return load_scenario(ROOT / "scenarios" / "convoy_lostlink.yaml")


def _by_id(tr, sc, invs, reference=None):
    return {v.invariant_id: v for v in check_all(tr, invs, scenario=sc, evaluate_rule=rule_evaluator(sc),
                                                 reference=reference)}


def test_port_alert_root_passes_every_extended_invariant(port, invs):
    v = _by_id(run(port), port, invs)
    assert all(v[k].result is Verdict.PASS for k in EXTENDED), {k: v[k].explanation for k in EXTENDED}


def test_port_alert_delayed_correction(port, invs):
    root = run(port)
    sched = (Intervention("delay", {"event_id": "correction-001", "role": "harbour_ops"}, {"by_ns": 300 * S}),)
    v = _by_id(run(port, sched), port, invs, reference=root)
    assert v["latency.bound"].witness == ["correction-001"]
    assert v["decision.consistent_with_truth"].witness == ["correction-001", "ais-006", "patrol-001"]
    assert v["decision.fault_attributed"].witness == ["correction-001"]
    assert v["quorum.independent_sources"].result is Verdict.PASS  # two real sources, just stale


def test_port_alert_duplicate_sighting(port, invs):
    sched = (Intervention("duplicate", {"event_id": "sighting-002", "role": "harbour_ops"},
                          {"count": 1, "spacing_ns": S}),)
    v = _by_id(run(port, sched), port, invs, reference=run(port))
    d = "decision:duty_officer:escalate_or_monitor"
    assert v["duplicate.suppressed"].witness == [d, "sighting-002", "sighting-002#dup1"]
    assert v["quorum.independent_sources"].witness == [d, "sighting-002", "sighting-002#dup1"]
    # wrong decision, but no truth event went missing because of the fault
    assert v["decision.fault_attributed"].witness == [d]


def test_convoy_fault_attributed_witness(convoy, invs):
    root = run(convoy)
    sched = (Intervention("delay", {"event_id": "retract-001", "role": "ops_center"}, {"by_ns": 300 * S}),)
    tr = run(convoy, sched)
    v = _by_id(tr, convoy, invs, reference=root)
    assert v["decision.consistent_with_truth"].witness == ["tele-001", "tele-002", "tele-003", "tele-004",
                                                           "retract-001"]
    assert v["decision.fault_attributed"].result is Verdict.FAIL
    assert v["decision.fault_attributed"].witness == ["retract-001"]
    assert _by_id(tr, convoy, invs)["decision.fault_attributed"].witness == v["decision.consistent_with_truth"].witness
