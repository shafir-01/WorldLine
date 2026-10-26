import json
import subprocess
import sys
from pathlib import Path

import pytest

from worldline.core import DeterministicRNG, Event
from worldline.worlds import (
    Actor,
    DecisionRule,
    DeliveryPolicy,
    Inject,
    PredicateError,
    Role,
    Scenario,
    Snapshot,
    Trace,
    TruthTransition,
    branch,
    load_scenario,
    parse_ns,
    parse_predicate,
    run,
    snapshot_at,
    view_as_known_at,
    view_as_observed_at,
)

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "scenarios" / "port_alert.yaml"
S = 1_000_000_000


@pytest.fixture(scope="module")
def scenario():
    return load_scenario(SCENARIO)


@pytest.fixture(scope="module")
def trace(scenario):
    return run(scenario)


def decision(trace, actor):
    return next(d for d in trace.decisions if d.payload["actor"] == actor)


def delay_hook(event_id, role_id, by_ns):
    def hook(ev, role, rt, rng):
        if ev.id == event_id and role == role_id:
            return [(ev, rt + by_ns)]
        return [(ev, rt)]
    return hook


# --- grammar -----------------------------------------------------------------

def test_scenario_loads_with_contract_shapes(scenario):
    assert isinstance(scenario, Scenario)
    assert [r.id for r in scenario.roles] == ["harbour_ops", "coast_guard", "evaluator"]
    assert all(isinstance(r, Role) and isinstance(r.delivery, DeliveryPolicy) for r in scenario.roles)
    assert {a.id for a in scenario.actors} == {"duty_officer", "coast_watch"}
    assert all(isinstance(a, Actor) for a in scenario.actors)
    assert all(isinstance(r, DecisionRule) for a in scenario.actors for r in a.decision_rules)
    assert all(isinstance(i, Inject) for i in scenario.injects)
    assert 30 <= len(scenario.injects) <= 50
    assert len(scenario.transitions) == 1 and isinstance(scenario.transitions[0], TruthTransition)
    assert scenario.clock == {"epoch_ns": 0, "tick_ns": S, "horizon_ns": 3600 * S}
    cg = scenario.role("coast_guard")
    assert cg.delivery.ordering == "network" and cg.delivery.base_latency_ns == 20 * S
    assert "harbour_private" not in cg.visibility
    duty = next(a for a in scenario.actors if a.id == "duty_officer")
    assert duty.decision_rules[0].at_ns == 1500 * S and duty.decision_rules[0].window_ns == 600 * S


def test_parse_ns_units():
    assert parse_ns("1500s") == 1500 * S
    assert parse_ns("2m") == 120 * S
    assert parse_ns("1h") == 3600 * S
    assert parse_ns("250ms") == 250_000_000
    assert parse_ns(42) == 42
    with pytest.raises(ValueError):
        parse_ns("soon")


def test_scenario_roundtrip_dict(scenario):
    again = Scenario.from_dict(json.loads(json.dumps(scenario.to_dict())))
    assert again == scenario


def test_scenario_rejects_bad_shapes():
    base = {"id": "x", "seed": 1, "clock": {"horizon_ns": "10s"}, "roles": [{"id": "r"}]}
    with pytest.raises(ValueError):
        Scenario.from_dict({**base, "actors": [{"id": "a", "role": "nope"}]})
    with pytest.raises(ValueError):
        Scenario.from_dict({**base, "injects": [{"id": "e", "at_ns": 1, "source_id": "s", "kind": "k"}] * 2})
    with pytest.raises(ValueError):
        Scenario.from_dict({**base, "roles": [{"id": "r", "delivery": {"ordering": "random"}}]})
    with pytest.raises(PredicateError):
        Scenario.from_dict({**base, "actors": [{"id": "a", "role": "r", "decision_rules": [
            {"id": "d", "at_ns": 1, "window_ns": 1, "when": "__import__('os')", "then": "a", "otherwise": "b"}]}]})


# --- predicate DSL -----------------------------------------------------------

def mk(i, kind, t, payload=None, rt=None):
    return Event(id=i, scenario_id="s", source_id="src", event_time_ns=t, kind=kind,
                 payload=payload or {}, received_time_ns=rt if rt is not None else t)


def test_predicate_count_confidence_and_not_corrected():
    evs = [
        mk("s1", "sighting", 10, {"confidence": 0.8}),
        mk("s2", "sighting", 20, {"confidence": 0.65}),
        mk("s3", "sighting", 30, {"confidence": 0.4}),
        mk("s4", "sighting", 35, {}),  # missing field never matches
    ]
    p = parse_predicate("count(kind='sighting', payload.confidence>=0.6, not_corrected) >= 2")
    assert p.evaluate(evs) is True
    evs2 = evs + [mk("c1", "correction", 40, {"corrects": "s2"})]
    assert p.evaluate(evs2) is False
    assert parse_predicate("count(kind='sighting', corrected) == 1").evaluate(evs2)
    assert parse_predicate("exists(kind='correction')").evaluate(evs2)
    assert not parse_predicate("exists(kind='nothing')").evaluate(evs2)
    assert parse_predicate("not exists(kind='nothing') and count(kind='sighting') > 3").evaluate(evs2)
    assert parse_predicate("(exists(kind='nothing') or exists(kind='sighting'))").evaluate(evs2)
    assert p.kinds == {"sighting", "correction"}


def test_predicate_window_versus_knowledge():
    # corrections outside the window still count as knowledge; sightings outside do not
    known = [mk("s1", "sighting", 10, {"confidence": 0.9}), mk("c1", "correction", 11, {"corrects": "s2"}),
             mk("s2", "sighting", 50, {"confidence": 0.9}), mk("s3", "sighting", 60, {"confidence": 0.9})]
    windowed = [e for e in known if e.received_time_ns >= 50]
    p = parse_predicate("count(kind='sighting', payload.confidence>=0.6, not_corrected) >= 2")
    assert p.evaluate(known, windowed) is False
    assert parse_predicate("count(kind='sighting', not_corrected) >= 1").evaluate(known, windowed) is True


def test_predicate_acknowledged():
    evs = [mk("a1", "alert", 100), mk("k1", "ack", 400, {"acknowledges": "a1"})]
    assert parse_predicate("acknowledged(kind='alert', within_ns=600s)").evaluate(evs)
    assert not parse_predicate("acknowledged(kind='alert', within_ns=100)").evaluate(evs)
    assert not parse_predicate("acknowledged(kind='alert')").evaluate([mk("a1", "alert", 100)])
    assert not parse_predicate("acknowledged(kind='alert')").evaluate([])
    evs2 = evs + [mk("a2", "alert", 500)]
    assert not parse_predicate("acknowledged(kind='alert')").evaluate(evs2)


@pytest.mark.parametrize("bad", [
    "", "import os", "count(kind='x') >= ", "count(kind='x' >= 2", "eval(kind='x')",
    "count(kind='x', bogus_flag)", "count(__class__='x')", "count(kind='x') >= 2 extra",
    "exists(kind='x') >= 'y'",
])
def test_predicate_rejects_garbage(bad):
    with pytest.raises(PredicateError):
        parse_predicate(bad)


# --- determinism -------------------------------------------------------------

def test_same_inputs_same_digest_in_process(scenario, trace):
    t2 = run(load_scenario(SCENARIO))
    assert t2.digest == trace.digest
    assert t2.truth.digest == trace.truth.digest
    assert {r: v.digest for r, v in t2.views.items()} == {r: v.digest for r, v in trace.views.items()}
    assert [d.content_hash for d in t2.decisions] == [d.content_hash for d in trace.decisions]


def test_same_inputs_same_digest_in_subprocess(trace):
    code = (
        "from worldline.worlds import load_scenario, run;"
        f"print(run(load_scenario(r'{SCENARIO}')).digest)"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, check=True)
    assert out.stdout.strip() == trace.digest


def test_different_seed_changes_views_not_truth(scenario, trace):
    other = Scenario.from_dict({**scenario.to_dict(), "seed": scenario.seed + 1})
    t2 = run(other)
    assert t2.truth.digest == trace.truth.digest
    assert t2.views["harbour_ops"].digest != trace.views["harbour_ops"].digest
    assert t2.views["evaluator"].digest == trace.views["evaluator"].digest  # zero jitter, zero latency
    assert t2.digest != trace.digest


def test_trace_shape_and_branch_id(trace, scenario):
    assert isinstance(trace, Trace)
    assert trace.scenario_id == "port_alert" and trace.seed == scenario.seed
    assert trace.branch_id == "root"
    assert trace.clock_end["now_ns"] == scenario.horizon_ns
    assert trace.truth.verify() == [] and all(v.verify() == [] for v in trace.views.values())
    assert len(trace.truth) == len(scenario.injects)
    assert trace.digest == trace.compute_digest()


# --- separation --------------------------------------------------------------

def test_harbour_private_never_reaches_coast_guard(trace):
    private_truth = [e.id for e in trace.truth if "harbour_private" in e.policy_labels]
    assert len(private_truth) >= 3
    for e in trace.views["coast_guard"]:
        assert "harbour_private" not in e.policy_labels
        assert e.id not in private_truth
    for role in ("harbour_ops", "evaluator"):
        assert set(private_truth) <= {e.id for e in trace.views[role]}


def test_received_never_before_event_time_and_evaluator_is_still_a_view(trace):
    for role, log in trace.views.items():
        for e in log:
            assert e.received_time_ns is not None
            assert e.received_time_ns >= e.event_time_ns, (role, e.id)
    for e in trace.views["evaluator"]:
        assert e.received_time_ns == e.event_time_ns
    for e in trace.views["harbour_ops"]:
        assert 2 * S <= e.received_time_ns - e.event_time_ns <= 3 * S
    for e in trace.views["coast_guard"]:
        assert 20 * S <= e.received_time_ns - e.event_time_ns <= 35 * S


def test_views_are_subsets_of_truth(trace):
    truth_ids = {e.id for e in trace.truth}
    truth_hash_by_id = {e.id: e.replace(received_time_ns=None).content_hash for e in trace.truth}
    for role, log in trace.views.items():
        for e in log:
            if e.provenance_ids:
                continue
            assert e.id in truth_ids
            assert e.replace(received_time_ns=None).content_hash == truth_hash_by_id[e.id]


def test_truth_events_carry_no_received_time(trace):
    assert all(e.received_time_ns is None for e in trace.truth)


def test_fifo_role_never_reorders_and_network_role_may(trace):
    ho = trace.views["harbour_ops"].events
    for a, b in zip(ho, ho[1:]):
        assert a.received_time_ns <= b.received_time_ns
        assert a.sort_key() <= b.sort_key()
    cg = trace.views["coast_guard"].ordered()  # truth order
    received = [e.received_time_ns for e in cg]
    assert received != sorted(received), "network ordering with 15s jitter should reorder something"


def test_radar_outage_withholds_from_every_view_but_keeps_truth(trace, scenario):
    outage = scenario.transitions[0]
    silenced = [e.id for e in trace.truth
                if e.source_id == outage.source_id and outage.from_ns <= e.event_time_ns <= outage.to_ns]
    assert silenced == ["radar-002", "radar-003", "radar-004"]
    for log in trace.views.values():
        assert not ({e.id for e in log} & set(silenced))
    for role in trace.views:
        assert {"radar-001", "radar-005"} <= {e.id for e in trace.views[role]}


def test_view_as_known_at_and_observed_at(trace):
    known = view_as_known_at(trace, "coast_guard", 1000 * S)
    assert all(e.received_time_ns <= 1000 * S for e in known)
    assert [e.received_time_ns for e in known] == sorted(e.received_time_ns for e in known)
    # ais-004 happened at 900s but reaches coast_guard >= 920s: observed-at 905s must exclude it,
    # known-at 905s excludes it too; known-at 1000s includes it.
    ids_known_905 = {e.id for e in view_as_known_at(trace, "coast_guard", 905 * S)}
    assert "ais-004" not in ids_known_905 and "ais-004" in {e.id for e in known}
    observed = view_as_observed_at(trace, "coast_guard", 1000 * S)
    assert all(e.event_time_ns <= 1000 * S and e.received_time_ns <= 1000 * S for e in observed)
    assert set(e.id for e in observed) <= set(e.id for e in known)
    assert view_as_known_at(trace, "harbour_ops", 0) == []


# --- decisions ---------------------------------------------------------------

def test_baseline_decisions(trace):
    duty = decision(trace, "duty_officer")
    assert duty.kind == "decision" and duty.source_id == "duty_officer"
    assert duty.payload["option"] == "monitor"
    assert duty.payload["predicate_value"] is False
    assert duty.payload["rule_id"] == "escalate_or_monitor" and duty.payload["at_ns"] == 1500 * S
    assert duty.event_time_ns == 1500 * S
    cw = decision(trace, "coast_watch")
    assert cw.payload["option"] == "stand_down" and cw.payload["predicate_value"] is True


def test_decisions_use_only_the_actor_role_view(trace, scenario):
    for d in trace.decisions:
        actor = next(a for a in scenario.actors if a.id == d.payload["actor"])
        known_ids = {e.id for e in view_as_known_at(trace, actor.role, d.payload["at_ns"])}
        assert set(d.payload["known_event_ids"]) == known_ids
        later = {e.id for e in trace.views[actor.role] if e.received_time_ns > d.payload["at_ns"]}
        assert not (set(d.payload["known_event_ids"]) & later)
    duty = decision(trace, "duty_officer")
    assert "correction-001" in duty.payload["known_event_ids"]
    assert "sighting-005" not in duty.payload["known_event_ids"]
    cw = decision(trace, "coast_watch")
    assert not any(i.startswith(("berth", "crew", "shift")) for i in cw.payload["known_event_ids"])


def test_correction_delay_flips_duty_officer_decision(scenario, trace):
    """The seeded latent defect: the baseline correction lands ~90s before the decision point."""
    corr = next(e for e in trace.views["harbour_ops"] if e.id == "correction-001")
    slack = 1500 * S - corr.received_time_ns
    assert 85 * S <= slack <= 95 * S
    still_ok = run(scenario, delivery_hook=delay_hook("correction-001", "harbour_ops", slack))
    assert decision(still_ok, "duty_officer").payload["option"] == "monitor"
    flipped = run(scenario, delivery_hook=delay_hook("correction-001", "harbour_ops", slack + 1))
    assert decision(flipped, "duty_officer").payload["option"] == "escalate"
    assert decision(flipped, "duty_officer").payload["predicate_value"] is True
    assert "correction-001" not in decision(flipped, "duty_officer").payload["known_event_ids"]
    assert flipped.truth.digest == trace.truth.digest  # the world did not change, only who knew what
    assert flipped.digest != trace.digest
    flipped_120 = run(scenario, delivery_hook=delay_hook("correction-001", "harbour_ops", 120 * S))
    assert decision(flipped_120, "duty_officer").payload["option"] == "escalate"
    small = run(scenario, delivery_hook=delay_hook("correction-001", "harbour_ops", 30 * S))
    assert decision(small, "duty_officer").payload["option"] == "monitor"


def test_dropping_ack_flips_coast_watch(scenario):
    def drop_ack(ev, role, rt, rng):
        return [] if (ev.id == "ack-001" and role == "coast_guard") else [(ev, rt)]
    t = run(scenario, delivery_hook=drop_ack)
    assert decision(t, "coast_watch").payload["option"] == "dispatch_patrol"
    assert "ack-001" not in {e.id for e in t.views["coast_guard"]}
    assert "ack-001" in {e.id for e in t.views["harbour_ops"]}


def test_hook_can_drop_duplicate_and_corrupt(scenario, trace):
    def hook(ev, role, rt, rng):
        if role != "coast_guard":
            return [(ev, rt)]
        if ev.id == "sighting-002":
            dup = ev.replace(id=ev.id + "#dup", provenance_ids=(ev.id,))
            return [(ev, rt), (dup, rt + 5 * S)]
        if ev.id == "weather-001":
            return [(ev, None)]
        if ev.id == "sighting-003":
            bad = ev.replace(payload={**ev.payload, "confidence": 0.95}, provenance_ids=(ev.id,), quality=0.1)
            return [(bad, rt)]
        return [(ev, rt)]
    t = run(scenario, delivery_hook=hook)
    cg = {e.id: e for e in t.views["coast_guard"]}
    assert "weather-001" not in cg and "weather-001" in {e.id for e in trace.views["coast_guard"]}
    assert "sighting-002#dup" in cg and cg["sighting-002#dup"].provenance_ids == ("sighting-002",)
    assert cg["sighting-003"].payload["confidence"] == 0.95 and cg["sighting-003"].provenance_ids == ("sighting-003",)
    assert t.views["harbour_ops"].digest == trace.views["harbour_ops"].digest
    assert t.truth.digest == trace.truth.digest


def test_hook_receives_forked_rng_and_is_deterministic(scenario):
    seen = []
    def hook(ev, role, rt, rng):
        assert isinstance(rng, DeterministicRNG)
        seen.append(rng.path)
        return [(ev, rt + int(rng.random() * S))]
    a = run(scenario, delivery_hook=hook)
    b = run(scenario, delivery_hook=hook)
    assert a.digest == b.digest
    assert seen[0] == ("deliver/harbour_ops/ais-001", "hook")


def test_hook_may_not_deliver_before_event_time(scenario):
    def hook(ev, role, rt, rng):
        return [(ev, ev.event_time_ns - 1)]
    with pytest.raises(ValueError):
        run(scenario, delivery_hook=hook)


def test_schedule_without_interventions_module_raises(scenario, monkeypatch):
    monkeypatch.setitem(sys.modules, "worldline.interventions", None)  # forces ImportError on import
    with pytest.raises(ImportError, match="hook_for_schedule"):
        run(scenario, schedule=({"op": "delay"},))


def test_schedule_uses_hook_for_schedule_when_available(scenario, trace, monkeypatch):
    import types
    fake = types.ModuleType("worldline.interventions")
    def hook_for_schedule(schedule):
        assert schedule == ("delay-correction",)
        return delay_hook("correction-001", "harbour_ops", 120 * S)
    fake.hook_for_schedule = hook_for_schedule
    fake.schedule_hash = lambda s: "sha256:" + "ab" * 32
    monkeypatch.setitem(sys.modules, "worldline.interventions", fake)
    t = run(scenario, schedule=("delay-correction",))
    assert decision(t, "duty_officer").payload["option"] == "escalate"
    assert t.fault_schedule_hash == "sha256:" + "ab" * 32
    assert t.branch_id == "root/" + t.fault_schedule_hash
    assert t.digest != trace.digest


# --- snapshots and branches --------------------------------------------------

def test_snapshot_freezes_prefix(trace):
    snap = snapshot_at(trace, 1200 * S)
    assert isinstance(snap, Snapshot)
    assert all(e.event_time_ns <= 1200 * S for e in snap.truth)
    assert all(e.received_time_ns <= 1200 * S for v in snap.views.values() for e in v)
    assert snap.decisions == []
    assert snap.clock["now_ns"] == 1200 * S
    assert snap.rng == {"seed": trace.seed, "path": []}
    for role, v in snap.views.items():
        assert [e.id for e in v] == [e.id for e in view_as_known_at(trace, role, 1200 * S)]
    snap2 = snapshot_at(trace, 2000 * S)
    assert [d.id for d in snap2.decisions] == ["decision:duty_officer:escalate_or_monitor"]


def test_branch_with_empty_schedule_reproduces_root(trace):
    for t_ns in (0, 1200 * S, 1409 * S, 1500 * S, 2500 * S, 3600 * S):
        b = branch(snapshot_at(trace, t_ns), ())
        assert b.digest == trace.digest, t_ns
        assert b.branch_id == "root"
        assert [d.content_hash for d in b.decisions] == [d.content_hash for d in trace.decisions]


def test_branch_diverges_only_after_snapshot(trace, scenario):
    snap = snapshot_at(trace, 1300 * S)
    b = branch(snap, (), delivery_hook=delay_hook("correction-001", "harbour_ops", 120 * S))
    assert b.truth.digest == trace.truth.digest
    for role in trace.views:
        prefix = [e.content_hash for e in snap.views[role]]
        assert [e.content_hash for e in b.views[role]][:len(prefix)] == prefix
        assert all(e.received_time_ns > 1300 * S for e in b.views[role].events[len(prefix):])
    assert decision(b, "duty_officer").payload["option"] == "escalate"
    assert decision(trace, "duty_officer").payload["option"] == "monitor"


def test_branch_cannot_rewrite_the_frozen_past(trace):
    # correction-001 happened at 1407s and reaches harbour_ops at ~1409.8s. Branching at 1408s it
    # is in flight; a hook that tries to deliver it at 1407.5s (before the snapshot) is clamped
    # to just after the snapshot, so the frozen prefix is never rewritten.
    snap = snapshot_at(trace, 1408 * S)
    assert "correction-001" not in {e.id for e in snap.views["harbour_ops"]}
    def hook(ev, role, rt, rng):
        if ev.id == "correction-001" and role == "harbour_ops":
            return [(ev, 1407 * S + S // 2)]
        return [(ev, rt)]
    b = branch(snap, (), delivery_hook=hook)
    p = next(e for e in b.views["harbour_ops"] if e.id == "correction-001")
    assert p.received_time_ns == 1408 * S + 1
    for role in b.views:
        frozen = [e.content_hash for e in snap.views[role]]
        assert [e.content_hash for e in b.views[role]][:len(frozen)] == frozen
    # decisions already taken stay taken
    snap2 = snapshot_at(trace, 1600 * S)
    b2 = branch(snap2, (), delivery_hook=delay_hook("correction-001", "harbour_ops", 120 * S))
    assert decision(b2, "duty_officer").content_hash == decision(trace, "duty_officer").content_hash
    assert b2.digest == trace.digest  # the delayed event was already received before the snapshot


def test_branch_rejects_foreign_snapshot(trace, scenario):
    other = Scenario.from_dict({**scenario.to_dict(), "id": "other"})
    with pytest.raises(ValueError):
        run(other, snapshot=snapshot_at(trace, 10 * S))


# --- serialization -----------------------------------------------------------

def test_trace_roundtrip_json(trace):
    d = json.loads(json.dumps(trace.to_dict()))
    again = Trace.from_dict(d)
    assert again.digest == trace.digest
    assert again.truth.digest == trace.truth.digest
    assert again.scenario == trace.scenario
    assert again.clock_end == trace.clock_end
    d["decisions"][0]["payload"]["option"] = "escalate"
    with pytest.raises(ValueError):
        Trace.from_dict(d)


def test_snapshot_roundtrip_json(trace):
    snap = snapshot_at(trace, 1500 * S)
    again = Snapshot.from_dict(json.loads(json.dumps(snap.to_dict())))
    assert again.digest == snap.digest
    assert branch(again, ()).digest == trace.digest
