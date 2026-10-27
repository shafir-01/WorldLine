"""Run explorer branches serially or in a spawn-safe process pool, with an ordered merge.

Each branch is a pure function of (scenario, schedule): the worker rebuilds the Scenario from its
dict and the invariants from theirs once (pool initializer), runs the schedule, checks every
invariant and returns plain data. Results are merged in submission order (``Executor.map``), so the
output is identical to the serial path for any worker count; only timing fields differ.

Windows uses ``spawn``: every worker re-imports worldline (~hundreds of ms). Callers running
``explore(..., workers=N)`` from a script need the usual ``if __name__ == "__main__":`` guard.
"""
from __future__ import annotations

import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Sequence

from worldline.core import Verdict
from worldline.interventions import Intervention
from worldline.invariants import Invariant, check_all
from worldline.worlds import Scenario, run
from worldline.worlds.scenario import scenario_from_dict

_STATE: dict = {}


def run_one(scenario: Scenario, invariants: dict[str, Invariant], schedule_dicts: list[dict], now_ns: int,
            evaluate_rule) -> dict:
    """One branch -> {"ok": True, branch_id, digest, verdicts, fails, cost_ns} or {"ok": False, reason}."""
    sched = tuple(Intervention.from_dict(d) for d in schedule_dicts)
    t1 = time.perf_counter_ns()
    try:
        tr = run(scenario, sched)
    except ValueError as exc:  # schedule violates the time rule: recorded by the caller, never hidden
        return {"ok": False, "reason": str(exc)}
    vs = check_all(tr, invariants, scenario=scenario, evaluate_rule=evaluate_rule, now_ns=now_ns)
    return {"ok": True, "branch_id": tr.branch_id, "digest": tr.digest, "verdicts": [v.to_dict() for v in vs],
            "fails": sorted({v.invariant_id for v in vs if v.result == Verdict.FAIL}),
            "cost_ns": time.perf_counter_ns() - t1}


def _init(scenario_dict: dict, invariant_dicts: list[dict], now_ns: int) -> None:
    from .explore import rule_evaluator  # local: explore imports this module
    sc = scenario_from_dict(scenario_dict)
    _STATE["scenario"] = sc
    _STATE["invariants"] = {d["id"]: Invariant(**d) for d in invariant_dicts}
    _STATE["now_ns"] = now_ns
    _STATE["evaluate_rule"] = rule_evaluator(sc)


def _run_chunk(chunk: list[list[dict]]) -> list[dict]:
    s = _STATE
    return [run_one(s["scenario"], s["invariants"], d, s["now_ns"], s["evaluate_rule"]) for d in chunk]


def run_branches(scenario: Scenario, invariants: dict[str, Invariant], schedules: Sequence[list[dict]], *,
                 now_ns: int = 0, workers: int = 1) -> list[dict]:
    """Run every schedule (as intervention dicts); results come back in input order."""
    schedules = [list(s) for s in schedules]
    if workers <= 1 or len(schedules) < 2:
        from .explore import rule_evaluator
        ev = rule_evaluator(scenario)
        return [run_one(scenario, invariants, s, now_ns, ev) for s in schedules]
    workers = min(workers, len(schedules))
    # contiguous chunks, ~4 per worker: amortises IPC while keeping the pool balanced
    n_chunks = min(len(schedules), workers * 4)
    size = -(-len(schedules) // n_chunks)
    chunks = [schedules[i:i + size] for i in range(0, len(schedules), size)]
    ctx = multiprocessing.get_context("spawn")
    inv_dicts = [invariants[k].to_dict() for k in sorted(invariants)]
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_init,
                             initargs=(scenario.to_dict(), inv_dicts, now_ns)) as ex:
        out: list[dict] = []
        for part in ex.map(_run_chunk, chunks):  # map preserves submission order
            out.extend(part)
    return out
