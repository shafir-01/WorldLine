"""WORLDLINE benchmark: guided vs random exploration, and ddmin minimization, all measured.

    uv run python experiments/benchmark.py --seeds 5 --budget 150 --out experiments/out/benchmark.json

Nothing in the output is typed in by hand. Every number comes from code that ran in this
process; the "definitions" block says exactly how each one was computed.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

from worldline.cli import common as C
from worldline.explorer import explore, minimize
from worldline.interventions import schedule_hash

STRATEGIES = ("guided", "random")

DEFINITIONS = {
    "n_nodes": "Branches actually run for the strategy, root included. Below budget+1 when schedules repeat "
               "(deduplicated by schedule hash) or are rejected for violating the time rule.",
    "n_failing": "Branches whose verdicts contain a FAIL for an invariant that PASSes on the root trace.",
    "failing_rate_per_100": "100 * n_failing / max(1, n_nodes - 1): failing branches per 100 non-root branches run.",
    "first_decision_fail_index": "Index in exploration order (root = 0) of the first branch failing "
                                 "decision.consistent_with_truth; null if none within budget.",
    "wall_ms": "Wall-clock milliseconds for explore() including every branch run and verdict check "
               "(time.perf_counter_ns, this machine, see environment).",
    "original_size": "Number of interventions in the failing schedule handed to ddmin.",
    "minimal_size": "Number of interventions in the 1-minimal schedule ddmin returned.",
    "oracle_calls": "Distinct schedules ddmin executed (cache hits excluded).",
    "one_minimal_verified": "Independent check: the minimal schedule fails and every single-intervention removal passes.",
    "aggregate": "mean and median over seeds per strategy; first_decision_fail_index aggregates over non-null values "
                 "and reports how many seeds found none.",
}


def _agg(values: list) -> dict:
    vals = [v for v in values if v is not None]
    return {"mean": statistics.fmean(vals) if vals else None,
            "median": statistics.median(vals) if vals else None,
            "n": len(vals), "n_null": len(values) - len(vals)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=5, help="Seeds 1..N")
    ap.add_argument("--budget", type=int, default=150)
    ap.add_argument("--schedule-size", type=int, default=3)
    ap.add_argument("--scenario", default="scenarios/port_alert.yaml")
    ap.add_argument("--minimize-cases", type=int, default=5, help="Failing schedules of size 3-4 to minimize")
    ap.add_argument("--out", default="experiments/out/benchmark.json")
    args = ap.parse_args(argv)

    console = Console(highlight=False)
    scenario_path = C.resolve_repo_path(args.scenario)
    sc, inv = C.load_world(scenario_path)
    target = C.TARGET_INVARIANT

    # ---- exploration: both strategies, seeds 1..N, one shared budget
    rows: list[dict] = []
    failing_pool: list[tuple[int, str, list[dict]]] = []  # (seed, strategy, schedule dicts) for minimization
    for seed in range(1, args.seeds + 1):
        for s in STRATEGIES:
            res = explore(sc, inv, budget=args.budget, seed=seed, strategy=s, schedule_size=args.schedule_size)
            d = res.to_dict()
            row = {"seed": seed, "strategy": s, **C.comparison_row(d)}
            rows.append(row)
            for n in res.nodes:
                if target in n.failing_invariants and 3 <= len(n.schedule) <= 4:
                    failing_pool.append((seed, s, n.schedule))
            console.print(f"seed {seed} {s:6} nodes {row['n_nodes']:4} failing {row['n_failing']:3} "
                          f"first decision FAIL idx {row['first_decision_fail_index']} wall {row['wall_ms']:.0f} ms",
                          markup=False)

    # Size-4 schedules only arise with --schedule-size 4; with the default 3 the pool is size 3, which the
    # definitions record. To give ddmin 4-element inputs as well, also explore once per seed at size 4.
    if args.schedule_size < 4:
        for seed in range(1, args.seeds + 1):
            res = explore(sc, inv, budget=args.budget, seed=seed, strategy="guided", schedule_size=4)
            for n in res.nodes:
                if target in n.failing_invariants and len(n.schedule) == 4:
                    failing_pool.append((seed, "guided@size4", n.schedule))

    aggregate = {}
    for s in STRATEGIES:
        sub = [r for r in rows if r["strategy"] == s]
        aggregate[s] = {k: _agg([r[k] for r in sub])
                        for k in ("n_nodes", "n_failing", "failing_rate_per_100", "first_decision_fail_index", "wall_ms")}

    # ---- minimization: distinct failing schedules of size 3-4
    seen: set[str] = set()
    cases: list[dict] = []
    skipped: list[dict] = []
    # spread the sample over seeds and over both sizes: round-robin on seed, alternating size 4 / size 3
    picks: list[tuple[int, str, list[dict]]] = []
    buckets = {(seed, size): [t for t in failing_pool if t[0] == seed and len(t[2]) == size]
               for seed in range(1, args.seeds + 1) for size in (4, 3)}
    while len(picks) < args.minimize_cases and any(buckets.values()):
        for seed in range(1, args.seeds + 1):
            for size in (4, 3) if len(picks) % 2 == 0 else (3, 4):
                if buckets[(seed, size)] and len(picks) < args.minimize_cases:
                    picks.append(buckets[(seed, size)].pop(0))
                    break
    for seed, s, sched_dicts in picks:
        schedule = C.schedule_from_dicts(sched_dicts)
        key = schedule_hash(schedule)
        if key in seen:
            continue
        seen.add(key)
        t0 = time.perf_counter_ns()
        try:
            m = minimize(sc, inv, schedule, target)
        except ValueError as exc:  # a subset violated the time rule; recorded, not hidden
            skipped.append({"seed": seed, "strategy": s, "schedule": sched_dicts, "reason": str(exc)})
            continue
        cases.append({"seed": seed, "strategy": s, "original_size": len(m.original), "minimal_size": len(m.minimal),
                      "oracle_calls": m.oracle_calls, "cache_hits": m.cache_hits,
                      "one_minimal_verified": m.one_minimal_verified, "wall_ms": (time.perf_counter_ns() - t0) / 1e6,
                      "minimal": m.minimal, "witness": m.witness})
        console.print(f"minimize seed {seed} {s}: {len(m.original)} -> {len(m.minimal)} in {m.oracle_calls} oracle calls, "
                      f"1-minimal {m.one_minimal_verified}", markup=False)

    min_agg = {k: _agg([c[k] for c in cases]) for k in ("original_size", "minimal_size", "oracle_calls", "wall_ms")}
    min_agg["one_minimal_verified_all"] = all(c["one_minimal_verified"] for c in cases) if cases else None

    out = {
        "scenario_id": sc.id, "scenario_path": str(args.scenario), "target_invariant": target,
        "budget": args.budget, "schedule_size": args.schedule_size, "seeds": list(range(1, args.seeds + 1)),
        "environment": C.environment(),
        "definitions": DEFINITIONS,
        "exploration": {"rows": rows, "aggregate": aggregate},
        "minimization": {"cases": cases, "skipped": skipped, "aggregate": min_agg},
        "claim_ceiling": "EMPIRICALLY_VALIDATED on one synthetic scenario and this machine; wall times are not portable",
    }
    C.write_json(args.out, out)

    t = Table(title=f"{sc.id}: budget {args.budget}, seeds 1..{args.seeds} (mean / median)", box=box.ASCII)
    for col in ("strategy", "nodes", "failing", "fail/100", "first decision FAIL idx", "wall ms"):
        t.add_column(col)
    for s in STRATEGIES:
        a = aggregate[s]

        def f(k, fmt="{:.1f}"):
            v = a[k]
            if v["mean"] is None:
                return "none"
            extra = f" ({v['n_null']} none)" if v["n_null"] else ""
            return f"{fmt.format(v['mean'])} / {fmt.format(v['median'])}{extra}"
        t.add_row(s, f("n_nodes"), f("n_failing"), f("failing_rate_per_100"), f("first_decision_fail_index"), f("wall_ms", "{:.0f}"))
    console.print(t)
    t2 = Table(title="ddmin on failing schedules", box=box.ASCII)
    for col in ("seed", "strategy", "original", "minimal", "oracle calls", "1-minimal"):
        t2.add_column(col)
    for c in cases:
        t2.add_row(str(c["seed"]), c["strategy"], str(c["original_size"]), str(c["minimal_size"]), str(c["oracle_calls"]),
                   str(c["one_minimal_verified"]))
    console.print(t2)
    console.print(f"wrote {args.out}", markup=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
