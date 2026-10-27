"""Branching exploration of alternative worlds, and minimization of failing ones.

Two strategies share one budget so they can be compared honestly:
  random  — uniform random fault schedules drawn from all mutators over all events.
  guided  — interventions concentrated on events whose kinds a decision rule depends
            on, and on roles that make decisions. Read from the Scenario, never from
            truth-at-runtime, so the explorer has no information an engineer lacks.

A third, `windowed`, proposes only interventions that move a decision-relevant delivery across a
decision boundary (or drop / corrupt one the decision knows), with delays solved from the root
trace's receipt times (see reduction.py and docs/EXPLORER.md).

`reduce=True` skips a branch whose equivalence signature (reduction.schedule_signature) was already
explored, and `workers=N` runs branches in a process pool; neither changes which schedules are
proposed, and the result is identical for every worker count.
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
from .parallel import run_branches
from .reduction import DeliveryModel, build_model, flip_threshold, schedule_signature, window_candidates

STRATEGIES = ("random", "guided", "windowed")


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
    # reduce=True only: proposals not run because an explored branch (or the root) has the same
    # equivalence signature. Each: {"schedule", "equivalent_to": branch_id, "signature"}.
    skipped_equivalent: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["n_nodes"] = len(self.nodes)
        d["n_failing"] = len(self.failing)
        d["n_rejected"] = len(self.rejected)
        d["n_skipped_equivalent"] = len(self.skipped_equivalent)
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


def _windowed_pool(model: DeliveryModel, rng: DeterministicRNG) -> list[Intervention]:
    """Decision-window candidates; those predicted to flip a decision first, each tier shuffled by seed."""
    cands = window_candidates(model, rng.fork("candidates"))
    flips = [iv for iv, f in cands if f]
    rest = [iv for iv, f in cands if not f]
    return rng.fork("order/flip").shuffle(flips) + rng.fork("order/rest").shuffle(rest)


def explore(scenario: Scenario, invariants: dict[str, Invariant], *, budget: int, seed: int,
            strategy: str = "guided", schedule_size: int = 1, now_ns: int = 0,
            reduce: bool = False, workers: int = 1) -> ExplorationResult:
    """Explore up to `budget` proposed fault schedules.

    reduce   skip proposals whose equivalence signature matches the root or an earlier branch
             (recorded in `skipped_equivalent`); never changes which schedules are proposed.
    workers  run branches in a spawn process pool of this size; output is identical to workers=1.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    t0 = time.perf_counter_ns()
    rng = DeterministicRNG(seed, ("explore", strategy))
    root = run(scenario)
    root_verdicts = _verdicts_for(root, scenario, invariants, now_ns)
    baseline_fail = _baseline_failures(root_verdicts)
    nodes = [BranchNode("root", [], None, root.digest, [v.to_dict() for v in root_verdicts], 0,
                        sorted(baseline_fail))]
    model = build_model(scenario, root) if (reduce or strategy == "windowed") else None
    if strategy == "windowed":
        pool = _windowed_pool(model, rng.fork("pool"))
    else:
        pool = _candidate_pool(scenario, root, rng.fork("pool"), strategy, budget)
    if not pool:
        return ExplorationResult(scenario.id, seed, budget, strategy, nodes, [], root.digest,
                                 time.perf_counter_ns() - t0, schedule_size)

    # 1. propose (serial, seed-determined; independent of any branch outcome)
    planned: list[tuple[FaultSchedule, str | None]] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    owner: dict[str, str] = {}
    if reduce:
        root_sig = schedule_signature(model, invariants, ())
        if root_sig is not None:
            owner[root_sig] = "root"
    pick = rng.fork("pick")
    for i in range(budget):
        if strategy == "guided":
            # bias towards the front of the pool (decision-relevant) but keep exploring
            k = min(len(pool) - 1, int(abs(pick.gauss(0, max(1.0, len(pool) / 4)))))
            chosen = [pool[k]]
            for _ in range(schedule_size - 1):
                chosen.append(pool[min(len(pool) - 1, int(abs(pick.gauss(0, max(1.0, len(pool) / 4)))))])
        elif strategy == "windowed":
            # sweep the pool in order; extra slots (schedule_size > 1) drawn from the same pool
            if schedule_size == 1 and i >= len(pool):
                break
            chosen = [pool[i % len(pool)]] + [pick.choice(pool) for _ in range(schedule_size - 1)]
        else:
            chosen = [pick.choice(pool) for _ in range(schedule_size)]
        sched: FaultSchedule = tuple(chosen)
        sh = schedule_hash(sched)
        if sh in seen:
            continue
        seen.add(sh)
        sig = schedule_signature(model, invariants, sched) if reduce else None
        if sig is not None:
            if sig in owner:
                skipped.append({"schedule": [iv.to_dict() for iv in sched], "equivalent_to": owner[sig],
                                "signature": sig})
                continue
            owner[sig] = f"root/{sh}"
        planned.append((sched, sig))

    # 2. run (serial or process pool; results in plan order)
    results = run_branches(scenario, invariants, [[iv.to_dict() for iv in s] for s, _ in planned],
                           now_ns=now_ns, workers=workers)

    # 3. ordered merge
    failing: list[str] = []
    rejected: list[dict] = []
    for (sched, sig), r in zip(planned, results):
        if not r["ok"]:
            if sig is not None:  # the model predicted an admissible world; equivalents were skipped on that basis
                raise RuntimeError(f"reduction model admitted a schedule the runner rejected: {r['reason']}")
            rejected.append({"schedule": [iv.to_dict() for iv in sched], "reason": r["reason"]})
            continue
        fails = sorted(set(r["fails"]) - baseline_fail)
        node = BranchNode(r["branch_id"], [iv.to_dict() for iv in sched], "root", r["digest"],
                          r["verdicts"], r["cost_ns"], fails)
        nodes.append(node)
        if fails:
            failing.append(node.branch_id)
    return ExplorationResult(scenario.id, seed, budget, strategy, nodes, failing, root.digest,
                             time.perf_counter_ns() - t0, schedule_size, rejected, skipped)


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
    # When the minimal schedule is a single concrete delay: the smallest by_ns that still reproduces
    # the target failure, solved from root receipt times and checked with the oracle at t and t-1.
    threshold_ns: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def failure_oracle(scenario: Scenario, invariants: dict[str, Invariant], target_invariant: str,
                   now_ns: int = 0) -> Callable[[tuple], bool]:
    inv = {target_invariant: invariants[target_invariant]}
    ev = rule_evaluator(scenario)

    def oracle(schedule: tuple) -> bool:
        try:
            tr = run(scenario, tuple(schedule))
        except ValueError:  # subset violates the time rule: it cannot be the failing world
            return False
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
                              v.witness, v.explanation, tr.digest, res.trace,
                              delay_threshold(scenario, res.minimal, oracle))


def delay_threshold(scenario: Scenario, schedule: Sequence[Intervention],
                    oracle: Callable[[tuple], bool]) -> int | None:
    """For a single concrete delay: the smallest by_ns that makes `oracle` fail, else None.

    Candidates are the per-rule flip thresholds (reduction.flip_threshold); the smallest candidate t
    with oracle(t) True and oracle(t - 1) False is returned. Every decision is constant between delay
    breakpoints, so no delay below the first flip can change a decision.
    """
    if len(schedule) != 1 or schedule[0].op != "delay" or "event_id" not in schedule[0].target:
        return None
    iv = schedule[0]
    model = build_model(scenario)
    cands = sorted({t for r in model.rules
                    for t in [flip_threshold(scenario, iv.target["event_id"], iv.role, r.rule_id, model=model)]
                    if t is not None})

    def with_by(by: int) -> tuple:
        return (Intervention("delay", dict(iv.target), {**iv.params, "by_ns": by}),)

    for t in cands:
        if oracle(with_by(t)) and not oracle(with_by(t - 1)):
            return t
    return None
