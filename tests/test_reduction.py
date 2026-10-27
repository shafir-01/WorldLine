"""Reduction (equivalence signatures), delivery model, flip thresholds, windowed strategy, parallel runs."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

from worldline.core import DeterministicRNG, Verdict
from worldline.explorer.explore import _candidate_pool, explore, minimize, rule_evaluator
from worldline.explorer.reduction import (build_model, delay_breakpoints, flip_threshold, projection_modes,
                                          rule_kinds, schedule_signature, window_candidates)
from worldline.interventions import Intervention
from worldline.invariants import Invariant, check_all, load_invariants
from worldline.worlds import load_scenario, run

S = 1_000_000_000
ROOT = Path(__file__).resolve().parents[1]
TARGET = "decision.consistent_with_truth"
CORRECTION_RT = 1_409_781_717_903          # root receipt of correction-001 at harbour_ops
THRESHOLD = 1500 * S - CORRECTION_RT + 1   # 90.218282098 s: first delay that moves it past at_ns


@pytest.fixture(scope="module")
def sc():
    return load_scenario(ROOT / "scenarios" / "port_alert.yaml")


@pytest.fixture(scope="module")
def inv():
    return load_invariants(ROOT / "invariants")


@pytest.fixture(scope="module")
def root(sc):
    return run(sc)


@pytest.fixture(scope="module")
def model(sc, root):
    return build_model(sc, root)


def delay(eid: str, by_ns: int, role: str | None = "harbour_ops") -> Intervention:
    tgt = {"event_id": eid} if role is None else {"event_id": eid, "role": role}
    return Intervention("delay", tgt, {"by_ns": by_ns})


def results(sc, inv, sched) -> tuple | None:
    """Verdict result per invariant, or None when the runner rejects the schedule."""
    try:
        tr = run(sc, tuple(sched))
    except ValueError:
        return None
    vs = check_all(tr, inv, scenario=sc, evaluate_rule=rule_evaluator(sc), now_ns=0)
    return tuple((v.invariant_id, v.result.value) for v in vs)


@pytest.fixture(scope="module")
def full_pool(sc, root, model):
    """Every single intervention the default mutators propose over all events, plus window candidates."""
    pool = _candidate_pool(sc, root, DeterministicRNG(11).fork("pool"), "random", 100)
    pool += [iv for iv, _ in window_candidates(model, DeterministicRNG(11))]
    seen, out = set(), []
    for iv in pool:
        if iv.hash() not in seen:
            seen.add(iv.hash())
            out.append(iv)
    return out


# ---------------------------------------------------------------- delivery model

def test_model_reproduces_root_views(sc, root, model):
    views = model.predict(())
    for role, log in root.views.items():
        got = [(ev.id, rt) for ev, rt in views[role]]
        want = [(e.id, e.received_time_ns) for e in sorted(log, key=lambda e: (e.received_time_ns, *e.sort_key()))]
        assert got == want
    assert model.root_receipt("harbour_ops", "correction-001") == CORRECTION_RT


def test_model_predicts_runner_views_exactly(sc, model, full_pool):
    rng = DeterministicRNG(5)
    n_run = n_rej = 0
    for _ in range(250):
        sched = tuple(rng.choice(full_pool) for _ in range(rng.randint(1, 3)))
        pred = model.predict(sched)
        try:
            tr = run(sc, sched)
        except ValueError:
            assert pred is None, "model admitted a schedule the runner rejects"
            n_rej += 1
            continue
        assert pred is not None, "model rejected a schedule the runner admits"
        n_run += 1
        for role, log in tr.views.items():
            want = [(e.id, e.received_time_ns, e.replace(received_time_ns=None).content_hash)
                    for e in sorted(log, key=lambda e: (e.received_time_ns, *e.sort_key()))]
            assert [(ev.id, rt, ev.content_hash) for ev, rt in pred[role]] == want
        assert model.decide(pred) == {d.id: d.payload["option"] for d in tr.decisions}
    assert n_run > 50 and n_rej > 0


# ---------------------------------------------------------------- soundness of the signature

def _check_classes(sc, inv, model, schedules):
    classes: dict[str, set] = defaultdict(set)
    for sched in schedules:
        sig = schedule_signature(model, inv, sched)
        res = results(sc, inv, sched)
        if sig is None:
            assert res is None
            continue
        assert res is not None
        classes[sig].add(res)
    bad = {k: v for k, v in classes.items() if len(v) > 1}
    assert not bad, f"{len(bad)} signature classes contain differing verdicts"
    return classes


def test_signature_sound_exhaustive_single_interventions(sc, inv, model, full_pool):
    """Every single-intervention world the mutators can propose: equal signature => equal verdicts."""
    classes = _check_classes(sc, inv, model, [(iv,) for iv in full_pool])
    assert len(classes) < len(full_pool) / 2, "reduction should merge many single-intervention worlds"
    root_sig = schedule_signature(model, inv, ())
    assert root_sig in classes and classes[root_sig] == {results(sc, inv, ())}


def test_signature_sound_on_pairs_and_triples(sc, inv, model, full_pool):
    rng = DeterministicRNG(17)
    scheds = [tuple(rng.choice(full_pool) for _ in range(rng.randint(2, 3))) for _ in range(200)]
    _check_classes(sc, inv, model, scheds)


def test_equivalence_examples(model, inv):
    sig = lambda *s: schedule_signature(model, inv, s)  # noqa: E731
    # both push correction-001 past the decision and stay under every other boundary that matters
    assert sig(delay("correction-001", 300 * S)) == sig(delay("correction-001", 100 * S))
    assert sig(delay("correction-001", 60 * S)) != sig(delay("correction-001", 300 * S))
    # moving an AIS position no rule or invariant reads is the root world, up to verdicts
    assert sig(delay("ais-003", 5 * S)) == sig()
    # schedule order of independent interventions commutes
    a, b = delay("sighting-001", 5 * S), Intervention("drop", {"event_id": "weather-002"}, {})
    assert sig(a, b) == sig(b, a)


def test_unknown_projection_falls_back_to_exact_views(sc, root, inv):
    odd = {"absence.odd": Invariant("absence.odd", "1", "absence", params={"scope": "elsewhere", "match": {}})}
    m = build_model(sc, root)
    assert projection_modes(m, odd) == {"absence.odd": "exact"}
    assert set(projection_modes(m, inv).values()) == {"projected"}
    # exact mode: the AIS delay is no longer merged with the root
    assert schedule_signature(m, odd, (delay("ais-003", 5 * S),)) != schedule_signature(m, odd, ())


def test_rule_kinds():
    assert rule_kinds("count(kind='sighting', payload.confidence>=0.6, not_corrected) >= 2") == {"sighting", "correction"}
    assert rule_kinds("acknowledged(kind='alert', within_ns=600s)") == {"alert", "ack"}
    assert rule_kinds("count(payload.confidence>=0.6) >= 1") is None  # no kind filter: every kind is readable


# ---------------------------------------------------------------- reduced exploration

@pytest.mark.parametrize("strategy", ["random", "guided", "windowed"])
def test_reduction_keeps_coverage_and_cuts_runs(sc, inv, strategy):
    full = explore(sc, inv, budget=120, seed=2, strategy=strategy, schedule_size=2)
    red = explore(sc, inv, budget=120, seed=2, strategy=strategy, schedule_size=2, reduce=True)
    cov = lambda r: {tuple(n.failing_invariants) for n in r.nodes if n.failing_invariants}  # noqa: E731
    assert cov(red) == cov(full)
    runs = lambda r: len(r.nodes) - 1 + len(r.rejected)  # noqa: E731
    assert runs(red) + len(red.skipped_equivalent) == runs(full)
    assert red.skipped_equivalent and runs(red) < runs(full)
    assert red.to_dict()["n_skipped_equivalent"] == len(red.skipped_equivalent)
    assert full.skipped_equivalent == []
    # the explored nodes are exactly the full run's nodes minus the skipped ones, in the same order
    skipped = {tuple(map(str, s["schedule"])) for s in red.skipped_equivalent}
    assert [n.schedule for n in red.nodes] == [n.schedule for n in full.nodes
                                               if tuple(map(str, n.schedule)) not in skipped]


def test_skipped_branches_match_their_representative(sc, inv):
    red = explore(sc, inv, budget=100, seed=4, strategy="random", schedule_size=2, reduce=True)
    by_id = {n.branch_id: n for n in red.nodes}
    baseline = set(by_id["root"].failing_invariants)
    assert red.skipped_equivalent
    for s in red.skipped_equivalent:
        rep = by_id[s["equivalent_to"]]
        res = results(sc, inv, tuple(Intervention.from_dict(d) for d in s["schedule"]))
        fails = sorted({i for i, r in res if r == Verdict.FAIL.value} - baseline)
        assert fails == rep.failing_invariants


def test_reduced_exploration_is_deterministic(sc, inv):
    a = explore(sc, inv, budget=60, seed=9, strategy="random", schedule_size=2, reduce=True)
    b = explore(sc, inv, budget=60, seed=9, strategy="random", schedule_size=2, reduce=True)
    assert [n.trace_digest for n in a.nodes] == [n.trace_digest for n in b.nodes]
    assert a.skipped_equivalent == b.skipped_equivalent


# ---------------------------------------------------------------- flip thresholds

def test_flip_threshold_correction(sc, root, model):
    thr = flip_threshold(sc, "correction-001", "harbour_ops", "escalate_or_monitor", root=root)
    assert thr == THRESHOLD == 90_218_282_098
    opt = lambda by: {d.payload["rule_id"]: d.payload["option"]  # noqa: E731
                      for d in run(sc, (delay("correction-001", by),)).decisions}["escalate_or_monitor"]
    assert opt(thr) == "escalate" and opt(thr - 1) == "monitor"
    assert flip_threshold(sc, "correction-001", None, "escalate_or_monitor", model=model) == THRESHOLD


def test_flip_threshold_none_cases(sc, model):
    assert flip_threshold(sc, "ais-003", "harbour_ops", "escalate_or_monitor", model=model) is None
    assert flip_threshold(sc, "correction-001", "coast_guard", "escalate_or_monitor", model=model) is None
    with pytest.raises(KeyError):
        flip_threshold(sc, "correction-001", "harbour_ops", "nope", model=model)


def test_flip_threshold_ack_matches_runner(sc, model):
    thr = flip_threshold(sc, "ack-001", "coast_guard", "stand_down_if_acknowledged", model=model)
    assert thr is not None and thr in delay_breakpoints(model, "ack-001", model.rules[1])
    opt = lambda by: {d.payload["rule_id"]: d.payload["option"]  # noqa: E731
                      for d in run(sc, (delay("ack-001", by, "coast_guard"),)).decisions}["stand_down_if_acknowledged"]
    assert opt(thr) == "dispatch_patrol" and opt(thr - 1) == "stand_down"


def test_minimize_reports_threshold(sc, inv):
    sched = (delay("correction-001", 300 * S),
             Intervention("revoke", {}, {"role": "coast_guard", "from_ns": 3000 * S}),
             Intervention("clock_skew", {}, {"source_id": "tide_gauge", "offset_ns": 5 * S}))
    m = minimize(sc, inv, sched, TARGET)
    assert m.minimal == [delay("correction-001", 300 * S).to_dict()]
    assert m.threshold_ns == THRESHOLD and m.to_dict()["threshold_ns"] == THRESHOLD
    m2 = minimize(sc, inv, (Intervention("drop", {"event_id": "correction-001", "role": "harbour_ops"}, {}),), TARGET)
    assert m2.threshold_ns is None


# ---------------------------------------------------------------- windowed strategy

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_windowed_finds_decision_failure_first(sc, inv, seed):
    res = explore(sc, inv, budget=50, seed=seed, strategy="windowed")
    idx = next(i for i, n in enumerate(res.nodes) if TARGET in n.failing_invariants)
    assert idx == 1


def test_windowed_only_targets_decision_relevant_deliveries(sc, inv, model):
    res = explore(sc, inv, budget=200, seed=1, strategy="windowed")
    rule_of_role = {r.role: r for r in model.rules}
    assert len(res.nodes) - 1 == len(window_candidates(model, DeterministicRNG(0)))  # size 1: one sweep
    for n in res.nodes[1:]:
        (iv,) = n.schedule
        assert iv["op"] in ("delay", "drop", "corrupt")
        r = rule_of_role[iv["target"]["role"]]
        ev = next(e for e in run(sc).truth if e.id == iv["target"]["event_id"])
        assert ev.kind in r.kinds
        rt = model.root_receipt(r.role, ev.id)
        if iv["op"] == "delay":
            new = rt + iv["params"]["by_ns"]
            crosses = (rt <= r.at_ns < new) or (rt < r.at_ns - r.window_ns <= new)
            assert crosses, f"delay of {ev.id} does not cross a boundary of {r.rule_id}"
        else:
            assert rt <= r.at_ns


def test_windowed_includes_solved_threshold(sc, inv):
    res = explore(sc, inv, budget=200, seed=1, strategy="windowed")
    delays = {(n.schedule[0]["target"]["event_id"], n.schedule[0]["params"].get("by_ns")) for n in res.nodes[1:]}
    assert ("correction-001", THRESHOLD) in delays


# ---------------------------------------------------------------- parallel

def _strip(res):
    d = res.to_dict()
    d.pop("wall_ns")
    for n in d["nodes"]:
        n.pop("cost_ns")
    return d


@pytest.mark.parametrize("strategy,reduce", [("random", False), ("windowed", True)])
def test_parallel_matches_serial(sc, inv, strategy, reduce):
    a = explore(sc, inv, budget=40, seed=3, strategy=strategy, schedule_size=2, reduce=reduce, workers=1)
    b = explore(sc, inv, budget=40, seed=3, strategy=strategy, schedule_size=2, reduce=reduce, workers=2)
    assert _strip(a) == _strip(b)
