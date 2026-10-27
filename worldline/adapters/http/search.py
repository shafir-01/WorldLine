"""explore / minimize / challenge, with every oracle call answered by the real service.

These mirror ``worldline.explorer.explore``/``minimize`` and ``worldline.genome.challenge`` line for
line where they can, and reuse their pieces (candidate pool from the same mutators, the same
BranchNode / ExplorationResult / MinimizationResult / ChallengeResult shapes, ``ddmin``, the
genome's ``_check_preconditions`` and ``_instantiate``). The one substitution: wherever the
simulator calls ``run(scenario, schedule)``, these call ``HttpWorld.run(schedule)``, and the
decision evaluator is the service's own rule function instead of the scenario DSL.
"""
from __future__ import annotations

import time
from typing import Sequence

from worldline.core import DeterministicRNG, Verdict
from worldline.explorer import BranchNode, ExplorationResult, MinimizationResult, ddmin, one_minimal_check
from worldline.explorer.explore import _candidate_pool
from worldline.genome import ChallengeResult, FailureGenome
from worldline.genome.genome import _check_preconditions, _instantiate
from worldline.interventions import FaultSchedule, Intervention, schedule_hash
from worldline.invariants import Invariant, VerdictRecord, check_all
from worldline.worlds import Scenario

from .driver import HttpTrace, HttpWorld, ServiceUnderTest


def _world(app_or_world: ServiceUnderTest | HttpWorld, scenario: Scenario | None) -> HttpWorld:
    if isinstance(app_or_world, HttpWorld):
        return app_or_world
    if scenario is None:
        raise ValueError("a scenario is required when passing a ServiceUnderTest")
    return HttpWorld(app_or_world, scenario)


def verdicts_http(world: HttpWorld, trace: HttpTrace, invariants: dict[str, Invariant],
                  now_ns: int = 0) -> list[VerdictRecord]:
    """``check_all`` unchanged; scenario = the projection, evaluate_rule = the service's own rule."""
    return check_all(trace, invariants, scenario=world.scenario, evaluate_rule=world.evaluate_rule, now_ns=now_ns)


# ------------------------------------------------------------------ explore

def explore_http(app: ServiceUnderTest | HttpWorld, scenario: Scenario | None, invariants: dict[str, Invariant], *,
                 budget: int = 60, seed: int = 42, strategy: str = "guided", schedule_size: int = 3,
                 now_ns: int = 0) -> ExplorationResult:
    world = _world(app, scenario)
    t0 = time.perf_counter_ns()
    rng = DeterministicRNG(seed, ("explore", strategy))
    root = world.run()
    root_vs = verdicts_http(world, root, invariants, now_ns)
    baseline_fail = {v.invariant_id for v in root_vs if v.result == Verdict.FAIL}
    nodes = [BranchNode("root", [], None, root.digest, [v.to_dict() for v in root_vs], 0, sorted(baseline_fail))]
    pool = _candidate_pool(world.scenario, root, rng.fork("pool"), strategy, budget)
    failing: list[str] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    pick = rng.fork("pick")
    for _ in range(budget if pool else 0):
        if strategy == "guided":
            chosen = [pool[min(len(pool) - 1, int(abs(pick.gauss(0, max(1.0, len(pool) / 4)))))]
                      for _ in range(schedule_size)]
        else:
            chosen = [pick.choice(pool) for _ in range(schedule_size)]
        sched: FaultSchedule = tuple(chosen)
        sh = schedule_hash(sched)
        if sh in seen:
            continue
        seen.add(sh)
        t1 = time.perf_counter_ns()
        try:
            tr = world.run(sched)
        except ValueError as exc:  # schedule violates the time rule: record, do not hide
            rejected.append({"schedule": [iv.to_dict() for iv in sched], "reason": str(exc)})
            continue
        vs = verdicts_http(world, tr, invariants, now_ns)
        fails = sorted({v.invariant_id for v in vs if v.result == Verdict.FAIL} - baseline_fail)
        node = BranchNode(tr.branch_id, [iv.to_dict() for iv in sched], "root", tr.digest,
                          [v.to_dict() for v in vs], time.perf_counter_ns() - t1, fails)
        nodes.append(node)
        if fails:
            failing.append(node.branch_id)
    return ExplorationResult(world.scenario.id, seed, budget, strategy, nodes, failing, root.digest,
                             time.perf_counter_ns() - t0, schedule_size, rejected)


# ------------------------------------------------------------------ minimize

def failure_oracle_http(world: HttpWorld, invariants: dict[str, Invariant], target: str, now_ns: int = 0):
    inv = {target: invariants[target]}

    def oracle(schedule: tuple) -> bool:
        try:
            tr = world.run(tuple(schedule))
        except ValueError:
            return False
        return any(v.result == Verdict.FAIL for v in verdicts_http(world, tr, inv, now_ns))

    return oracle


def minimize_http(app: ServiceUnderTest | HttpWorld, scenario: Scenario | None, invariants: dict[str, Invariant],
                  failing_schedule: Sequence[Intervention], target_invariant: str,
                  now_ns: int = 0) -> MinimizationResult:
    world = _world(app, scenario)
    oracle = failure_oracle_http(world, invariants, target_invariant, now_ns)
    res = ddmin(tuple(failing_schedule), oracle)
    verified = one_minimal_check(res.minimal, oracle)
    tr = world.run(tuple(res.minimal))
    vs = verdicts_http(world, tr, {target_invariant: invariants[target_invariant]}, now_ns)
    v = next(x for x in vs if x.result == Verdict.FAIL)
    return MinimizationResult(world.scenario.id, target_invariant, [i.to_dict() for i in failing_schedule],
                              [i.to_dict() for i in res.minimal], verified, res.oracle_calls, res.cache_hits,
                              v.witness, v.explanation, tr.digest, res.trace)


# ------------------------------------------------------------------ genome challenge

def challenge_http(genome: FailureGenome, scenario: Scenario | None, app: ServiceUnderTest | HttpWorld,
                   invariants: dict[str, Invariant], *, now_ns: int = 0) -> ChallengeResult:
    """``worldline.genome.challenge`` with the root and the instantiated branch run against the service."""
    world = _world(app, scenario)
    sc = world.scenario
    root = world.run()
    pre = _check_preconditions(genome, sc, root)
    met = all(pre.values())
    schedule, rid, note = _instantiate(genome, sc, root)
    if not met or not schedule:
        return ChallengeResult(genome.id, sc.id, met, pre, [i.to_dict() for i in schedule], rid, [], False,
                               None, f"not instantiated: preconditions_met={met}; {note}")
    tr = world.run(tuple(schedule))
    target = ({genome.violated_invariant: invariants[genome.violated_invariant]}
              if genome.violated_invariant in invariants else invariants)
    vs = verdicts_http(world, tr, target, now_ns)
    root_fail = {v.invariant_id for v in verdicts_http(world, root, target, now_ns) if v.result == Verdict.FAIL}
    new_fail = [v for v in vs if v.result == Verdict.FAIL and v.invariant_id not in root_fail]
    reproduced = bool(new_fail)
    expl = (f"reproduced against service {world.service.name} on {sc.id}: {new_fail[0].explanation}" if reproduced
            else f"preconditions met, mechanism instantiated ({note}), but no new {genome.violated_invariant} FAIL")
    return ChallengeResult(genome.id, sc.id, met, pre, [i.to_dict() for i in schedule], rid,
                           [v.to_dict() for v in vs], reproduced, tr.digest if reproduced else None, expl)
