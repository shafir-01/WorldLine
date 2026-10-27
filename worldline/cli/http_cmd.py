"""`worldline http ...`: the agent-side pipeline run against a real service through the HTTP adapter.

    worldline http demo --budget 60 --seed 42 --out experiments/out_http

Writes the simulator pipeline's file names (traces/root.json, verdicts/root.json, exploration.json,
minimal_schedule.json, challenge.json) so the same readers work. Requires the `service` extra
(fastapi, httpx); imports are lazy so the rest of the CLI does not depend on it.
"""
from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console

from worldline.core import Verdict

from . import common as C

http_app = typer.Typer(help="Run the pipeline against a real ASGI service (examples/service_demo).",
                       no_args_is_help=True)
console = Console(highlight=False)
DEFAULT_GENOME = Path("experiments") / "out" / "genome.json"
MAX_CANDIDATES = 25


def _echo(msg: str) -> None:
    console.print(msg, markup=False, soft_wrap=True)


def do_http_demo(out: Path, budget: int, seed: int, scenario_path: Path, genome_path: Path,
                 invariants_path: Path = Path(C.DEFAULT_INVARIANTS), schedule_size: int = 3) -> dict:
    try:
        from worldline.adapters.http import (HttpWorld, challenge_http, demo_service, explore_http,
                                             minimize_http, verdicts_http)
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(f"the http adapter needs the service extra (uv sync --extra service): {exc}") from exc
    from worldline.explorer import causal_diff
    from worldline.genome import FailureGenome
    from worldline.interventions import schedule_hash
    from worldline.worlds import load_scenario

    from .main import _candidates, _self_explaining  # reuse the simulator pipeline's selection rule

    t_all = time.perf_counter()
    sc = load_scenario(C.resolve_repo_path(scenario_path))
    inv = C.invariants_for(sc, invariants_path)
    service = demo_service()
    world = HttpWorld(service, sc)
    summary: dict = {"service": service.name, "scenario_id": sc.id, "out": str(out)}

    def write(name: str, tr) -> list:
        vs = verdicts_http(world, tr, inv)
        C.write_trace(out, name, tr, vs)
        return vs

    # ---- root
    root = world.run()
    root_vs = write("root", root)
    summary["root"] = {"digest": root.digest, "decisions": [d.payload["option"] for d in root.decisions],
                       "verdicts": {v.invariant_id: v.result.value for v in root_vs},
                       "requests": len(root.exchanges), "rejected": len(root.rejected())}

    # ---- explore
    runs, comparison, trace_files = {}, {}, {"root": {"trace": "traces/root.json", "verdicts": "verdicts/root.json"}}
    for strategy in ("guided", "random"):
        res = explore_http(world, None, inv, budget=budget, seed=seed, strategy=strategy, schedule_size=schedule_size)
        d = res.to_dict()
        runs[strategy] = d
        comparison[strategy] = C.comparison_row(d)
        for node in res.nodes:
            if not node.failing_invariants or node.branch_id in trace_files:
                continue
            name = C.safe_branch_name(node.branch_id)
            write(name, world.run(C.schedule_from_dicts(node.schedule)))
            trace_files[node.branch_id] = {"trace": f"traces/{name}.json", "verdicts": f"verdicts/{name}.json",
                                           "strategy": strategy, "failing_invariants": node.failing_invariants}
    expl = {"adapter": "http", "service": service.name, "scenario_id": sc.id, "scenario_path": str(scenario_path),
            "invariants_path": str(invariants_path), "seed": seed, "budget": budget, "schedule_size": schedule_size,
            "root_digest": root.digest, "strategies": list(runs), "runs": runs, "comparison": comparison,
            "trace_files": trace_files}
    C.write_json(out / "exploration.json", expl)
    summary["explore"] = comparison

    # ---- minimize (same candidate selection as `worldline minimize`)
    cands, strategy, target = _candidates(expl, C.TARGET_INVARIANT)
    if not cands:
        raise RuntimeError("no failing branch found against the service; nothing to minimize")
    considered, skipped = [], []
    for node in cands[:MAX_CANDIDATES]:
        try:
            considered.append((node, minimize_http(world, None, inv, C.schedule_from_dicts(node["schedule"]), target)))
        except ValueError as exc:
            skipped.append({"branch_id": node["branch_id"], "reason": str(exc)})
    node, res = max(considered, key=lambda nr: (len(nr[0]["schedule"]), _self_explaining(nr[1]),
                                               -len(nr[0]["failing_invariants"])))
    minimal = C.schedule_from_dicts(res.minimal)
    min_tr = world.run(minimal)
    write("minimal", min_tr)
    mdoc = res.to_dict()
    mdoc.update({"adapter": "http", "service": service.name, "scenario_path": str(scenario_path),
                 "invariants_path": str(invariants_path), "branch_id": node["branch_id"], "strategy": strategy,
                 "requested_invariant": C.TARGET_INVARIANT, "self_explaining": _self_explaining(res),
                 "candidates_considered": [{"branch_id": n["branch_id"], "original_size": len(n["schedule"]),
                                            "minimal_size": len(r.minimal), "oracle_calls": r.oracle_calls,
                                            "self_explaining": _self_explaining(r)} for n, r in considered],
                 "candidates_skipped": skipped, "minimal_schedule_hash": schedule_hash(minimal),
                 "minimal_branch_id": min_tr.branch_id, "causal_diff": causal_diff(root, min_tr),
                 "minimal_decisions": [d.payload["option"] for d in min_tr.decisions],
                 "trace_files": {"minimal": "traces/minimal.json", "minimal_verdicts": "verdicts/minimal.json"}})
    C.write_json(out / "minimal_schedule.json", mdoc)
    summary["minimize"] = {"branch_id": node["branch_id"], "original_size": len(res.original),
                           "minimal_size": len(res.minimal), "oracle_calls": res.oracle_calls,
                           "one_minimal_verified": res.one_minimal_verified, "minimal": res.minimal,
                           "witness": res.witness, "decisions": mdoc["minimal_decisions"]}

    # ---- genome challenge (simulator-extracted genome, real service)
    gpath = C.resolve_repo_path(genome_path)
    g = FailureGenome.from_dict(C.read_json(gpath))
    ch = challenge_http(g, None, world, inv)
    primary = ch.to_dict()
    primary["scenario_path"] = str(scenario_path)
    primary["service"] = service.name
    if ch.reproduced:
        write(f"challenge_{sc.id}", world.run(C.schedule_from_dicts(primary["instantiated_schedule"])))
        primary["trace_file"] = f"traces/challenge_{sc.id}.json"
    cdoc = {"adapter": "http", "genome_id": g.id, "genome_path": str(genome_path),
            "genome_origin": {"scenario_id": g.origin.get("scenario_id"), "trace_digest": g.origin.get("trace_digest")},
            "invariants_path": str(invariants_path), "primary": primary, "controls": [],
            "controls_note": "no immune variant of the service exists yet; reproduction is reported without a control",
            "success": bool(ch.reproduced), "claim_ceiling": g.claim_ceiling}
    C.write_json(out / "challenge.json", cdoc)
    summary["challenge"] = {"genome_id": g.id, "preconditions_met": ch.preconditions_met,
                            "instantiated_schedule": ch.instantiated_schedule, "reproduced": ch.reproduced,
                            "counterexample_digest": ch.counterexample_digest, "explanation": ch.explanation}
    summary["wall_s"] = round(time.perf_counter() - t_all, 2)
    return summary


@http_app.command("demo")
def http_demo(budget: int = typer.Option(60, "--budget"),
              seed: int = typer.Option(42, "--seed"),
              out: Path = typer.Option(Path("experiments") / "out_http", "--out"),
              scenario: Path = typer.Option(Path("scenarios") / "port_alert.yaml", "--scenario",
                                            help="Scenario whose injects are the truth stream."),
              genome: Path = typer.Option(DEFAULT_GENOME, "--genome", help="Genome extracted by the simulator pipeline."),
              invariants: Path = typer.Option(Path(C.DEFAULT_INVARIANTS), "--invariants"),
              schedule_size: int = typer.Option(3, "--schedule-size")) -> None:
    """Root, explore, minimize and genome challenge against examples/service_demo over HTTP (ASGI)."""
    s = do_http_demo(out, budget, seed, scenario, genome, invariants, schedule_size)
    r = s["root"]
    _echo(f"[service] {s['service']} driven with {s['scenario_id']} truth via httpx ASGI transport")
    _echo(f"[root] decision {r['decisions']}  digest {r['digest']}  ({r['requests']} requests, {r['rejected']} rejected)")
    for inv_id, res in sorted(r["verdicts"].items()):
        _echo(f"  {res:7} {inv_id}")
    for strat, row in s["explore"].items():
        _echo(f"[explore {strat}] nodes {row['n_nodes']}  failing {row['n_failing']}  "
              f"fail/100 {row['failing_rate_per_100']:.1f}  first decision FAIL idx {row['first_decision_fail_index']}  "
              f"wall {row['wall_ms']:.0f} ms")
    m = s["minimize"]
    _echo(f"[minimize] {m['branch_id']}: {m['original_size']} -> {m['minimal_size']} interventions, "
          f"{m['oracle_calls']} oracle calls (each a full HTTP run), one-minimal {m['one_minimal_verified']}")
    for iv in m["minimal"]:
        _echo(f"  {iv['op']} {iv['target']} {iv['params']}")
    _echo(f"  service decision under minimal schedule: {m['decisions']}  witness {m['witness']}")
    c = s["challenge"]
    _echo(f"[challenge] {c['genome_id']} preconditions_met={c['preconditions_met']} reproduced={c['reproduced']}")
    for iv in c["instantiated_schedule"]:
        _echo(f"  instantiated: {iv['op']} {iv['target']} {iv['params']}")
    _echo(f"  {c['explanation']}")
    _echo(f"wrote {out / 'exploration.json'}, {out / 'minimal_schedule.json'}, {out / 'challenge.json'} "
          f"and traces/ verdicts/  (total {s['wall_s']} s)")
