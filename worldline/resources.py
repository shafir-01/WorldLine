"""Locate WORLDLINE's bundled data (scenarios, invariants, release policy, kernel public key).

Two layouts are supported:

* **checkout**: the data lives at the repository root (``<repo>/scenarios``, ``<repo>/invariants``,
  ``<repo>/policies``), next to the ``worldline`` package.
* **installed wheel**: the build copies the same files into the package as ``worldline/_data/...``
  (see ``[tool.hatch.build.targets.wheel.force-include]`` in pyproject.toml), so the CLI works from
  ``pip install``/``uvx`` with no checkout.

``resource_path("invariants")`` returns the first existing location, preferring the checkout so
edits to the repository take effect immediately. The private kernel key is never packaged.
"""
from __future__ import annotations

import shutil
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGED_DATA = PACKAGE_DIR / "_data"
CHECKOUT_ROOT = PACKAGE_DIR.parent

# Relative names shipped with the wheel (directories or files).
BUNDLED = ("scenarios", "invariants", "policies/release.yaml", "policies/kernel_public.pem")


def data_roots() -> list[Path]:
    """Roots searched for bundled data, in priority order (checkout first, then the wheel)."""
    roots: list[Path] = []
    if (CHECKOUT_ROOT / "pyproject.toml").exists() and (CHECKOUT_ROOT / "invariants").is_dir():
        roots.append(CHECKOUT_ROOT)
    if PACKAGED_DATA.is_dir():
        roots.append(PACKAGED_DATA)
    return roots


def resource_path(relative: str | Path) -> Path | None:
    """Absolute path of a bundled resource such as ``scenarios/port_alert.yaml``, or None."""
    rel = Path(relative)
    if rel.is_absolute():
        return rel if rel.exists() else None
    for root in data_roots():
        p = root / rel
        if p.exists():
            return p
    return None


def resolve(path: str | Path) -> Path:
    """A path as given when it exists (relative to cwd), else the bundled resource, else unchanged.

    Drop-in replacement for ``worldline.cli.common.resolve_repo_path`` that also works from a wheel.
    """
    p = Path(path)
    if p.exists():
        return p
    found = resource_path(p)
    return found if found is not None else p


def data_layout() -> str:
    roots = data_roots()
    if not roots:
        return "missing"
    return "checkout" if roots[0] == CHECKOUT_ROOT else "wheel"


def bundled_scenarios() -> list[Path]:
    root = resource_path("scenarios")
    if root is None:
        return []
    return sorted(p for p in root.rglob("*.y*ml") if p.suffix in (".yaml", ".yml"))


def describe() -> dict:
    """Machine-readable summary used by ``worldline resources``."""
    def s(p: Path | None) -> str | None:
        return str(p) if p is not None else None
    return {
        "layout": data_layout(),
        "roots": [str(r) for r in data_roots()],
        "scenarios_dir": s(resource_path("scenarios")),
        "invariants_dir": s(resource_path("invariants")),
        "policy": s(resource_path("policies/release.yaml")),
        "public_key": s(resource_path("policies/kernel_public.pem")),
        "scenarios": [str(p) for p in bundled_scenarios()],
    }


def copy_to(dest: str | Path, overwrite: bool = False) -> list[Path]:
    """Materialise the bundled data into ``dest`` (a working directory where every CLI default resolves).

    Returns the paths written. Existing files are kept unless ``overwrite``.
    """
    dest = Path(dest)
    written: list[Path] = []
    for name in BUNDLED:
        src = resource_path(name)
        if src is None:
            continue
        target = dest / name
        files = [(f, target / f.relative_to(src)) for f in src.rglob("*") if f.is_file()] if src.is_dir() else [(src, target)]
        for f, t in files:
            if "__pycache__" in f.parts or (t.exists() and not overwrite):
                continue
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, t)
            written.append(t)
    return written


__all__ = ["BUNDLED", "PACKAGED_DATA", "bundled_scenarios", "copy_to", "data_layout", "data_roots",
           "describe", "resolve", "resource_path"]
