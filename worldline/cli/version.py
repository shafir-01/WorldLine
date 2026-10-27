"""`--version` callback, build info and the `worldline resources` sub-app.

Wiring (in worldline/cli/main.py)::

    from .version import register
    register(app)            # adds --version/-V to the root app and the `resources` sub-command

or piece by piece::

    from .version import resources_app, version_option
    @app.callback()
    def _root(version: bool = version_option()) -> None: ...
    app.add_typer(resources_app, name="resources")
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import typer

from worldline import __version__
from worldline import resources as R

COMMIT_ENV_VARS = ("WORLDLINE_COMMIT", "GIT_COMMIT", "CI_COMMIT_SHA")


def _git_commit(cwd: Path) -> str | None:
    if not (cwd / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else None


def build_info() -> dict[str, Any]:
    """Version, source commit (env var, else the checkout's HEAD, else None), Python and platform."""
    commit = next((os.environ[v] for v in COMMIT_ENV_VARS if os.environ.get(v)), None)
    if commit is None:
        commit = _git_commit(R.CHECKOUT_ROOT)
    return {
        "version": __version__,
        "commit": commit,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(terse=True),
        "data": R.data_layout(),
    }


def version_string() -> str:
    info = build_info()
    commit = f" ({info['commit'][:12]})" if info["commit"] else ""
    return f"worldline {info['version']}{commit} python {info['python']} {info['platform']}"


def version_callback(value: Optional[bool]) -> None:
    """Eager Typer callback: print the version line and exit."""
    if value:
        typer.echo(version_string())
        raise typer.Exit()


def version_option() -> Any:
    return typer.Option(None, "--version", "-V", callback=version_callback, is_eager=True,
                        help="Show version, commit and Python, then exit.")


# ------------------------------------------------------------------ resources

resources_app = typer.Typer(help="Show (or copy out) the scenarios, invariants, policy and public key bundled with this install.",
                            invoke_without_command=True)


@resources_app.callback(invoke_without_command=True)
def resources(as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
              copy_to: Optional[Path] = typer.Option(None, "--copy-to",
                                                     help="Copy the bundled data into this directory (a ready working dir)."),
              overwrite: bool = typer.Option(False, "--overwrite", help="With --copy-to: replace existing files.")) -> None:
    """Print where the bundled data lives; with --copy-to, materialise it so every CLI default resolves."""
    if copy_to is not None:
        written = R.copy_to(copy_to, overwrite=overwrite)
        typer.echo(f"copied {len(written)} files into {copy_to}")
        typer.echo(f"next: cd {copy_to} && worldline demo --skip-tests")
        return
    d = R.describe()
    if as_json:
        typer.echo(json.dumps({**d, "build": build_info()}, indent=2))
        return
    typer.echo(f"worldline {__version__}  data layout: {d['layout']}")
    for key in ("scenarios_dir", "invariants_dir", "policy", "public_key"):
        typer.echo(f"  {key:<15}{d[key] or '(not found)'}")
    typer.echo("  scenarios:")
    for s in d["scenarios"]:
        typer.echo(f"    {s}")
    if d["scenarios"] and d["invariants_dir"]:
        example = next((s for s in d["scenarios"] if s.endswith("port_alert.yaml")), d["scenarios"][0])
        typer.echo(f'example: worldline run "{example}" --invariants "{d["invariants_dir"]}" --out wl-out')


def register(app: typer.Typer) -> None:
    """Attach --version/-V (root callback) and the `resources` sub-command to the WORLDLINE app.

    Only use when `app` has no callback of its own; otherwise add `version: bool = version_option()`
    to the existing callback's signature.
    """
    @app.callback()
    def _root(version: Optional[bool] = version_option()) -> None:  # noqa: ARG001 - handled eagerly
        """WORLDLINE: branch executions, isolate the causal difference, type the evidence, gate the release."""

    app.add_typer(resources_app, name="resources")


if __name__ == "__main__":  # `python -m worldline.cli.version [--json]` before the sub-app is wired
    resources_app(prog_name="worldline resources")
