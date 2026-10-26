"""Explorer: deterministic branching, guided bias, minimization, causal diff, rule evaluator."""
from __future__ import annotations

from pathlib import Path

import pytest

from worldline.core import DeterministicRNG, Verdict
import importlib
from worldline.explorer.diff import causal_diff
from worldline.explorer.explore import (
    _candidate_pool, decision_dependencies, explore, minimize, rule_evaluator,
)
from worldline.interventions import Intervention
from worldline.invariants import check_all, load_invariants
from worldline.worlds import load_scenario, run, view_as_known_at

S = 1_000_000_000
ROOT = Path(__file__).resolve().parents[1]
EXPLORE_MOD = importlib.import_module("worldline.explorer.explore")
TARGET = "decision.consistent_with_truth"


@pytest.fixture(scope="module")
def sc():
    return load_scenario(ROOT / "scenarios" / "port_alert.yaml")


@pytest.fixture(scope="module")
def inv():
    return load_invariants(ROOT / "invariants")


@pytest.fixture(scope="module")
def root(sc):
    return run(sc)


def delay_correction(by_s: int = 300) -> Intervention:
    return Intervention("delay", {"event_id": "correction-001", "role": "harbour_ops"}, {"by_ns": by_s * S})


FAILING_SCHEDULE = (
    delay_correction(300),
    Intervention("revoke", {}, {"role": "coast_guard", "from_ns": 3000 * S}),
    Intervention("clock_skew", {}, {"source_id": "tide_gauge", "offset_ns": 5 * S}),
    Intervention("clock_skew", {}, {"source_id": "met_office", "offset_ns": 10 * S}),
)


def _signature(res):
    return [(n.branch_id, n.trace_digest, tuple(n.failing_invariants)) for n in res.nodes], list(res.failing)


# ---------------------------------------------------------------- determinism

@pytest.mark.parametrize("strategy", ["random", "guided"])
def test_same_seed_is_identical(sc, inv, strategy):
    a = explore(sc, inv, budget=30, seed=42, strategy=strategy)
    b = explore(sc, inv, budget=30, seed=42, strategy=strategy)
    assert _signature(a) == _signature(b)
    assert a.root_digest == b.root_digest
    assert [r["schedule"] for r in a.rejected] == [r["schedule"] for r in b.rejected]


@pytest.mark.parametrize("strategy", ["random", "guided"])
def test_different_seeds_differ(sc, inv, strategy):
    a = explore(sc, inv, budget=30, seed=1, strategy=strategy)
    b = explore(sc, inv, budget=30, seed=2, strategy=strategy)
    assert [n.schedule for n in a.nodes[1:]] != [n.schedule for n in b.nodes[1:]]


def test_unknown_strategy_rejected(sc, inv):
    with pytest.raises(ValueError):
        explore(sc, inv, budget=5, seed=1, strategy="bogus")


# ---------------------------------------------------------------- shape of the result

@pytest.mark.parametrize("strategy", ["random", "guided"])
def test_root_node_present_and_clean(sc, inv, root, strategy):
    res = explore(sc, inv, budget=20, seed=7, strategy=strategy)
    r = res.nodes[0]
    assert r.branch_id == "root" and r.parent is None and r.schedule == []
    assert r.trace_digest == root.digest == res.root_digest
    assert r.failing_invariants == []
    assert "root" not in res.failing
    by_id = {v["invariant_id"]: v["result"] for v in r.verdicts}
    assert by_id["always.non_negative_capacity"] == Verdict.UNKNOWN.value
    assert all(v == Verdict.PASS.value for k, v in by_id.items() if k != "always.non_negative_capacity")


@pytest.mark.parametrize("strategy", ["random", "guided"])
def test_exploration_finds_failures(sc, inv, strategy):
    res = explore(sc, inv, budget=60, seed=42, strategy=strategy)
    assert res.failing, f"{strategy} found no failing branch in 60"
    assert res.first_failing() is not None
    assert res.first_failing(TARGET) is not None
    d = res.to_dict()
    assert d["n_failing"] == len(res.failing) and d["n_nodes"] == len(res.nodes)


@pytest.mark.parametrize("strategy", ["random", "guided"])
def test_every_failing_node_has_new_fail(sc, inv, strategy):
    res = explore(sc, inv, budget=60, seed=42, strategy=strategy)
    root_fail = {v["invariant_id"] for v in res.nodes[0].verdicts if v["result"] == Verdict.FAIL.value}
    assert root_fail == set()
    nodes = {n.branch_id: n for n in res.nodes}
    for bid in res.failing:
        n = nodes[bid]
        assert n.parent == "root" and n.schedule
        fails = {v["invariant_id"] for v in n.verdicts if v["result"] == Verdict.FAIL.value}
        assert set(n.failing_invariants) == fails - root_fail and fails
        for v in n.verdicts:
            if v["result"] == Verdict.FAIL.value:
                assert v["witness"], "every FAIL carries a witness"
    for n in res.nodes[1:]:
        assert (n.branch_id in res.failing) == bool(n.failing_invariants)


@pytest.mark.parametrize("size", [1, 2, 3])
def test_schedule_size_respected(sc, inv, size):
    res = explore(sc, inv, budget=15, seed=3, strategy="guided", schedule_size=size)
    assert res.schedule_size == size
    assert all(len(n.schedule) == size for n in res.nodes[1:])
    assert len(res.nodes) >= 2


def test_rejected_schedules_recorded_not_raised(sc, inv, monkeypatch):
    class BadSkew:
        op = "clock_skew"

        def propose(self, trace, rng, budget):
            # pushes every ais_feed event before its own event time: violates the time rule
            return [Intervention("clock_skew", {}, {"source_id": "ais_feed", "offset_ns": -(10 * 3600) * S})]

    monkeypatch.setattr(EXPLORE_MOD, "default_mutators", lambda: [BadSkew()])
    res = explore(sc, inv, budget=5, seed=1, strategy="random")
    assert len(res.rejected) == 1
    assert res.rejected[0]["schedule"][0]["params"]["source_id"] == "ais_feed"
    assert "received_time_ns" in res.rejected[0]["reason"]
    assert len(res.nodes) == 1 and res.failing == []
    assert res.to_dict()["n_rejected"] == 1


# ---------------------------------------------------------------- guided pool

def test_decision_dependencies(sc):
    deps = decision_dependencies(sc)
    assert set(deps) == {"escalate_or_monitor", "stand_down_if_acknowledged"}
    d = deps["escalate_or_monitor"]
    assert d["actor"] == "duty_officer" and d["role"] == "harbour_ops" and d["at_ns"] == 1500 * S
    assert d["kinds"] == ["correction", "sighting"]
    assert deps["stand_down_if_acknowledged"]["kinds"] == ["ack", "alert", "correction"]


def test_guided_pool_front_is_decision_relevant(sc, root):
    deps = decision_dependencies(sc)
    relevant = {k for d in deps.values() for k in d["kinds"]}
    roles = {d["role"] for d in deps.values()}
    truth = {e.id: e for e in root.truth}
    budget = 40
    guided = _candidate_pool(sc, root, DeterministicRNG(42, ("explore", "guided")).fork("pool"), "guided", budget)
    rnd = _candidate_pool(sc, root, DeterministicRNG(42, ("explore", "guided")).fork("pool"), "random", budget)
    assert len(guided) == len(rnd) and {g.hash() for g in guided} == {p.hash() for p in rnd}

    def relevant_iv(iv):
        eid = iv.target.get("event_id")
        role = iv.role
        if eid:
            ev = truth[eid.split("#")[0]]
            return ev.kind in relevant and (role is None or role in roles)
        return iv.op in ("revoke", "clock_skew") and (role is None or role in roles)

    n_rel = sum(1 for iv in rnd if relevant_iv(iv))
    assert 0 < n_rel < len(rnd), "scenario must mix relevant and irrelevant candidates for this test to bite"
    assert all(relevant_iv(iv) for iv in guided[:n_rel])
    assert not any(relevant_iv(iv) for iv in guided[n_rel:])
    assert any(iv.target.get("event_id") == "correction-001" for iv in guided[:n_rel])


# ---------------------------------------------------------------- minimization

def test_delay_flips_decision(sc, inv, root):
    tr = run(sc, (delay_correction(91),))
    opts = {d.payload["rule_id"]: d.payload["option"] for d in tr.decisions}
    assert opts["escalate_or_monitor"] == "escalate"
    base = {d.payload["rule_id"]: d.payload["option"] for d in root.decisions}
    assert base == {"escalate_or_monitor": "monitor", "stand_down_if_acknowledged": "stand_down"}
    vs = check_all(tr, inv, scenario=sc, evaluate_rule=rule_evaluator(sc), now_ns=0)
    v = next(x for x in vs if x.invariant_id == TARGET)
    assert v.result == Verdict.FAIL and v.witness[0].startswith("correction-001")
    tr_short = run(sc, (delay_correction(85),))
    assert {d.payload["rule_id"]: d.payload["option"] for d in tr_short.decisions}["escalate_or_monitor"] == "monitor"


def test_minimize_end_to_end(sc, inv):
    res = minimize(sc, inv, FAILING_SCHEDULE, TARGET)
    assert len(res.original) == 4
    assert res.minimal == [delay_correction(300).to_dict()]
    assert res.one_minimal_verified is True
    assert res.witness and res.witness[0].startswith("correction-001")
    assert res.target_invariant == TARGET and res.scenario_id == "port_alert"
    assert res.oracle_calls >= 1 and res.trace_digest == run(sc, (delay_correction(300),)).digest
    d = res.to_dict()
    assert d["minimal"] == res.minimal and isinstance(d["steps"], list)


def test_minimize_is_deterministic(sc, inv):
    a, b = minimize(sc, inv, FAILING_SCHEDULE, TARGET), minimize(sc, inv, FAILING_SCHEDULE, TARGET)
    assert a.to_dict() == b.to_dict()


def test_minimize_rejects_non_failing_schedule(sc, inv):
    with pytest.raises(ValueError):
        minimize(sc, inv, (delay_correction(10),), TARGET)


# ---------------------------------------------------------------- causal diff

def test_causal_diff_root_vs_delayed(sc, root):
    tr = run(sc, (delay_correction(300),))
    d = causal_diff(root, tr)
    assert d["a"] == "root" and d["b"] == tr.branch_id
    assert d["truth_identical"] is True and d["differs"] is True
    assert len(d["decisions"]) == 1
    dec = d["decisions"][0]
    assert dec["actor"] == "duty_officer" and dec["rule_id"] == "escalate_or_monitor"
    assert (dec["option_a"], dec["option_b"]) == ("monitor", "escalate")
    assert dec["known_only_in_a"] == ["correction-001"] and dec["known_only_in_b"] == []
    ho = d["views"]["harbour_ops"]
    assert ho["only_in_a"] == [] and ho["only_in_b"] == [] and ho["payload_changed"] == []
    assert [m["event_id"] for m in ho["received_time_moved"]] == ["correction-001"]
    assert ho["received_time_moved"][0]["delta_ns"] == 300 * S
    for role in ("coast_guard", "evaluator"):
        v = d["views"][role]
        assert not (v["only_in_a"] or v["only_in_b"] or v["received_time_moved"] or v["payload_changed"])


def test_causal_diff_identity(root):
    d = causal_diff(root, root)
    assert d["differs"] is False and d["decisions"] == []


# ---------------------------------------------------------------- rule evaluator

def test_rule_evaluator_agrees_with_runner(sc, root):
    ev = rule_evaluator(sc)
    role_of = {a.id: a.role for a in sc.actors}
    for tr in (root, run(sc, (delay_correction(300),))):
        assert len(tr.decisions) == 2
        for d in tr.decisions:
            p = d.payload
            known = view_as_known_at(tr, role_of[p["actor"]], p["at_ns"])
            assert ev(p["rule_id"], known) == p["option"]
            assert sorted(e.id for e in known) == sorted(p["known_event_ids"])


def test_rule_evaluator_over_truth_differs_from_stale_view(sc, root):
    ev = rule_evaluator(sc)
    truth = [e.with_receipt(e.event_time_ns) for e in root.truth if e.event_time_ns <= 1500 * S]
    assert ev("escalate_or_monitor", truth) == "monitor"
    stale = [e for e in truth if e.id != "correction-001"]
    assert ev("escalate_or_monitor", stale) == "escalate"
