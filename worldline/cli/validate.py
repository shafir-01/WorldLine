"""Scenario authoring commands: ``worldline validate`` and ``worldline init``.

This module exposes a Typer app ``app`` with two commands. Register it on the main
CLI without a name so both become top-level commands::

    from worldline.cli import validate
    app.add_typer(validate.app)          # -> worldline validate ..., worldline init ...

(or call ``validate.register(app)``, which does the same thing).

    worldline validate scenarios/triage_board.yaml [--invariants invariants] [--strict] [--json]
    worldline validate --example > my_scenario.yaml
    worldline init scenarios/my_scenario.yaml [--id my_scenario] [--force]

Exit codes: 0 valid (warnings allowed unless --strict), 1 errors found, 2 usage error.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console

from worldline.worlds.schema import EXAMPLE_SCENARIO, ValidationReport, scaffold, validate_file

app = typer.Typer(help="Author scenarios: validate them, scaffold new ones.", add_completion=False,
                  pretty_exceptions_enable=False)

console = Console(highlight=False)
DEFAULT_INVARIANTS = Path("invariants")


def register(parent: typer.Typer) -> None:
    """Attach ``validate`` and ``init`` to ``parent`` as top-level commands."""
    parent.add_typer(app)


def _echo(msg: str = "", style: str | None = None) -> None:
    console.print(msg, markup=False, soft_wrap=True, style=style)


def _resolve_invariants(path: Optional[Path], explicit: bool) -> Optional[Path]:
    """The invariants directory to check references against, or None to skip that check."""
    if path is None:
        return None
    if path.exists():
        return path
    repo_alt = Path(__file__).resolve().parents[2] / path
    if not path.is_absolute() and repo_alt.exists():
        return repo_alt
    return path if explicit else None   # explicit but missing -> reported as an error by the validator


def _print_report(r: ValidationReport, strict: bool) -> None:
    for i in r.issues:
        where = f"{r.source}:{i.line}" if i.line else f"{r.source}"
        style = "bold red" if i.severity == "error" else "yellow"
        _echo(f"{where}: {i.severity}: {i}", style=style)
    n_err, n_warn = len(r.errors), len(r.warnings)
    failed = n_err > 0 or (strict and n_warn > 0)
    s = r.summary
    shape = (f"{s.get('roles', 0)} roles, {s.get('actors', 0)} actors, {s.get('decision_rules', 0)} decision rules, "
             f"{s.get('injects', 0)} injects, {s.get('transitions', 0)} transitions") if s else ""
    if failed:
        _echo(f"FAIL {r.source}: {n_err} error(s), {n_warn} warning(s)", style="bold red")
    else:
        _echo(f"OK   {r.source}: valid ({shape}); {n_warn} warning(s)", style="green")


@app.command("validate")
def validate_cmd(
    scenarios: Optional[List[Path]] = typer.Argument(None, help="Scenario YAML file(s) to validate."),
    invariants: Optional[Path] = typer.Option(None, "--invariants",
                                              help="Invariants directory or file to resolve the scenario's "
                                                   "`invariants:` ids against [default: invariants/ if present]."),
    example: bool = typer.Option(False, "--example", help="Print a minimal valid scenario and exit."),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors."),
    as_json: bool = typer.Option(False, "--json", help="Print the reports as JSON."),
) -> None:
    """Check scenario YAML: schema, predicate DSL, references, and suspicious timings."""
    if example:
        typer.echo(EXAMPLE_SCENARIO, nl=False)
        raise typer.Exit(code=0)
    if not scenarios:
        _echo("error: give at least one scenario file, or --example", style="bold red")
        raise typer.Exit(code=2)
    inv = _resolve_invariants(invariants if invariants is not None else DEFAULT_INVARIANTS, invariants is not None)
    reports = [validate_file(p, invariants=inv) for p in scenarios]
    failed = any(not r.ok or (strict and r.warnings) for r in reports)
    if as_json:
        typer.echo(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        for r in reports:
            _print_report(r, strict)
        if inv is None:
            _echo("note: no invariants directory found; invariant ids were not checked (use --invariants)")
    raise typer.Exit(code=1 if failed else 0)


_ID_OK = re.compile(r"^[A-Za-z0-9_.-]+$")


@app.command("init")
def init_cmd(
    path: Path = typer.Argument(..., help="Where to write the new scenario YAML."),
    scenario_id: Optional[str] = typer.Option(None, "--id", help="Scenario id [default: the file name stem]."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Scaffold a commented scenario template that validates as-is."""
    sid = scenario_id or re.sub(r"[^A-Za-z0-9_.-]+", "_", path.stem) or "my_scenario"
    if not _ID_OK.match(sid):
        _echo(f"error: scenario id {sid!r} may only contain letters, digits, '_', '.', '-'", style="bold red")
        raise typer.Exit(code=2)
    if path.exists() and not force:
        _echo(f"error: {path} already exists (use --force to overwrite)", style="bold red")
        raise typer.Exit(code=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scaffold(sid), encoding="utf-8", newline="\n")
    _echo(f"wrote {path} (scenario id {sid})")
    _echo(f"next: edit it, then  uv run worldline validate {path}  and  uv run worldline run {path}")
