"""Packaging: metadata, bundled-data lookup, release tooling, and a build + install smoke test.

The smoke test builds the wheel and sdist with `uv build`, installs the wheel into a fresh venv
outside the checkout, and runs the installed `worldline` against the scenario path the install
itself reports. It is skipped when uv is not on PATH (or in $UV).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

import worldline
from worldline import resources as R
from worldline.cli.version import build_info, version_string

ROOT = Path(__file__).resolve().parents[1]
UV = os.environ.get("UV") or shutil.which("uv")


def _load_release_module():
    spec = importlib.util.spec_from_file_location("wl_release", ROOT / "scripts" / "release.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ fast checks

def test_version_is_semver_and_single_sourced():
    assert re.fullmatch(r"\d+\.\d+\.\d+", worldline.__version__)
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in text
    assert 'path = "worldline/__init__.py"' in text
    assert not re.search(r'^version\s*=', text, re.M), "version must come from worldline/__init__.py"


def test_build_info_and_version_string():
    info = build_info()
    assert info["version"] == worldline.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", info["python"])
    assert {"commit", "platform", "data"} <= info.keys()
    assert version_string().startswith(f"worldline {worldline.__version__}")


def test_resources_resolve_from_checkout():
    assert R.data_layout() in {"checkout", "wheel"}
    for name in R.BUNDLED:
        p = R.resource_path(name)
        assert p is not None and p.exists(), name
    assert any(p.name == "port_alert.yaml" for p in R.bundled_scenarios())
    # an existing relative path wins; a missing one falls back to the bundled copy
    assert R.resolve("invariants").exists()
    assert R.resolve(Path("does") / "not" / "exist") == Path("does") / "not" / "exist"


def test_resources_copy_to(tmp_path):
    written = R.copy_to(tmp_path)
    assert (tmp_path / "invariants").is_dir()
    assert (tmp_path / "policies" / "release.yaml").is_file()
    assert (tmp_path / "policies" / "kernel_public.pem").is_file()
    assert not (tmp_path / "policies" / "kernel_private.pem").exists()
    assert (tmp_path / "scenarios" / "port_alert.yaml").is_file()
    assert written and R.copy_to(tmp_path) == []  # idempotent without --overwrite


def test_changelog_has_unreleased_and_versions():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in text
    for v in ("0.1.0", "0.1.1", "0.1.2", "0.1.3"):
        assert f"## [{v}]" in text


def test_release_script_groups_and_rewrites_changelog():
    rel = _load_release_module()
    assert rel.bump("0.1.3", "patch") == "0.1.4"
    assert rel.bump("0.1.3", "minor") == "0.2.0"
    assert rel.bump("0.1.3", "major") == "1.0.0"
    assert rel.classify("feat(cli): add resources command")[0] == "Added"
    assert rel.classify("fix: off-by-one in ddmin")[0] == "Fixed"
    assert rel.classify("CI: create releases through the API")[0] == "CI and releases"
    assert rel.classify("README: structure tree")[0] == "Documentation"

    before = ("# Changelog\n\nintro\n\n## [Unreleased]\n\n### Added\n\n- Hand-written note (abc1234).\n\n"
              "## [0.1.0] - 2026-01-01\n\n- first\n\n"
              "[Unreleased]: https://example/-/compare/v0.1.0...HEAD\n[0.1.0]: https://example/-/tags/v0.1.0\n")
    commits = [("abc1234", "feat: already described"), ("def5678", "fix: a bug"), ("0a0a0a0", "Release v0.1.0")]
    entry = rel.render_entry("0.2.0", "2026-02-02", commits, rel.unreleased_text(before))
    assert entry.startswith("## [0.2.0] - 2026-02-02")
    assert entry.count("### Added") == 1 and "Hand-written note" in entry
    assert "already described" not in entry  # its sha is already in the hand-written notes
    assert "- A bug (def5678)" in entry and "Release v0.1.0" not in entry

    after = rel.update_changelog(before, "0.2.0", entry, "v0.1.0")
    unrel = after.index("## [Unreleased]")
    assert after.index("## [0.2.0]") > unrel and after.index("## [0.1.0]") > after.index("## [0.2.0]")
    assert rel.unreleased_text(after) == ""
    assert "[Unreleased]: https://gitlab.com/shafir2/WorldLine/-/compare/v0.2.0...HEAD" in after
    assert "[0.2.0]: https://gitlab.com/shafir2/WorldLine/-/compare/v0.1.0...v0.2.0" in after
    assert "[0.1.0]: https://example/-/tags/v0.1.0" in after


# ------------------------------------------------------------------ build + install smoke test

def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    assert r.returncode == 0, f"{' '.join(map(str, cmd))} -> {r.returncode}\n{r.stdout}\n{r.stderr}"
    return r


@pytest.fixture(scope="module")
def dist(tmp_path_factory) -> Path:
    if not UV:
        pytest.skip("uv not found on PATH (set $UV to its path)")
    out = tmp_path_factory.mktemp("dist")
    _run([UV, "build", "--out-dir", out], ROOT)
    return out


def test_build_produces_wheel_and_sdist(dist):
    wheels, sdists = list(dist.glob("*.whl")), list(dist.glob("*.tar.gz"))
    assert len(wheels) == 1 and len(sdists) == 1
    v = worldline.__version__
    assert wheels[0].name == f"worldline-{v}-py3-none-any.whl"
    names = zipfile.ZipFile(wheels[0]).namelist()
    for required in ("worldline/_data/invariants/core.yaml", "worldline/_data/scenarios/port_alert.yaml",
                     "worldline/_data/policies/release.yaml", "worldline/_data/policies/kernel_public.pem",
                     "worldline/resources.py", "worldline/cli/version.py"):
        assert required in names, required
    assert not any("kernel_private" in n or "__pycache__" in n for n in names)
    meta = zipfile.ZipFile(wheels[0]).read(f"worldline-{v}.dist-info/METADATA").decode()
    assert f"Version: {v}" in meta and "Project-URL: Repository" in meta and "License-Expression: MIT" in meta
    snames = tarfile.open(sdists[0]).getnames()
    assert not any("kernel_private" in n for n in snames)
    assert f"worldline-{v}/policies/kernel_public.pem" in snames


def test_wheel_installs_and_runs_outside_checkout(dist, tmp_path):
    venv = tmp_path / "venv"
    env = {k: val for k, val in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT")}
    _run([UV, "venv", "--quiet", "--python", f"{sys.version_info.major}.{sys.version_info.minor}", venv], tmp_path, env)
    bindir = venv / ("Scripts" if os.name == "nt" else "bin")
    py = bindir / ("python.exe" if os.name == "nt" else "python")
    exe = bindir / ("worldline.exe" if os.name == "nt" else "worldline")
    wheel = next(dist.glob("*.whl"))
    _run([UV, "pip", "install", "--quiet", "--python", py, wheel], tmp_path, env)

    work = tmp_path / "work"  # no scenarios/, invariants/ or policies/ here: only the wheel's data can be used
    work.mkdir()
    assert "Usage" in _run([exe, "--help"], work, env).stdout

    # `worldline resources` once main.py wires it; the module entry point works either way.
    r = subprocess.run([str(exe), "resources", "--json"], cwd=str(work), env=env, capture_output=True, text=True)
    if r.returncode != 0:
        r = _run([py, "-m", "worldline.cli.version", "--json"], work, env)
    info = json.loads(r.stdout)
    assert info["layout"] == "wheel", info
    assert info["build"]["version"] == worldline.__version__
    scenario = next(s for s in info["scenarios"] if s.endswith("port_alert.yaml"))
    assert Path(scenario).is_file() and "site-packages" in scenario.replace("\\", "/").lower()

    out = _run([exe, "run", scenario, "--invariants", info["invariants_dir"], "--out", work / "out"], work, env)
    assert "PASS" in out.stdout
    assert (work / "out" / "traces" / "root.json").is_file()

    v = subprocess.run([str(exe), "--version"], cwd=str(work), env=env, capture_output=True, text=True)
    if v.returncode == 0 and "worldline" in v.stdout:  # once main.py wires the --version callback
        assert worldline.__version__ in v.stdout
