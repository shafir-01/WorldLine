"""Branching exploration of alternative worlds, and minimization of failing ones.

Two strategies share one budget so they can be compared honestly:
  random  — uniform random fault schedules drawn from all mutators over all events.
  guided  — interventions concentrated on events whose kinds a decision rule depends
            on, and on roles that make decisions. Read from the Scenario, never from
            truth-at-runtime, so the explorer has no information an engineer lacks.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Sequence

from worldline.core import DeterministicRNG, Event, Verdict
from worldline.interventions import FaultSchedule, Intervention, schedule_hash, default_mutators
from worldline.invariants import Invariant, VerdictRecord, check_all
from worldline.worlds import Scenario, Trace, run
from worldline.worlds.predicate import parse_predicate

from .ddmin import DDMinResult, ddmin, one_minimal_check


# ------------------------------------------------------------------ rule evaluator

def rule_evaluator(scenario: Scenario) -> Callable[[str, list[Event]], str]:
    """Evaluate a decision rule over an arbitrary event set (used by the `decision` invariant
    to ask: what would the rule have said over truth-as-of the decision time?)."""
    rules = {}
    for actor in scenario.actors:
        for rule in actor.decision_rules:
            rules[rule.id] = rule

    def evaluate(rule_id: str, events: list[Event]) -> str:
        rule = rules[rule_id]
        known = [e if e.received_time_ns is not None else e.with_receipt(e.event_time_ns) for e in events]
        known = [e for e in known if e.received_time_ns <= rule.at_ns]
        windowed = [e for e in known if e.received_time_ns >= rule.at_ns - rule.window_ns]
        return rule.then if parse_predicate(rule.when).evaluate(known, windowed) else rule.otherwise

    return evaluate


def decision_dependencies(scenario: Scenario) -> dict[str, dict]:
    """rule_id -> {actor, role, at_ns, window_ns, kinds}. The guided strategy's only input."""
    out = {}
    for actor in scenario.actors:
        for rule in actor.decision_rules:
            kinds = set(parse_predicate(rule.when).kinds)
            # a correction of a depended-on kind is itself decision-relevant
            kinds |= {"correction"} if kinds else set()
            out[rule.id] = {"actor": actor.id, "role": actor.role, "at_ns": rule.at_ns,
                            "window_ns": rule.window_ns, "kinds": sorted(kinds)}
    return out


# ------------------------------------------------------------------ results

@dataclass
class BranchNode:
    branch_id: str
    schedule: list[dict]
    parent: str | None
    trace_digest: str
    verdicts: list[dict]
    cost_ns: int
    failing_invariants: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExplorationResult:
    scenario_id: str
    seed: int
    budget: int
    strategy: str
    nodes: list[BranchNode]
    failing: list[str]
    root_digest: str
    wall_ns: int
    schedule_size: int
    rejected: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["n_nodes"] = len(self.nodes)
        d["n_failing"] = len(self.failing)
        d["n_rejected"] = len(self.rejected)
        d["failing_rate_per_100"] = (100.0 * len(self.failing) / max(1, len(self.nodes) - 1))
        return d

    def first_failing(self, invariant_id: str | None = None) -> BranchNode | None:
        for n in self.nodes:
            if n.failing_invariants and (invariant_id is None or invariant_id in n.failing_invariants):
                return n
        return None


# ------------------------------------------------------------------ exploration

def _verdicts_for(trace: Trace, scenario: Scenario, invariants: dict[str, Invariant], now_ns: int) -> list[VerdictRecord]:
    return check_all(trace, invariants, scenario=scenario, evaluate_rule=rule_evaluator(scenario), now_ns=now_ns)


def _baseline_failures(root_verdicts: list[VerdictRecord]) -> set[str]:
    return {v.invariant_id for v in root_verdicts if v.result == Verdict.FAIL}


def _candidate_pool(scenario: Scenario, root: Trace, rng: DeterministicRNG, strategy: str, budget: int) -> list[Intervention]:
    pool: list[Intervention] = []
    for m in default_mutators():
        pool.extend(m.propose(root, rng.fork(f"mut/{m.op}"), budget))
    if strategy == "random":
        return pool
    if strategy != "guided":
        raise ValueError(f"unknown strategy {strategy!r}")
    deps = decision_dependencies(scenario)
    relevant_kinds = {k for d in deps.values() for k in d["kinds"]}
    deciding_roles = {d["role"] for d in deps.values()}
    truth = {e.id: e for e in root.truth}
    guided = []
    for iv in pool:
        eid = iv.target.get("event_id")
        role = iv.target.get("role") or iv.params.get("role")
        ev = truth.get(eid.split("#")[0]) if eid else None
        if ev is not None and ev.kind in relevant_kinds and (role is None or role in deciding_roles):
            guided.append(iv)
        elif ev is None and iv.op in ("revoke", "clock_skew") and (role in deciding_roles or role is None):
            guided.append(iv)
    # guided keeps the targeted pool first, then falls back to the rest so budget is never wasted
    seen = {g.hash() for g in guided}
    return guided + [p for p in pool if p.hash() not in seen]


def explore(scenario: Scenario, invariants: dict[str, Invariant], *, budget: int, seed: int,
            strategy: str = "guided", schedule_size: int = 1, now_ns: int = 0) -> ExplorationResult:
    t0 = time.perf_counter_ns()
    rng = DeterministicRNG(seed, ("explore", strategy))
    root = run(scenario)
    root_verdicts = _verdicts_for(root, scenario, invariants, now_ns)
    baseline_fail = _baseline_failures(root_verdicts)
    nodes = [BranchNode("root", [], None, root.digest, [v.to_dict() for v in root_verdicts], 0,
                        sorted(baseline_fail))]
    pool = _candidate_pool(scenario, root, rng.fork("pool"), strategy, budget)
    if not pool:
        return ExplorationResult(scenario.id, seed, budget, strategy, nodes, [], root.digest,
                                 time.perf_counter_ns() - t0, schedule_size)
    failing: list[str] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    pick = rng.fork("pick")
    for i in range(budget):
        if strategy == "guided":
            # bias towards the front of the pool (decision-relevant) but keep exploring
            k = min(len(pool) - 1, int(abs(pick.gauss(0, max(1.0, len(pool) / 4)))))
            chosen = [pool[k]]
            for _ in range(schedule_size - 1):
                chosen.append(pool[min(len(pool) - 1, int(abs(pick.gauss(0, max(1.0, len(pool) / 4)))))])
        else:
            chosen = [pick.choice(pool) for _ in range(schedule_size)]
        sched: FaultSchedule = tuple(chosen)
        sh = schedule_hash(sched)
        if sh in seen:
            continue
        seen.add(sh)
        t1 = time.perf_counter_ns()
        try:
            tr = run(scenario, sched)
        except ValueError as exc:  # schedule violates the time rule (e.g. negative skew): record, do not hide
            rejected.append({"schedule": [iv.to_dict() for iv in sched], "reason": str(exc)})
            continue
        verdicts = _verdicts_for(tr, scenario, invariants, now_ns)
        fails = sorted({v.invariant_id for v in verdicts if v.result == Verdict.FAIL} - baseline_fail)
        node = BranchNode(tr.branch_id, [iv.to_dict() for iv in sched], "root", tr.digest,
                          [v.to_dict() for v in verdicts], time.perf_counter_ns() - t1, fails)
        nodes.append(node)
        if fails:
            failing.append(node.branch_id)
    return ExplorationResult(scenario.id, seed, budget, strategy, nodes, failing, root.digest,
                             time.perf_counter_ns() - t0, schedule_size, rejected)


# ------------------------------------------------------------------ minimization

@dataclass
class MinimizationResult:
    scenario_id: str
    target_invariant: str
    original: list[dict]
    minimal: list[dict]
    one_minimal_verified: bool
    oracle_calls: int
    cache_hits: int
    witness: list[str]
    explanation: str
    trace_digest: str
    steps: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


def failure_oracle(scenario: Scenario, invariants: dict[str, Invariant], target_invariant: str,
                   now_ns: int = 0) -> Callable[[tuple], bool]:
    inv = {target_invariant: invariants[target_invariant]}
    ev = rule_evaluator(scenario)

    def oracle(schedule: tuple) -> bool:
        tr = run(scenario, tuple(schedule))
        vs = check_all(tr, inv, scenario=scenario, evaluate_rule=ev, now_ns=now_ns)
        return any(v.result == Verdict.FAIL for v in vs)

    return oracle


def minimize(scenario: Scenario, invariants: dict[str, Invariant], failing_schedule: Sequence[Intervention],
             target_invariant: str, now_ns: int = 0) -> MinimizationResult:
    oracle = failure_oracle(scenario, invariants, target_invariant, now_ns)
    res: DDMinResult = ddmin(tuple(failing_schedule), oracle)
    verified = one_minimal_check(res.minimal, oracle)
    tr = run(scenario, tuple(res.minimal))
    vs = check_all(tr, {target_invariant: invariants[target_invariant]}, scenario=scenario,
                   evaluate_rule=rule_evaluator(scenario), now_ns=now_ns)
    v = next(x for x in vs if x.result == Verdict.FAIL)
    return MinimizationResult(scenario.id, target_invariant, [i.to_dict() for i in failing_schedule],
                              [i.to_dict() for i in res.minimal], verified, res.oracle_calls, res.cache_hits,
                              v.witness, v.explanation, tr.digest, res.trace)
