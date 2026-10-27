"""WORLDLINE command line.

    run | explore | minimize | genome extract | genome challenge | report      (agent side)
    keygen | verify | authorize                                               (kernel side)
    demo                                                                      (whole chain, locally)
    real usgs                                                                 (real-data scenario from USGS)

Every command prints the digests it produced. The kernel commands read only the
output directory, the policy and the keys, and recompute every result they attest.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from worldline.core import EvidenceKind, Verdict, content_hash
from worldline.evidence import (
    AUTHORIZED, DENIED, Bundle, EvidenceRecord, authorize as kernel_authorize, generate_keypair, key_id,
    load_policy, recompute_and_attest, revoke_if_assumption_invalid, utc_now_iso,
)
from worldline.explorer import causal_diff, explore as run_explore, minimize as run_minimize
from worldline.genome import FailureGenome, extract
from worldline.interventions import schedule_hash
from worldline.worlds import load_scenario, run

from . import common as C

app = typer.Typer(help="WORLDLINE: branch executions, isolate the causal difference, type the evidence, gate the release.",
                  no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)
genome_app = typer.Typer(help="Portable failure mechanisms.", no_args_is_help=True)
app.add_typer(genome_app, name="genome")
real_app = typer.Typer(help="Scenarios whose truth stream is real recorded public data (with provenance).",
                       no_args_is_help=True)
app.add_typer(real_app, name="real")
from .http_cmd import http_app  # noqa: E402  (lazy-imports the http adapter inside its commands)
app.add_typer(http_app, name="http")
from .version import register as _register_version  # noqa: E402
_register_version(app)
from . import validate as _validate  # noqa: E402
_validate.register(app)

console = Console(highlight=False)
DEFAULT_OUT = Path("experiments") / "out"
STRATEGIES = ("guided", "random")


def _echo(msg: str) -> None:
    console.print(msg, markup=False, soft_wrap=True)


def _fail(msg: str) -> None:
    console.print(f"error: {msg}", markup=False, style="bold red")
    raise typer.Exit(code=1)


# =============================================================================== run

def do_run(scenario_path: Path, out: Path, invariants_path: Path = Path(C.DEFAULT_INVARIANTS)) -> dict:
    sc, inv = C.load_world(scenario_path, invariants_path)
    tr = run(sc)
    vs = C.verdicts(tr, sc, inv)
    tp, vp = C.write_trace(out, "root", tr, vs)
    return {"scenario_id": sc.id, "digest": tr.digest, "trace_file": str(tp), "verdicts_file": str(vp),
            "decisions": [d.payload for d in tr.decisions], "verdicts": [v.to_dict() for v in vs]}


@app.command("run")
def run_cmd(scenario: Path = typer.Argument(..., help="Scenario YAML."),
            out: Path = typer.Option(DEFAULT_OUT, "--out", help="Output directory."),
            invariants: Path = typer.Option(Path(C.DEFAULT_INVARIANTS), "--invariants")) -> None:
    """Run the root (unperturbed) world and check its invariants."""
    r = do_run(scenario, out, invariants)
    _echo(f"scenario {r['scenario_id']}  root trace digest {r['digest']}")
    for d in r["decisions"]:
        _echo(f"  decision {d['actor']}/{d['rule_id']} at {d['at_ns'] / 1e9:.0f}s -> {d['option']} "
              f"(knew {len(d['known_event_ids'])} events)")
    for v in r["verdicts"]:
        _echo(f"  {v['result']:7} {v['invariant_id']}  {v['explanation'][:90]}")
    _echo(f"wrote {r['trace_file']} and {r['verdicts_file']}")



# =============================================================================== explore

def do_explore(scenario_path: Path, budget: int, seed: int, out: Path, strategy: str = "both",
               schedule_size: int = 3, invariants_path: Path = Path(C.DEFAULT_INVARIANTS)) -> dict:
    strategies = list(STRATEGIES) if strategy == "both" else [strategy]
    if any(s not in STRATEGIES for s in strategies):
        raise ValueError(f"strategy must be one of guided|random|both, got {strategy!r}")
    sc, inv = C.load_world(scenario_path, invariants_path)
    runs, comparison, trace_files = {}, {}, {}
    root = run(sc)
    C.write_trace(out, "root", root, C.verdicts(root, sc, inv))
    trace_files["root"] = {"trace": "traces/root.json", "verdicts": "verdicts/root.json"}
    for s in strategies:
        res = run_explore(sc, inv, budget=budget, seed=seed, strategy=s, schedule_size=schedule_size)
        d = res.to_dict()
        runs[s] = d
        comparison[s] = C.comparison_row(d)
        for node in res.nodes:
            if not node.failing_invariants or node.branch_id in trace_files:
                continue
            tr = run(sc, C.schedule_from_dicts(node.schedule))
            name = C.safe_branch_name(node.branch_id)
            C.write_trace(out, name, tr, C.verdicts(tr, sc, inv))
            trace_files[node.branch_id] = {"trace": f"traces/{name}.json", "verdicts": f"verdicts/{name}.json",
                                           "strategy": s, "failing_invariants": node.failing_invariants}
    doc = {"scenario_id": sc.id, "scenario_path": str(scenario_path), "invariants_path": str(invariants_path),
           "seed": seed, "budget": budget, "schedule_size": schedule_size, "root_digest": root.digest,
           "strategies": strategies, "runs": runs, "comparison": comparison, "trace_files": trace_files}
    C.write_json(out / "exploration.json", doc)
    return doc


def _comparison_table(comparison: dict, title: str = "guided vs random") -> Table:
    t = Table(title=title, box=box.ASCII)
    for col in ("strategy", "nodes", "failing", "fail/100", "first decision FAIL idx", "wall ms"):
        t.add_column(col)
    for s, row in comparison.items():
        t.add_row(s, str(row["n_nodes"]), str(row["n_failing"]), f"{row['failing_rate_per_100']:.1f}",
                  str(row["first_decision_fail_index"]), f"{row['wall_ms']:.0f}")
    return t


@app.command()
def explore(scenario: Path = typer.Argument(...),
            budget: int = typer.Option(..., "--budget"),
            seed: int = typer.Option(..., "--seed"),
            out: Path = typer.Option(DEFAULT_OUT, "--out"),
            strategy: str = typer.Option("both", "--strategy", help="guided | random | both"),
            schedule_size: int = typer.Option(3, "--schedule-size"),
            invariants: Path = typer.Option(Path(C.DEFAULT_INVARIANTS), "--invariants")) -> None:
    """Branch the world under fault schedules and record which branches violate invariants."""
    try:
        doc = do_explore(scenario, budget, seed, out, strategy, schedule_size, invariants)
    except ValueError as exc:
        _fail(str(exc))
    _echo(f"scenario {doc['scenario_id']}  root trace digest {doc['root_digest']}")
    console.print(_comparison_table(doc["comparison"]))
    _echo(f"wrote {out / 'exploration.json'} and {len(doc['trace_files'])} trace/verdict pairs")


# =============================================================================== minimize

MAX_CANDIDATES = 25


def _candidates(expl: dict, target: str) -> tuple[list[dict], str | None, str]:
    """Failing branches to consider: from the guided run when it has any, longest schedules first."""
    order = ["guided"] + [s for s in expl["runs"] if s != "guided"]
    for want_target in (True, False):
        for s in order:
            if s not in expl["runs"]:
                continue
            cands = [n for n in expl["runs"][s]["nodes"]
                     if n["failing_invariants"] and (not want_target or target in n["failing_invariants"])]
            if cands:
                cands.sort(key=lambda n: -len(n["schedule"]))  # stable: exploration order within a size
                return cands, s, (target if want_target else cands[0]["failing_invariants"][0])
    return [], None, target


def _self_explaining(res) -> bool:
    """The minimal intervention touches an event the decision-maker lacked at decision time: the
    counterexample names its own cause (a blackout or a duplicate does not; a delayed correction does)."""
    witness = set(res.witness)
    return any((iv["target"].get("event_id") or "").split("#")[0] in witness for iv in res.minimal)


def do_minimize(exploration_json: Path, out: Path, invariant: str = C.TARGET_INVARIANT) -> dict:
    expl = C.read_json(exploration_json)
    cands, strategy, target = _candidates(expl, invariant)
    if not cands:
        raise ValueError("no failing branch in exploration.json; nothing to minimize")
    sc, inv = C.load_world(C.resolve_repo_path(expl["scenario_path"]), C.resolve_repo_path(expl["invariants_path"]))
    considered, skipped = [], []
    for node in cands[:MAX_CANDIDATES]:
        try:
            res = run_minimize(sc, inv, C.schedule_from_dicts(node["schedule"]), target)
        except ValueError as exc:
            # a subset of the schedule can violate the time rule (e.g. a clock skew without its companion);
            # explore() records such schedules as rejected, the ddmin oracle raises. Skip, do not hide.
            skipped.append({"branch_id": node["branch_id"], "reason": str(exc)})
            continue
        considered.append((node, res))
    if not considered:
        raise ValueError(f"every candidate was rejected during minimization: {skipped}")
    # longest original first (so minimization is visible), then self-explaining, then fewest other FAILs
    node, res = max(considered, key=lambda nr: (len(nr[0]["schedule"]), _self_explaining(nr[1]),
                                               -len(nr[0]["failing_invariants"])))
    minimal = C.schedule_from_dicts(res.minimal)
    root, min_tr = run(sc), run(sc, minimal)
    C.write_trace(out, "minimal", min_tr, C.verdicts(min_tr, sc, inv))
    doc = res.to_dict()
    doc.update({"scenario_path": expl["scenario_path"], "invariants_path": expl["invariants_path"],
                "branch_id": node["branch_id"], "strategy": strategy, "requested_invariant": invariant,
                "selection_rule": "guided run first; branches failing the target; longest original schedule; "
                                  "minimal intervention targets an event in the FAIL witness (self-explaining); "
                                  "fewest other failing invariants; exploration order",
                "self_explaining": _self_explaining(res),
                "candidates_considered": [{"branch_id": n["branch_id"], "original_size": len(n["schedule"]),
                                           "minimal_size": len(r.minimal), "oracle_calls": r.oracle_calls,
                                           "self_explaining": _self_explaining(r)} for n, r in considered],
                "candidates_skipped": skipped,
                "minimal_schedule_hash": schedule_hash(minimal), "minimal_branch_id": min_tr.branch_id,
                "causal_diff": causal_diff(root, min_tr),
                "trace_files": {"minimal": "traces/minimal.json", "minimal_verdicts": "verdicts/minimal.json"}})
    C.write_json(out / "minimal_schedule.json", doc)
    return doc


@app.command()
def minimize(exploration_json: Path = typer.Argument(...),
             out: Path = typer.Option(DEFAULT_OUT, "--out"),
             invariant: str = typer.Option(C.TARGET_INVARIANT, "--invariant")) -> None:
    """ddmin a failing fault schedule down to a 1-minimal counterexample."""
    try:
        doc = do_minimize(exploration_json, out, invariant)
    except ValueError as exc:
        _fail(str(exc))
    if doc["target_invariant"] != invariant:
        _echo(f"note: no branch fails {invariant}; minimized {doc['target_invariant']} instead")
    _echo(f"branch {doc['branch_id']} ({doc['strategy']}; {len(doc['candidates_considered'])} candidate(s) minimized, "
          f"self-explaining: {doc['self_explaining']})")
    _echo(f"schedule size {len(doc['original'])} -> {len(doc['minimal'])}  oracle calls {doc['oracle_calls']} "
          f"(cache hits {doc['cache_hits']})  one-minimal verified: {doc['one_minimal_verified']}")
    for iv in doc["minimal"]:
        _echo(f"  {iv['op']} {iv['target']} {iv['params']}")
    _echo(f"witness {doc['witness']}")
    _echo(f"explanation: {doc['explanation']}")
    diff = doc["causal_diff"]
    _echo(f"causal diff vs root: {len(diff['decisions'])} decision(s) changed, truth identical: {diff['truth_identical']}")
    _echo(f"minimal trace digest {doc['trace_digest']}  schedule hash {doc['minimal_schedule_hash']}")
    _echo(f"wrote {out / 'minimal_schedule.json'}")


# =============================================================================== genome

def mechanism_sentence(g: dict) -> str:
    parts = []
    for step in g["mechanism"]:
        sel = step["selector"]
        txt = (f"{step['op']} the {sel['position'].replace('_', ' ')} '{sel['kind']}' event "
               f"({sel['relation'].replace('_', ' ')}) seen by the {sel['role'].replace('_', ' ')}")
        if step["op"] == "delay" and step["params"].get("until") == "after_decision":
            txt += " until after the decision"
        parts.append(txt)
    return "; then ".join(parts) + f", so that {g['violated_invariant']} fails."


def do_genome_extract(minimal_json: Path, out_file: Path) -> dict:
    m = C.read_json(minimal_json)
    sc, inv = C.load_world(C.resolve_repo_path(m["scenario_path"]), C.resolve_repo_path(m["invariants_path"]))
    minimal = C.schedule_from_dicts(m["minimal"])
    tr = run(sc, minimal)
    target = m["target_invariant"]
    vs = C.verdicts(tr, sc, {target: inv[target]})
    fails = [v for v in vs if v.result == Verdict.FAIL]
    if not fails:
        raise ValueError(f"minimal schedule does not make {target} FAIL on re-run; refusing to extract")
    g = extract(tr, list(minimal), fails[0], sc,
                origin_extra={"code_commit": C.git_commit(), "minimal_schedule_hash": schedule_hash(minimal),
                              "scenario_path": m["scenario_path"], "invariants_path": m["invariants_path"]})
    doc = g.to_dict()
    doc["mechanism_sentence"] = mechanism_sentence(doc)
    C.write_json(out_file, doc)
    return doc


@genome_app.command("extract")
def genome_extract(minimal_json: Path = typer.Argument(...),
                   out: Path = typer.Option(..., "--out", help="Output genome JSON file.")) -> None:
    """Abstract a minimal counterexample into a portable failure mechanism."""
    try:
        doc = do_genome_extract(minimal_json, out)
    except ValueError as exc:
        _fail(str(exc))
    _echo(f"genome {doc['id']}  ({doc['evidence_kind']}; {doc['claim_ceiling']})")
    _echo(f"mechanism: {doc['mechanism_sentence']}")
    _echo(f"preconditions: {', '.join(doc['preconditions'])}")
    _echo(f"wrote {out}")


def do_genome_challenge(genome_json: Path, scenario_path: Path, seed: int, out_file: Path,
                        immune: list[Path] | None, invariants_path: Path = Path(C.DEFAULT_INVARIANTS)) -> dict:
    gd = C.read_json(genome_json)
    g = FailureGenome.from_dict(gd)
    out_dir = out_file.parent
    if immune is None:
        immune = [C.DEFAULT_IMMUNE] if C.resolve_repo_path(C.DEFAULT_IMMUNE).exists() else []

    def one(path: Path) -> dict:
        res, sc = C.run_challenge(g, path, invariants_path)
        d = res.to_dict()
        d["scenario_path"] = str(path)
        if res.reproduced:
            tr = run(sc, C.schedule_from_dicts(d["instantiated_schedule"]))
            inv = C.invariants_for(sc, invariants_path)
            name = f"challenge_{sc.id}"
            C.write_trace(out_dir, name, tr, C.verdicts(tr, sc, inv))
            d["trace_file"] = f"traces/{name}.json"
        return d

    primary = one(scenario_path)
    controls = [one(p) for p in immune]
    doc = {"genome_id": g.id, "genome_path": str(genome_json), "requested_seed": seed,
           "seed_note": "challenge is deterministic in the scenario's own seed; --seed is recorded, not applied",
           "invariants_path": str(invariants_path), "primary": primary, "controls": controls,
           "success": C.challenge_success(primary, controls), "claim_ceiling": g.claim_ceiling}
    C.write_json(out_file, doc)
    return doc


@genome_app.command("challenge")
def genome_challenge(genome_json: Path = typer.Argument(...),
                     scenario: Path = typer.Argument(..., help="Scenario to challenge."),
                     seed: int = typer.Option(42, "--seed"),
                     out: Path = typer.Option(..., "--out", help="Output challenge JSON file."),
                     immune: Optional[list[Path]] = typer.Option(None, "--immune",
                                                                 help="Control scenario(s) expected NOT to reproduce."),
                     invariants: Path = typer.Option(Path(C.DEFAULT_INVARIANTS), "--invariants")) -> None:
    """Instantiate a genome against another scenario (and immune controls) and see if it reproduces."""
    doc = do_genome_challenge(genome_json, scenario, seed, out, immune or None, invariants)
    p = doc["primary"]
    _echo(f"genome {doc['genome_id']} on {p['scenario_id']}: reproduced {'yes' if p['reproduced'] else 'no'}")
    _echo(f"  {p['explanation']}")
    if p.get("counterexample_digest"):
        _echo(f"  counterexample trace digest {p['counterexample_digest']} -> {p.get('trace_file')}")
    for c in doc["controls"]:
        _echo(f"control {c['scenario_id']}: reproduced {'yes' if c['reproduced'] else 'no'}  ({c['explanation'][:100]})")
    _echo(f"challenge success (reproduced on primary, not on controls): {doc['success']}")
    _echo(f"wrote {out}")


# =============================================================================== report (agent side)

def _agent_record(kind: EvidenceKind, claim: str, subject: str, procedure: str, inputs: object, result: dict,
                  ts: str) -> EvidenceRecord:
    return EvidenceRecord(kind=kind, claim=claim, subject=subject, procedure=procedure,
                          inputs_hash=C.inputs_hash(inputs), result=result, assumptions=list(C.ASSUMPTIONS),
                          derived_at=ts, producer=C.AGENT_PRODUCER)


def _verdict_kind(result: str) -> EvidenceKind:
    return {"PASS": EvidenceKind.EMPIRICALLY_VALIDATED, "FAIL": EvidenceKind.FAILED}.get(result, EvidenceKind.UNKNOWN)


def do_report(out: Path, code_commit: str) -> dict:
    ts = utc_now_iso()
    files = {}
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name not in ("index.json", "agent_evidence.json"):
            files[p.relative_to(out).as_posix()] = {"bytes": p.stat().st_size, "sha256": C.file_digest(p)}
    records: list[EvidenceRecord] = []
    digests: dict = {}

    junit = C.find_junit(out)
    if junit:
        tot = C.parse_junit(junit)
        kind = EvidenceKind.EMPIRICALLY_VALIDATED if tot["failures"] == 0 and tot["errors"] == 0 else EvidenceKind.FAILED
        records.append(_agent_record(kind, "tests:unit", code_commit, "pytest --junitxml", C.file_digest(junit), tot, ts))
    else:
        records.append(_agent_record(EvidenceKind.UNKNOWN, "tests:unit", code_commit, "pytest --junitxml", None,
                                     {"reason": "report.xml not found in output dir or cwd"}, ts))

    root_v = out / "verdicts" / "root.json"
    if root_v.exists():
        root_tr = C.read_json(out / "traces" / "root.json")
        digests["root_trace_digest"] = root_tr["digest"]
        for v in C.read_json(root_v):
            records.append(_agent_record(_verdict_kind(v["result"]), f"{C.ROOT_CLAIM_PREFIX}{v['invariant_id']}", code_commit,
                                         f"invariants.check@{v['invariant_version']}", root_tr["digest"], v, ts))
    min_v = out / "verdicts" / "minimal.json"
    if min_v.exists():
        min_tr = C.read_json(out / "traces" / "minimal.json")
        digests["minimal_trace_digest"] = min_tr["digest"]
        for v in C.read_json(min_v):
            records.append(_agent_record(_verdict_kind(v["result"]), f"{C.MINIMAL_CLAIM_PREFIX}{v['invariant_id']}",
                                         code_commit, f"invariants.check@{v['invariant_version']}", min_tr["digest"], v, ts))

    expl_p = out / "exploration.json"
    if expl_p.exists():
        expl = C.read_json(expl_p)
        records.append(_agent_record(EvidenceKind.EMPIRICALLY_VALIDATED, "explore:guided_vs_random", code_commit,
                                     "explorer.explore", {"seed": expl["seed"], "budget": expl["budget"],
                                                          "schedule_size": expl["schedule_size"]},
                                     C.comparison_claim_result(expl["comparison"]), ts))
    min_p = out / "minimal_schedule.json"
    if min_p.exists():
        m = C.read_json(min_p)
        sc, inv = C.load_world(C.resolve_repo_path(m["scenario_path"]), C.resolve_repo_path(m["invariants_path"]))
        res = C.minimal_claim_result(sc, inv, C.schedule_from_dicts(m["original"]), C.schedule_from_dicts(m["minimal"]),
                                     m["target_invariant"])
        kind = EvidenceKind.EMPIRICALLY_VALIDATED if res["one_minimal_verified"] else EvidenceKind.FAILED
        records.append(_agent_record(kind, "minimize:one_minimal", code_commit, "explorer.ddmin+one_minimal_check",
                                     m["minimal_schedule_hash"], res, ts))
        digests["minimal_schedule_hash"] = m["minimal_schedule_hash"]
    ch_p = out / "challenge.json"
    if ch_p.exists():
        ch = C.read_json(ch_p)
        res = C.challenge_claim_result(ch["primary"], ch["controls"])
        if C.challenge_success(ch["primary"], ch["controls"]):
            kind = EvidenceKind.EMPIRICALLY_VALIDATED
        elif not ch["primary"]["preconditions_met"] or not ch["primary"]["instantiated_schedule"]:
            kind = EvidenceKind.UNKNOWN  # mechanism not applicable here: the missing signal is the precondition set
        else:
            kind = EvidenceKind.FAILED   # instantiated but did not reproduce, or an immune control reproduced
        records.append(_agent_record(kind, "genome:challenge", code_commit, "genome.challenge", ch["genome_id"], res, ts))
        digests["genome_id"] = ch["genome_id"]

    # "artifact:digest" itself is kernel-only under the policy (producer: kernel); the agent reports the manifest.
    records.append(_agent_record(EvidenceKind.EMPIRICALLY_VALIDATED, "artifact:manifest", code_commit, "cli.report",
                                 sorted(files), {"root_trace_digest": digests.get("root_trace_digest"),
                                                 "minimal_trace_digest": digests.get("minimal_trace_digest")}, ts))

    index = {"code_commit": code_commit, "generated_at": ts, "environment": C.environment(), "files": files,
             "digests": digests, "claims": [{"claim": r.claim, "kind": r.kind.value, "id": r.id} for r in records],
             "claim_ceiling": "EMPIRICALLY_VALIDATED on synthetic scenarios; ATTESTED only after kernel recomputation"}
    C.write_json(out / "index.json", index)
    C.write_json(out / "agent_evidence.json", [r.to_dict() for r in records])
    return index


@app.command()
def report(out_dir: Path = typer.Argument(...),
           code_commit: str = typer.Option(..., "--code-commit")) -> None:
    """Gather the output directory into index.json and typed agent evidence (never ATTESTED)."""
    idx = do_report(out_dir, code_commit)
    _echo(f"code commit {idx['code_commit']}  {len(idx['files'])} files indexed")
    for k, v in idx["digests"].items():
        _echo(f"  {k}: {v}")
    for c in idx["claims"]:
        _echo(f"  {c['kind']:22} {c['claim']}")
    _echo(f"wrote {out_dir / 'index.json'} and {out_dir / 'agent_evidence.json'}")


# =============================================================================== keygen / verify / authorize (kernel)

def _read_key(value: str) -> str:
    """A PEM string, or a path to one (CI passes the PEM text in a variable)."""
    if "-----BEGIN" in value:
        return value.replace("\\n", "\n")
    p = Path(value)
    if not p.exists():
        raise ValueError(f"key {value!r} is neither a PEM string nor an existing file")
    return p.read_text(encoding="utf-8")


def do_keygen(out_dir: Path, force: bool = False) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    priv_p, pub_p = out_dir / "kernel_private.pem", out_dir / "kernel_public.pem"
    if priv_p.exists() and not force:
        raise FileExistsError(f"{priv_p} exists; pass --force to overwrite")
    priv, pub = generate_keypair()
    priv_p.write_text(priv, encoding="utf-8")
    pub_p.write_text(pub, encoding="utf-8")
    return priv_p, pub_p


@app.command()
def keygen(out_dir: Path = typer.Option(Path("policies"), "--out-dir"),
           force: bool = typer.Option(False, "--force")) -> None:
    """Generate the kernel Ed25519 keypair. Commit only kernel_public.pem."""
    try:
        priv_p, pub_p = do_keygen(out_dir, force)
    except FileExistsError as exc:
        _fail(str(exc))
    _echo(f"wrote {priv_p} (keep private; gitignored) and {pub_p}")
    _echo(f"key id {key_id(pub_p.read_text(encoding='utf-8'))}")


def do_verify(out: Path, signing_key: str, public_key: Path, bundle_out: Path) -> dict:
    agent_records = C.read_json(out / "agent_evidence.json")
    index = C.read_json(out / "index.json") if (out / "index.json").exists() else {}
    code_commit = index.get("code_commit", "")
    bundle = Bundle()
    for rd in agent_records:
        bundle.add(EvidenceRecord.from_dict(rd))  # raises if an agent tries to self-certify ATTESTED

    recomputed: dict = {}
    notes: list[str] = []

    # root trace + invariants, from the scenario the exploration names (never from the agent's verdict files)
    expl = C.read_json(out / "exploration.json") if (out / "exploration.json").exists() else None
    root_digest = minimal_digest = None
    sc = inv = None
    if expl:
        sc, inv = C.load_world(C.resolve_repo_path(expl["scenario_path"]), C.resolve_repo_path(expl["invariants_path"]))
    elif (out / "traces" / "root.json").exists():
        tr0 = C.load_trace(out / "traces" / "root.json")
        sc, inv = tr0.scenario, C.invariants_for(tr0.scenario)
    if sc is not None:
        root = run(sc)
        root_digest = root.digest
        recomputed.update(C.verdict_results(C.verdicts(root, sc, inv), C.ROOT_CLAIM_PREFIX))
        notes.append(f"root trace recomputed: {root.digest}")

    if expl:
        comp = {}
        for s in expl["strategies"]:
            res = run_explore(sc, inv, budget=expl["budget"], seed=expl["seed"], strategy=s,
                              schedule_size=expl["schedule_size"])
            comp[s] = C.comparison_row(res.to_dict())
        recomputed["explore:guided_vs_random"] = C.comparison_claim_result(comp)
        notes.append(f"exploration recomputed for {expl['strategies']} (budget {expl['budget']}, seed {expl['seed']})")

    min_p = out / "minimal_schedule.json"
    if min_p.exists() and sc is not None:
        m = C.read_json(min_p)
        original, minimal = C.schedule_from_dicts(m["original"]), C.schedule_from_dicts(m["minimal"])
        min_tr = run(sc, minimal)
        minimal_digest = min_tr.digest
        recomputed.update(C.verdict_results(C.verdicts(min_tr, sc, inv), C.MINIMAL_CLAIM_PREFIX))
        recomputed["minimize:one_minimal"] = C.minimal_claim_result(sc, inv, original, minimal, m["target_invariant"])
        notes.append(f"minimal schedule re-run: {min_tr.digest}; one-minimal "
                     f"{recomputed['minimize:one_minimal']['one_minimal_verified']}")

    ch_p, g_p = out / "challenge.json", out / "genome.json"
    if ch_p.exists() and g_p.exists():
        ch = C.read_json(ch_p)
        g = FailureGenome.from_dict(C.read_json(g_p))
        inv_path = ch.get("invariants_path", C.DEFAULT_INVARIANTS)
        primary = C.run_challenge(g, ch["primary"]["scenario_path"], inv_path)[0].to_dict()
        controls = [C.run_challenge(g, c["scenario_path"], inv_path)[0].to_dict() for c in ch["controls"]]
        recomputed["genome:challenge"] = C.challenge_claim_result(primary, controls)
        notes.append(f"genome challenge re-run: reproduced={primary['reproduced']}, "
                     f"controls={[c['reproduced'] for c in controls]}")

    junit = C.find_junit(out)
    if junit:
        tot = C.parse_junit(junit)
        if tot["failures"] == 0 and tot["errors"] == 0:
            recomputed["tests:unit"] = tot
            notes.append(f"junit {junit}: {tot['tests']} tests, 0 failures")
        else:
            notes.append(f"junit {junit}: {tot['failures']} failures / {tot['errors']} errors; not attested")
    else:
        notes.append("no report.xml: tests:unit not attested")

    recomputed["artifact:digest"] = {"root_trace_digest": root_digest, "minimal_trace_digest": minimal_digest}
    recomputed["artifact:manifest"] = dict(recomputed["artifact:digest"])

    appended = recompute_and_attest(bundle, recomputed, signing_key, subject=code_commit)
    vr = bundle.verify(public_key.read_text(encoding="utf-8"))
    C.write_json(bundle_out, bundle.to_dict())
    mismatches = [r for r in appended if r.kind == EvidenceKind.FAILED]
    return {"ok": vr.ok, "codes": vr.codes, "verification": vr.to_dict(), "notes": notes,
            "attested": [r.claim for r in appended if r.kind == EvidenceKind.ATTESTED],
            "mismatches": [r.result for r in mismatches], "root": bundle.root, "n_records": len(bundle.records),
            "bundle_file": str(bundle_out)}


@app.command()
def verify(out_dir: Path = typer.Argument(...),
           signing_key: str = typer.Option(..., "--signing-key", help="Ed25519 private key PEM text or file path."),
           public_key: Path = typer.Option(..., "--public-key"),
           out: Path = typer.Option(..., "--out", help="Bundle JSON to write.")) -> None:
    """THE KERNEL: recompute every agent claim, attest matches, flag mismatches, seal and verify the bundle."""
    try:
        key = _read_key(signing_key)
    except ValueError as exc:
        _fail(str(exc))
    r = do_verify(out_dir, key, public_key, out)
    for n in r["notes"]:
        _echo(f"  {n}")
    _echo(f"attested {len(r['attested'])} claim(s); mismatches {len(r['mismatches'])}")
    for m in r["mismatches"]:
        _echo(f"  MISMATCH {m['claim']}: agent={json.dumps(m['agent_reported'])[:120]} kernel={json.dumps(m['kernel_recomputed'])[:120]}")
    _echo(f"bundle root {r['root']}  records {r['n_records']}")
    _echo(f"verification ok={r['ok']} codes={r['codes'] or ['none']}  signer {r['verification']['signer_key_id']}")
    _echo(f"wrote {r['bundle_file']}")
    if not r["ok"]:
        raise typer.Exit(code=1)


def do_authorize(bundle_json: Path, policy_path: Path, public_key: Path, artifact_digest: str, out_file: Path,
                 now: str | None = None, invalidate_assumption: str | None = None) -> dict:
    bundle = Bundle.from_json(bundle_json.read_text(encoding="utf-8"))
    policy = load_policy(policy_path)
    now_dt = now or datetime.now(timezone.utc).isoformat()
    auth = kernel_authorize(bundle, policy, artifact_digest, public_key.read_text(encoding="utf-8"), now_dt)
    if invalidate_assumption:
        auth = revoke_if_assumption_invalid(bundle, invalidate_assumption, auth)
    doc = auth.to_dict()
    doc["bundle_file"] = str(bundle_json)
    C.write_json(out_file, doc)
    return doc


@app.command()
def authorize(bundle_json: Path = typer.Argument(...),
              policy: Path = typer.Option(Path("policies") / "release.yaml", "--policy"),
              public_key: Path = typer.Option(Path("policies") / "kernel_public.pem", "--public-key"),
              artifact_digest: str = typer.Option(..., "--artifact-digest"),
              out: Path = typer.Option(..., "--out"),
              now: Optional[str] = typer.Option(None, "--now", help="ISO time to evaluate at (default: now)."),
              invalidate_assumption: Optional[str] = typer.Option(None, "--invalidate-assumption",
                                                                  help="Revoke if any satisfying record rests on this assumption.")) -> None:
    """Release gate: AUTHORIZED or DENIED from the sealed bundle, the policy and the public key. Exit 1 on DENIED."""
    doc = do_authorize(bundle_json, C.resolve_repo_path(policy), C.resolve_repo_path(public_key), artifact_digest, out,
                       now, invalidate_assumption)
    style = "bold green" if doc["decision"] == AUTHORIZED else "bold red"
    console.print(f"{doc['decision']}  artifact {doc['artifact_digest']}  policy {doc['policy']}", style=style, markup=False)
    for r in doc["reasons"]:
        _echo(f"  {r}")
    _echo(f"satisfied {len(doc['satisfied'])}  missing {doc['missing']}  stale {len(doc['stale'])}  downgraded {len(doc['downgraded'])}")
    _echo(f"wrote {out}")
    if doc["decision"] != AUTHORIZED:
        raise typer.Exit(code=1)


# =============================================================================== demo

def _tamper(bundle_json: Path, tampered_out: Path) -> str:
    d = C.read_json(bundle_json)
    target = next((r for r in d["records"] if r["claim"].startswith(C.ROOT_CLAIM_PREFIX)), d["records"][0])
    target["result"] = {**target["result"], "result": "PASS", "tampered_by": "demo"}
    C.write_json(tampered_out, d)
    return target["claim"]


@app.command()
def demo(out: Path = typer.Option(DEFAULT_OUT, "--out"),
         budget: int = typer.Option(150, "--budget"),
         seed: int = typer.Option(42, "--seed"),
         scenario: Path = typer.Option(Path("scenarios") / "port_alert.yaml", "--scenario"),
         challenge_scenario: Path = typer.Option(Path("scenarios") / "convoy_lostlink.yaml", "--challenge-scenario"),
         skip_tests: bool = typer.Option(False, "--skip-tests", help="Do not run pytest for the tests:unit claim.")) -> None:
    """Run the whole chain locally: explore, minimize, genome, challenge, report, verify, authorize (+ a DENIED demo)."""
    out.mkdir(parents=True, exist_ok=True)
    scenario, challenge_scenario = C.resolve_repo_path(scenario), C.resolve_repo_path(challenge_scenario)
    commit = C.git_commit() or "local"
    keys = C.resolve_repo_path(Path("policies"))
    priv_p, pub_p = keys / "kernel_private.pem", keys / "kernel_public.pem"
    if not priv_p.exists():
        do_keygen(keys)
        _echo(f"[keygen] wrote {priv_p} and {pub_p}")

    if not skip_tests and C.find_junit(out) is None:
        _echo("[tests] running pytest for the tests:unit claim (excluding tests/test_cli.py) ...")
        subprocess.run([sys.executable, "-m", "pytest", "-q", "--ignore=tests/test_cli.py",
                        f"--junitxml={out / 'report.xml'}"], cwd=str(C.repo_root()), check=False)

    _echo(f"[explore] {scenario} budget {budget} seed {seed}")
    expl = do_explore(scenario, budget, seed, out)
    console.print(_comparison_table(expl["comparison"]))
    m = do_minimize(out / "exploration.json", out)
    _echo(f"[minimize] {m['branch_id']}: {len(m['original'])} -> {len(m['minimal'])} interventions, "
          f"{m['oracle_calls']} oracle calls, one-minimal {m['one_minimal_verified']}, witness {m['witness']}")
    g = do_genome_extract(out / "minimal_schedule.json", out / "genome.json")
    _echo(f"[genome] {g['id']}: {g['mechanism_sentence']}")
    ch = do_genome_challenge(out / "genome.json", challenge_scenario, seed, out / "challenge.json", None)
    _echo(f"[challenge] {ch['primary']['scenario_id']} reproduced={ch['primary']['reproduced']}; "
          f"controls: {[(c['scenario_id'], c['reproduced']) for c in ch['controls']]}")
    idx = do_report(out, commit)
    _echo(f"[report] {len(idx['claims'])} agent claims, {len(idx['files'])} files")
    v = do_verify(out, priv_p.read_text(encoding="utf-8"), pub_p, out / "bundle.json")
    _echo(f"[verify] ok={v['ok']} attested={len(v['attested'])} mismatches={len(v['mismatches'])} root {v['root']}")
    a = do_authorize(out / "bundle.json", keys / "release.yaml", pub_p, commit, out / "authorization.json")
    console.print(f"[authorize] {a['decision']}  ({'; '.join(a['reasons'][:3])})",
                  style="bold green" if a["decision"] == AUTHORIZED else "bold red", markup=False)
    claim = _tamper(out / "bundle.json", out / "bundle_tampered.json")
    d = do_authorize(out / "bundle_tampered.json", keys / "release.yaml", pub_p, commit, out / "authorization_denied.json")
    console.print(f"[authorize tampered {claim}] {d['decision']}  ({'; '.join(d['reasons'][:2])})",
                  style="bold red" if d["decision"] == DENIED else "bold yellow", markup=False)
    _echo(f"root trace {expl['root_digest']}  minimal trace {m['trace_digest']}  bundle root {v['root']}")
    _echo(f"wrote {out / 'authorization.json'} and {out / 'authorization_denied.json'}")
    if a["decision"] != AUTHORIZED or d["decision"] != DENIED:
        raise typer.Exit(code=1)


# =============================================================================== real data

def do_real_usgs(start: str, end: str, minmag: float, out: Path | None, snapshot_dir: Path, offline: bool,
                 threshold: float, decision_at: int | None) -> dict:
    from worldline.adapters import usgs

    key = usgs.window_key(start, end, minmag)
    snap_path = snapshot_dir / f"{key}.json"
    if offline:
        if not snap_path.exists():
            raise ValueError(f"--offline: no snapshot at {snap_path}")
    else:
        raw = usgs.fetch_window(start, end, minmag)
        usgs.snapshot(raw, snap_path)
    if not usgs.verify_snapshot(snap_path):
        raise ValueError(f"snapshot {snap_path} fails its SHA-256 check")
    out_yaml = out or Path("scenarios") / "real" / f"usgs_{key}.yaml"
    sc = usgs.to_scenario(snap_path, out_yaml, threshold=threshold, decision_at_s=decision_at,
                          snapshot_ref=snap_path.as_posix())
    prov = usgs.load_snapshot(snap_path)["provenance"]
    return {"key": key, "snapshot": str(snap_path), "snapshot_sha256": prov["sha256"],
            "fetched_at_utc": prov["fetched_at_utc"], "scenario": str(out_yaml), "scenario_id": sc.id,
            "reports": sum(1 for i in sc.injects if i.kind == "quake_report"),
            "corrections": sum(1 for i in sc.injects if i.kind == "correction"),
            "decision_at_s": sc.actors[0].decision_rules[0].at_ns / 1e9}


@real_app.command("usgs")
def real_usgs(start: str = typer.Option(..., "--start", help="Window start, UTC ISO (e.g. 2025-07-29T23:20:00Z)."),
              end: str = typer.Option(..., "--end", help="Window end, UTC ISO."),
              minmag: float = typer.Option(..., "--minmag", help="Minimum magnitude for the FDSN query."),
              out: Optional[Path] = typer.Option(None, "--out", help="Scenario YAML (default scenarios/real/usgs_<window>.yaml)."),
              snapshot_dir: Path = typer.Option(Path("data") / "real" / "usgs", "--snapshot-dir"),
              offline: bool = typer.Option(False, "--offline", help="Regenerate from the existing snapshot, no network."),
              threshold: float = typer.Option(8.5, "--threshold", help="MODEL: magnitude threshold of the decision rule."),
              decision_at: Optional[int] = typer.Option(None, "--decision-at",
                                                        help="MODEL: decision time in s after window start "
                                                             "(default: just after the first rule-flipping correction).")) -> None:
    """Fetch a USGS window (or reuse its snapshot), record provenance, and generate a real-data scenario."""
    try:
        r = do_real_usgs(start, end, minmag, out, snapshot_dir, offline, threshold, decision_at)
    except ValueError as exc:
        _fail(str(exc))
    _echo(f"snapshot {r['snapshot']}  sha256 {r['snapshot_sha256']}  fetched {r['fetched_at_utc']}"
          f"{'  (offline)' if offline else ''}")
    _echo(f"scenario {r['scenario_id']}: {r['reports']} real reports, {r['corrections']} real corrections; "
          f"MODEL decision at {r['decision_at_s']:.0f}s")
    _echo(f"wrote {r['scenario']}")


if __name__ == "__main__":  # pragma: no cover
    app()
