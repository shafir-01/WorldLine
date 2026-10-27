"""Shared helpers for the WORLDLINE CLI: IO, naming, and the recomputation routines
that both the agent side (`report`) and the kernel side (`verify`) call.

The kernel never trusts a `result` it did not recompute. Every claim result that the
agent writes into agent_evidence.json is produced by a function in this module, and
`verify` calls the very same functions on its own re-runs; recompute_and_attest then
compares the two canonical JSON values. Wall-clock quantities never enter a result.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

from worldline.core import Verdict, content_hash
from worldline.explorer import ExplorationResult, failure_oracle, one_minimal_check, rule_evaluator
from worldline.genome import ChallengeResult, FailureGenome, challenge
from worldline.interventions import Intervention
from worldline.invariants import Invariant, VerdictRecord, check_all, load_invariants
from worldline.worlds import Scenario, Trace, load_scenario, run

TARGET_INVARIANT = "decision.consistent_with_truth"
DEFAULT_INVARIANTS = "invariants"
DEFAULT_IMMUNE = Path("scenarios") / "convoy_lostlink_immune.yaml"
AGENT_PRODUCER = "agent:pipeline"
ASSUMPTIONS = ["bounded horizon", "declared delivery policies", "synthetic scenario"]
ROOT_CLAIM_PREFIX = "invariant:"
MINIMAL_CLAIM_PREFIX = "counterexample:invariant:"


# ------------------------------------------------------------------ files

def write_json(path: Path | str, obj: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return p


def read_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def safe_branch_name(branch_id: str) -> str:
    """branch ids look like 'root/sha256:abcd...'; make them filesystem safe."""
    return branch_id.replace("/", "__").replace(":", "-")


def write_trace(out: Path, name: str, trace: Trace, verdicts: Iterable[VerdictRecord]) -> tuple[Path, Path]:
    tp = write_json(out / "traces" / f"{name}.json", trace.to_dict())
    vp = write_json(out / "verdicts" / f"{name}.json", [v.to_dict() for v in verdicts])
    return tp, vp


def load_trace(path: Path | str) -> Trace:
    return Trace.from_dict(read_json(path))


# ------------------------------------------------------------------ scenario + invariants

def load_world(scenario_path: str | Path, invariants_path: str | Path = DEFAULT_INVARIANTS
               ) -> tuple[Scenario, dict[str, Invariant]]:
    scenario = load_scenario(scenario_path)
    return scenario, invariants_for(scenario, invariants_path)


def invariants_for(scenario: Scenario, invariants_path: str | Path = DEFAULT_INVARIANTS) -> dict[str, Invariant]:
    """All declared invariants, restricted to the scenario's list when it has one."""
    inv = load_invariants(resolve_repo_path(invariants_path))
    if scenario.invariants:
        chosen = {k: v for k, v in inv.items() if k in scenario.invariants}
        if chosen:
            return chosen
    return inv


def resolve_repo_path(path: str | Path) -> Path:
    """A path as given (relative to cwd), else relative to the repository root."""
    p = Path(path)
    if p.exists():
        return p
    alt = repo_root() / p
    if alt.exists():
        return alt
    try:  # installed wheel: data lives under worldline/_data
        from worldline.resources import resolve as _resolve
        r = Path(_resolve(p))
        if r.exists():
            return r
    except Exception:
        pass
    return p


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def verdicts(trace: Trace, scenario: Scenario, invariants: dict[str, Invariant]) -> list[VerdictRecord]:
    """Deterministic verdicts (evaluator clock fixed at 0 so derived_at_ns never drifts)."""
    return check_all(trace, invariants, scenario=scenario, evaluate_rule=rule_evaluator(scenario), now_ns=0)


def schedule_from_dicts(items: Iterable[dict]) -> tuple[Intervention, ...]:
    return tuple(Intervention.from_dict(d) for d in items)


# ------------------------------------------------------------------ claim results (agent and kernel share these)

def verdict_results(vs: Iterable[VerdictRecord], prefix: str) -> dict[str, dict]:
    return {f"{prefix}{v.invariant_id}": v.to_dict() for v in vs}


def first_decision_fail_index(result_dict: dict, invariant_id: str = TARGET_INVARIANT) -> int | None:
    """Index (in exploration order, root = 0) of the first branch failing `invariant_id`."""
    for i, n in enumerate(result_dict["nodes"]):
        if invariant_id in n.get("failing_invariants", []):
            return i
    return None


def comparison_row(result_dict: dict) -> dict:
    return {
        "n_nodes": result_dict["n_nodes"],
        "n_failing": result_dict["n_failing"],
        "failing_rate_per_100": result_dict["failing_rate_per_100"],
        "wall_ms": result_dict["wall_ns"] / 1e6,
        "first_decision_fail_index": first_decision_fail_index(result_dict),
    }


def comparison_claim_result(comparison: dict) -> dict:
    """The deterministic part of the comparison: timing never enters an evidence result."""
    return {s: {k: v for k, v in row.items() if k != "wall_ms"} for s, row in comparison.items()}


def minimal_claim_result(scenario: Scenario, invariants: dict[str, Invariant], original: tuple, minimal: tuple,
                         target: str) -> dict:
    oracle = failure_oracle(scenario, invariants, target)
    return {
        "target_invariant": target,
        "original_size": len(original),
        "minimal_size": len(minimal),
        "original_fails": bool(oracle(original)),
        "one_minimal_verified": bool(one_minimal_check(minimal, oracle)),
    }


def challenge_claim_result(primary: dict, controls: list[dict]) -> dict:
    return {
        "genome_id": primary["genome_id"],
        "primary": {"scenario_id": primary["scenario_id"], "reproduced": primary["reproduced"],
                    "preconditions_met": primary["preconditions_met"],
                    "counterexample_digest": primary["counterexample_digest"]},
        "controls": [{"scenario_id": c["scenario_id"], "reproduced": c["reproduced"],
                      "preconditions_met": c["preconditions_met"]} for c in controls],
        "transfer_claim": "within the shared Event v1 substrate across scenarios; not across heterogeneous codebases",
    }


def challenge_success(primary: dict, controls: list[dict]) -> bool:
    return bool(primary["reproduced"]) and all(not c["reproduced"] for c in controls)


def run_challenge(genome: FailureGenome, scenario_path: str | Path, invariants_path: str | Path,
                  ) -> tuple[ChallengeResult, Scenario]:
    scenario = load_scenario(resolve_repo_path(scenario_path))
    inv = load_invariants(resolve_repo_path(invariants_path))  # genome picks its own invariant
    return challenge(genome, scenario, inv), scenario


def parse_junit(path: Path | str) -> dict:
    """Totals from a JUnit XML report. Shared by agent and kernel so results compare equal."""
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tot = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for s in suites:
        for k in tot:
            tot[k] += int(s.get(k, 0) or 0)
    tot["passed"] = tot["tests"] - tot["failures"] - tot["errors"] - tot["skipped"]
    return tot


def find_junit(out_dir: Path) -> Path | None:
    for cand in (Path("report.xml"), out_dir / "report.xml"):
        if cand.exists():
            return cand
    return None


# ------------------------------------------------------------------ environment metadata

def git_commit() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10,
                           cwd=str(repo_root()))
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def uv_version() -> str | None:
    try:
        r = subprocess.run(["uv", "--version"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def environment() -> dict:
    return {
        "python": sys.version.split()[0],
        "python_full": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "uv": uv_version(),
        "commit": git_commit(),
    }


def file_digest(path: Path) -> str:
    return "sha256:" + __import__("hashlib").sha256(path.read_bytes()).hexdigest()


def inputs_hash(*parts: Any) -> str:
    return content_hash(list(parts))


def fail_ids(vs: Iterable[VerdictRecord]) -> list[str]:
    return sorted(v.invariant_id for v in vs if v.result == Verdict.FAIL)
