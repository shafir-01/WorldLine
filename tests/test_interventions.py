"""Interventions: each op through the delivery hook, selectors, mutators, abstract<->concrete."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from worldline.core import DeterministicRNG, Event, EventLog, content_hash
from worldline.interventions import (
    ClockSkewMutator, CorruptMutator, DelayMutator, DropMutator, DuplicateMutator, Intervention,
    ReorderMutator, RevokeMutator, abstractize, apply, concretize, default_mutators, hook_for_schedule,
    resolve, schedule_hash,
)

S = 1_000_000_000


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


def make_trace():
    truth = EventLog()
    truth.extend([
        ev(1, 10, payload={"confidence": 0.7}),
        ev(2, 20, payload={"confidence": 0.9}),
        ev(3, 25, kind="alert", source="ops", payload={"level": 2}),
        ev(4, 30, payload={"confidence": 0.4}, source="sonar"),
        ev(5, 40, kind="weather", source="met", payload={"wind_kt": 12, "storm": False}),
    ])
    views = {"harbour_ops": EventLog(), "coast_guard": EventLog()}
    for role, v in views.items():
        v.extend([e.with_receipt(e.event_time_ns + S) for e in truth])
    return FakeTrace(truth=truth, views=views)


def rng():
    return DeterministicRNG(7)


# ---------------------------------------------------------------- Intervention / schedule

def test_intervention_hash_is_content_based_and_order_independent():
    a = Intervention("delay", {"event_id": "e1"}, {"by_ns": 5, "role": "r"})
    b = Intervention("delay", {"event_id": "e1"}, {"role": "r", "by_ns": 5})
    assert a == b and a.hash() == b.hash() and hash(a) == hash(b)
    assert a.hash() != Intervention("drop", {"event_id": "e1"}).hash()
    assert Intervention.from_dict(a.to_dict()) == a
    assert schedule_hash((a, b)) != schedule_hash((b,))
    with pytest.raises(ValueError):
        Intervention("explode", {})


# ---------------------------------------------------------------- ops via the hook

def test_delay_shifts_received_time_only_for_targeted_role():
    tr = make_trace()
    e1 = tr.truth.ordered()[0]
    hook = hook_for_schedule((Intervention("delay", {"event_id": "e1"}, {"by_ns": 5 * S, "role": "harbour_ops"}),))
    assert hook(e1, "harbour_ops", 11 * S, rng()) == [(e1, 16 * S)]
    assert hook(e1, "coast_guard", 11 * S, rng()) == [(e1, 11 * S)]
    e2 = tr.truth.ordered()[1]
    assert hook(e2, "harbour_ops", 21 * S, rng()) == [(e2, 21 * S)]


def test_drop_suppresses_delivery():
    tr = make_trace()
    e1 = tr.truth.ordered()[0]
    hook = hook_for_schedule((Intervention("drop", {"event_id": "e1"}),))
    assert hook(e1, "harbour_ops", 11 * S, rng()) == [(e1, None)]


def test_duplicate_emits_payload_preserving_copies_with_provenance():
    tr = make_trace()
    e1 = tr.truth.ordered()[0]
    hook = hook_for_schedule((Intervention("duplicate", {"event_id": "e1"}, {"count": 2, "spacing_ns": S}),))
    out = hook(e1, "harbour_ops", 11 * S, rng())
    assert [(d.id, t) for d, t in out] == [("e1", 11 * S), ("e1#dup1", 12 * S), ("e1#dup2", 13 * S)]
    for d, _ in out[1:]:
        assert d.payload == e1.payload and d.event_time_ns == e1.event_time_ns
        assert "e1" in d.provenance_ids


def test_reorder_pushes_event_past_next_of_same_kind():
    tr = make_trace()
    events = tr.truth.ordered()
    e1, e2 = events[0], events[1]
    # by_ns computed from the truth events: e2 is the next sighting at 20s, so e1 lands at 11+10+1ns
    hook = hook_for_schedule((Intervention("reorder", {"event_id": "e1"}),), events)
    (_, t1), = hook(e1, "harbour_ops", 11 * S, rng())
    (_, t2), = hook(e2, "harbour_ops", 21 * S, rng())
    assert t1 == 21 * S + 1 and t1 > t2
    # explicit by_ns wins
    hook2 = hook_for_schedule((Intervention("reorder", {"event_id": "e1"}, {"by_ns": 3}),), events)
    assert hook2(e1, "harbour_ops", 11 * S, rng()) == [(e1, 11 * S + 3)]


def test_corrupt_sets_field_quality_and_provenance():
    tr = make_trace()
    e1 = tr.truth.ordered()[0]
    hook = hook_for_schedule((Intervention("corrupt", {"event_id": "e1"}, {"field": "payload.confidence", "value": 0.01}),))
    (c, t), = hook(e1, "harbour_ops", 11 * S, rng())
    assert t == 11 * S and c.id == "e1" and c.payload == {"confidence": 0.01}
    assert c.quality == 0.0 and c.provenance_ids == ("e1",)
    assert e1.payload == {"confidence": 0.7}  # truth untouched


def test_revoke_drops_all_deliveries_to_role_from_time():
    tr = make_trace()
    events = tr.truth.ordered()
    hook = hook_for_schedule((Intervention("revoke", {}, {"role": "coast_guard", "from_ns": 25 * S}),))
    assert hook(events[0], "coast_guard", 11 * S, rng()) == [(events[0], 11 * S)]
    assert hook(events[2], "coast_guard", 25 * S, rng()) == [(events[2], None)]
    assert hook(events[4], "coast_guard", 41 * S, rng()) == [(events[4], None)]
    assert hook(events[4], "harbour_ops", 41 * S, rng()) == [(events[4], 41 * S)]


def test_clock_skew_shifts_received_time_for_one_source():
    tr = make_trace()
    events = tr.truth.ordered()
    hook = hook_for_schedule((Intervention("clock_skew", {}, {"source_id": "radar", "offset_ns": -3 * S}),))
    assert hook(events[0], "harbour_ops", 11 * S, rng()) == [(events[0], 8 * S)]
    assert hook(events[2], "harbour_ops", 26 * S, rng()) == [(events[2], 26 * S)]  # source ops, unaffected


def test_abstract_target_matches_through_resolver_and_schedule_composes():
    tr = make_trace()
    events = tr.truth.ordered()
    sched = (
        Intervention("duplicate", {"kind": "sighting", "source_id": "radar", "nth": 1}, {"count": 1}),
        Intervention("delay", {"kind": "sighting", "source_id": "radar", "nth": 1, "role": "harbour_ops"}, {"by_ns": S}),
    )
    hook = hook_for_schedule(sched, events)
    out = hook(events[1], "harbour_ops", 21 * S, rng())  # e2 is nth=1 radar sighting
    assert [(d.id, t) for d, t in out] == [("e2", 22 * S), ("e2#dup1", 22 * S)]  # delay applies to the copy too
    assert hook(events[0], "harbour_ops", 11 * S, rng()) == [(events[0], 11 * S)]
    # callable resolver form
    hook2 = hook_for_schedule(sched, lambda sel: resolve(sel, events))
    assert [t for _, t in hook2(events[1], "coast_guard", 21 * S, rng())] == [21 * S, 21 * S]


def test_apply_maps_over_planned_deliveries():
    tr = make_trace()
    events = tr.truth.ordered()
    plan = [(events[0], "harbour_ops", 11 * S), (events[1], "harbour_ops", 21 * S)]
    out = apply((Intervention("drop", {"event_id": "e2"}),), events, plan, rng())
    assert [(e.id, r, t) for e, r, t in out] == [("e1", "harbour_ops", 11 * S), ("e2", "harbour_ops", None)]


# ---------------------------------------------------------------- resolve

def test_resolve_filters_and_orders_canonically():
    events = make_trace().truth.ordered()
    assert resolve({"kind": "sighting"}, events) == ["e1", "e2", "e4"]
    assert resolve({"kind": "sighting", "source_id": "radar", "nth": 1}, events) == ["e2"]
    assert resolve({"kind": "sighting", "nth": -1}, events) == ["e4"]
    assert resolve({"kind": "sighting", "nth": 9}, events) == []
    assert resolve({"payload_match": {"confidence": 0.4}}, events) == ["e4"]
    assert resolve({"kind": "weather", "payload_match": {"payload.storm": False}}, events) == ["e5"]
    assert resolve({"event_id": "e3"}, events) == ["e3"]
    assert resolve({"event_id": "nope"}, events) == []
    assert resolve({"kind": "sighting", "role": "harbour_ops"}, events) == ["e1", "e2", "e4"]  # role ignored here


# ---------------------------------------------------------------- mutators

@pytest.mark.parametrize("mut", default_mutators(), ids=lambda m: m.op)
def test_mutators_are_deterministic_and_concrete(mut):
    tr = make_trace()
    a = mut.propose(tr, DeterministicRNG(42), 3)
    b = mut.propose(tr, DeterministicRNG(42), 3)
    assert a == b and 0 < len(a) <= 3
    for iv in a:
        assert iv.op == mut.op
        if mut.op in ("revoke", "clock_skew"):
            assert iv.target == {}
        else:
            assert iv.is_concrete and iv.target["event_id"] in {e.id for e in tr.truth}
    assert mut.propose(tr, DeterministicRNG(43), 3) != a or mut.op == "reorder"


def test_mutator_specifics():
    tr = make_trace()
    r = DeterministicRNG(1)
    for iv in ReorderMutator().propose(tr, r, 10):
        assert iv.params["by_ns"] > 0
    for iv in CorruptMutator().propose(tr, r, 10):
        assert iv.params["field"].startswith("payload.")
    for iv in RevokeMutator().propose(tr, r, 10):
        assert iv.params["role"] in tr.views and iv.params["from_ns"] in {e.event_time_ns for e in tr.truth}
    for iv in ClockSkewMutator().propose(tr, r, 4):
        assert iv.params["source_id"] in {"radar", "ops", "sonar", "met"}
    for iv in DuplicateMutator(max_count=2).propose(tr, r, 5):
        assert 1 <= iv.params["count"] <= 2
    for iv in DelayMutator().propose(tr, r, 5) + DropMutator().propose(tr, r, 5):
        assert iv.params.get("role") in (None, "harbour_ops", "coast_guard")


# ---------------------------------------------------------------- abstract <-> concrete

def test_abstractize_concretize_round_trip_on_same_trace():
    tr = make_trace()
    concrete = [
        Intervention("delay", {"event_id": "e2"}, {"by_ns": 5 * S, "role": "harbour_ops"}),
        Intervention("drop", {"event_id": "e4"}),
        Intervention("corrupt", {"event_id": "e5"}, {"field": "payload.wind_kt", "value": 99}),
        Intervention("duplicate", {"event_id": "e1", "role": "coast_guard"}, {"count": 2}),
    ]
    for x in concrete:
        a = abstractize(x, tr)
        assert not a.is_concrete and a.params == x.params
        assert {"kind", "source_id", "nth"} <= set(a.target)
        assert concretize(a, tr) == x
    a2 = abstractize(concrete[0], tr)
    assert a2.target == {"kind": "sighting", "source_id": "radar", "nth": 1}
    a4 = abstractize(concrete[3], tr)
    assert a4.target == {"kind": "sighting", "source_id": "radar", "nth": 0, "role": "coast_guard"}
    # untargeted and already-abstract are identity
    rv = Intervention("revoke", {}, {"role": "x", "from_ns": 1})
    assert abstractize(rv, tr) == rv and concretize(rv, tr) == rv
    assert concretize(a2, tr.truth.ordered()) == concrete[0]


def test_round_trip_for_every_mutator_proposal():
    tr = make_trace()
    for mut in default_mutators():
        for iv in mut.propose(tr, DeterministicRNG(5), 4):
            assert concretize(abstractize(iv, tr), tr) == iv


def test_concretize_on_other_trace_or_missing():
    tr = make_trace()
    a = abstractize(Intervention("drop", {"event_id": "e4"}), tr)  # sonar sighting nth=0
    other = EventLog()
    other.extend([ev(9, 1, source="sonar"), ev(8, 2, source="sonar")])
    assert concretize(a, other).target == {"event_id": "e9"}
    empty = EventLog()
    empty.extend([ev(7, 1, kind="weather")])
    assert concretize(a, empty) is None
    with pytest.raises(KeyError):
        abstractize(Intervention("drop", {"event_id": "ghost"}), tr)
