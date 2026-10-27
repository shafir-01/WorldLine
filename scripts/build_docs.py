"""Build the WORLDLINE documentation site.

Steps:

1. Stage the existing markdown (docs/*.md, service/README.md, ui/README.md, and the
   optional docs/AUTHORING.md, WORKFLOW.md, INSTALL.md, INVARIANTS.md, REAL_*.md and
   CHANGELOG.md when they exist) into docs/site/, rewriting
   relative links: links to other staged documents point at their staged page,
   images under docs/screens point at docs/site/assets/screens/, and links to any
   other file in the repository point at that file on GitLab.
2. Generate docs/site/reference/cli.md from the live CLI: the command tree and
   options come from the Typer app, the verbatim help text from
   `uv run worldline <command> --help`. Generate docs/site/reference/invariants.md
   from invariants/*.yaml.
3. Run `uvx --with mkdocs-material mkdocs build --strict` into public/docs (or --out).

Staged and generated files are listed in docs/site/.gitignore; the hand-written
pages are the only sources under docs/site/ that belong in git. An optional source that
is absent falls back to its hand-written page in docs/site/_partials/ or is dropped
from the nav.

Runs on python:3.12 with uv on PATH (`pip install uv`). Standard library only.

    python scripts/build_docs.py                 # -> public/docs
    python scripts/build_docs.py --out public_docs
    python scripts/build_docs.py --stage-only    # stage + generate, no mkdocs build
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "docs" / "site"
REPO_URL = "https://gitlab.com/shafir2/WorldLine"
BLOB = f"{REPO_URL}/-/blob/main/"
TREE = f"{REPO_URL}/-/tree/main/"

MKDOCS = ["uvx", "--from", "mkdocs==1.6.1", "--with", "mkdocs-material==9.8.0", "mkdocs"]

RESEARCH_NOTE = (
    '!!! note "Technical background"\n'
    "    This page is part of the research record and is included as written. "
    "It explains *why* WORLDLINE is built the way it is and what has been measured. "
    "For using WORLDLINE, start with [Get started](../get-started/install.md).\n\n"
)
INCLUDED_NOTE = (
    '!!! info "Included from the repository"\n'
    "    This page is `{src}` from the repository, included as written.\n\n"
)

# source (repo-relative) -> (staged path under docs/site, required?, banner)
STAGED: dict[str, tuple[str, bool, str]] = {
    "docs/THESIS.md": ("research/thesis.md", True, RESEARCH_NOTE),
    "docs/REPORT.md": ("research/report.md", True, RESEARCH_NOTE),
    "docs/ROADMAP.md": ("research/roadmap.md", True, RESEARCH_NOTE),
    "docs/ARCHITECTURE.md": ("reference/architecture.md", True, INCLUDED_NOTE),
    "docs/RELEASES.md": ("guides/releases.md", True, INCLUDED_NOTE),
    "docs/GITLAB_SETUP.md": ("guides/gitlab-setup.md", True, INCLUDED_NOTE),
    "docs/REAL_DATA.md": ("guides/real-data.md", False, INCLUDED_NOTE),
    "docs/REAL_SERVICE.md": ("guides/real-service.md", False, INCLUDED_NOTE),
    "docs/AUTHORING.md": ("guides/authoring.md", False, INCLUDED_NOTE),
    "docs/WORKFLOW.md": ("guides/gitlab-workflow.md", False, INCLUDED_NOTE),
    "docs/INSTALL.md": ("get-started/install-release.md", False, INCLUDED_NOTE),
    "docs/INVARIANTS.md": ("reference/invariant-catalogue.md", False, INCLUDED_NOTE),
    "service/README.md": ("reference/verifier-api.md", True, INCLUDED_NOTE),
    "ui/README.md": ("reference/viewer.md", True, INCLUDED_NOTE),
    "CHANGELOG.md": ("changelog.md", False, ""),
}
# Hand-written fallbacks (excluded from the site by exclude_docs) used when an optional source is absent.
FALLBACKS = {
    "guides/authoring.md": "_partials/authoring.md",
    "guides/gitlab-workflow.md": "_partials/gitlab-workflow.md",
}
SCREENS_SRC = ROOT / "docs" / "screens"
SCREENS_DST = SITE / "assets" / "screens"

LINK = re.compile(r"(!?\[[^\]\n]*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")
FENCE = re.compile(r"^\s*(```|~~~)")


def log(msg: str) -> None:
    print(f"[docs] {msg}", flush=True)


# ---------------------------------------------------------------------------- staging


def rewrite_target(target: str, src_rel: str, dst_rel: str, staged_map: dict[str, str]) -> str:
    """Map one link target found in `src_rel` (repo path) for a page staged at `dst_rel` (site path)."""
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("#", "/")):
        return target
    path, _, anchor = target.partition("#")
    anchor = f"#{anchor}" if anchor else ""
    if not path:
        return target
    repo_path = posixpath.normpath(posixpath.join(posixpath.dirname(src_rel), path))
    dst_dir = posixpath.dirname(dst_rel) or "."
    if repo_path in staged_map:
        return posixpath.relpath(staged_map[repo_path], dst_dir) + anchor
    if repo_path.startswith("docs/screens/"):
        site_path = "assets/screens/" + repo_path[len("docs/screens/"):]
        return posixpath.relpath(site_path, dst_dir) + anchor
    full = ROOT / repo_path
    if full.is_dir():
        return TREE + repo_path.rstrip("/") + anchor
    if full.exists():
        return BLOB + repo_path + anchor
    log(f"WARNING {src_rel}: link to missing repository path {target!r} left unchanged")
    return target


def rewrite_links(text: str, src_rel: str, dst_rel: str, staged_map: dict[str, str]) -> str:
    out, in_fence = [], False
    for line in text.splitlines(keepends=True):
        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        out.append(LINK.sub(lambda m: m.group(1) + rewrite_target(m.group(2), src_rel, dst_rel, staged_map)
                            + m.group(3), line))
    return "".join(out)


def add_banner(text: str, banner: str, src_rel: str) -> str:
    """Insert the banner after the first H1 so the page title stays the document's own."""
    if not banner:
        return text
    banner = banner.format(src=src_rel)
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return "".join(lines[: i + 1]) + "\n" + banner + "".join(lines[i + 1:])
    return banner + text


def stage() -> list[str]:
    """Copy and rewrite. Returns the site paths of optional pages that are absent."""
    staged_map = {src: dst for src, (dst, _, _) in STAGED.items() if (ROOT / src).exists()}
    missing_optional: list[str] = []
    for src, (dst, required, banner) in STAGED.items():
        src_path, dst_path = ROOT / src, SITE / dst
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if not src_path.exists():
            if required:
                raise SystemExit(f"[docs] required source {src} is missing")
            fallback = FALLBACKS.get(dst)
            if fallback and (SITE / fallback).exists():
                shutil.copyfile(SITE / fallback, dst_path)
                log(f"{src} absent; using hand-written {fallback} for {dst}")
            else:
                dst_path.unlink(missing_ok=True)
                missing_optional.append(dst)
                log(f"{src} absent; {dst} left out of the nav")
            continue
        text = src_path.read_text(encoding="utf-8")
        text = add_banner(rewrite_links(text, src, dst, staged_map), banner, src)
        dst_path.write_text(text, encoding="utf-8", newline="\n")
        log(f"staged {src} -> docs/site/{dst}")
    if SCREENS_DST.exists():
        shutil.rmtree(SCREENS_DST)
    shutil.copytree(SCREENS_SRC, SCREENS_DST)
    log(f"copied {len(list(SCREENS_DST.glob('*.png')))} screenshots -> docs/site/assets/screens/")
    return missing_optional


# ---------------------------------------------------------------------------- CLI reference

INTROSPECT = r"""
import json, typer
from worldline.cli.main import app

def walk(cmd, path):
    params = []
    for p in cmd.params:
        if p.name == "help":
            continue
        params.append({
            "name": p.name,
            "kind": "argument" if getattr(p, "param_type_name", "") == "argument" else "option",
            "opts": list(p.opts) + list(getattr(p, "secondary_opts", []) or []),
            "type": p.type.name if hasattr(p.type, "name") else str(p.type),
            "required": bool(p.required),
            "default": None if callable(p.default) else p.default,
            "is_flag": bool(getattr(p, "is_flag", False)),
            "help": (getattr(p, "help", None) or "").strip(),
        })
    node = {"path": path, "help": (cmd.help or "").strip(), "params": params, "children": []}
    if hasattr(cmd, "list_commands"):  # duck-typed: recent Typer vendors its own click
        for name in cmd.list_commands(None):
            node["children"].append(walk(cmd.get_command(None, name), path + [name]))
    return node

print(json.dumps(walk(typer.main.get_command(app), []),
                 default=lambda o: o.as_posix() if hasattr(o, "as_posix") else str(o)))
"""


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    env = dict(os.environ, NO_COLOR="1", TERM="dumb", COLUMNS="100", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", **kw)


def cli_help(path: list[str]) -> str:
    res = run(["uv", "run", "--quiet", "worldline", *path, "--help"])
    if res.returncode != 0:
        raise SystemExit(f"[docs] `worldline {' '.join(path)} --help` failed:\n{res.stderr}")
    return "\n".join(line.rstrip() for line in res.stdout.strip("\n").splitlines())


def md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def fmt_default(p: dict) -> str:
    d = p["default"]
    if p["is_flag"]:
        return "off" if not d else "on"
    if d is None or d == "" or d == ():
        return ""
    return f"`{d}`"


def cli_reference() -> None:
    res = run(["uv", "run", "--quiet", "python", "-c", INTROSPECT])
    if res.returncode != 0:
        raise SystemExit(f"[docs] could not introspect the CLI:\n{res.stderr}")
    tree = json.loads(res.stdout)

    lines = [
        "# CLI reference",
        "",
        "!!! info \"Generated\"",
        "    This page is generated by `scripts/build_docs.py` from the installed `worldline` command:",
        "    the option tables come from the command definitions, the help blocks are the verbatim output of",
        "    `uv run worldline <command> --help`. Edit the command, not this page.",
        "",
        "Every command prints the digests it produced, so two runs can be compared by eye.",
        "Durations accept a unit suffix where noted (`90s`, `5m`).",
        "",
        "## Commands",
        "",
        "| Command | What it does |",
        "|---|---|",
    ]
    flat: list[dict] = []

    def collect(node: dict) -> None:
        for child in node["children"]:
            flat.append(child)
            collect(child)

    collect(tree)
    for node in flat:
        name = " ".join(node["path"])
        anchor = "worldline-" + "-".join(node["path"])
        summary = node["help"].split("\n\n")[0].replace("\n", " ")
        lines.append(f"| [`worldline {name}`](#{anchor}) | {md_escape(summary)} |")
    lines += ["", "## `worldline`", "", "```text", cli_help([]), "```", ""]

    for node in flat:
        name = " ".join(node["path"])
        anchor = "worldline-" + "-".join(node["path"])
        lines += [f"## `worldline {name}` {{#{anchor}}}", ""]
        if node["help"]:
            lines += [node["help"], ""]
        args = [p for p in node["params"] if p["kind"] == "argument"]
        opts = [p for p in node["params"] if p["kind"] == "option"]
        if args:
            lines += ["**Arguments**", "", "| Name | Type | Required | Description |", "|---|---|---|---|"]
            for p in args:
                lines.append(f"| `{p['name'].upper()}` | {p['type']} | {'yes' if p['required'] else 'no'} | "
                             f"{md_escape(p['help'])} |")
            lines.append("")
        if opts:
            lines += ["**Options**", "", "| Option | Type | Default | Description |", "|---|---|---|---|"]
            for p in opts:
                names = ", ".join(f"`{o}`" for o in p["opts"])
                typ = "flag" if p["is_flag"] else p["type"]
                req = " **(required)**" if p["required"] else ""
                lines.append(f"| {names} | {typ} | {fmt_default(p)} | {md_escape(p['help'])}{req} |")
            lines.append("")
        lines += ["??? example \"`--help` output\"", "", "    ```text"]
        lines += [("    " + line) if line else "" for line in cli_help(node["path"]).splitlines()]
        lines += ["    ```", ""]

    out = SITE / "reference" / "cli.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    log(f"generated docs/site/reference/cli.md ({len(flat)} commands)")


# ---------------------------------------------------------------------------- invariants reference

INVARIANTS_DUMP = r"""
import json, pathlib, yaml
out = []
for f in sorted([*pathlib.Path("invariants").glob("*.yaml"), *pathlib.Path("invariants").glob("*.yml")]):
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    items = data.get("invariants", []) if isinstance(data, dict) else data
    out.append({"file": f.as_posix(), "invariants": items})
print(json.dumps(out))
"""


def invariants_reference(staged_optional: set[str]) -> None:
    res = run(["uv", "run", "--quiet", "python", "-c", INVARIANTS_DUMP])
    if res.returncode != 0:
        raise SystemExit(f"[docs] could not read invariants/:\n{res.stderr}")
    files = json.loads(res.stdout)
    lines: list[str] = []
    total = 0
    for f in files:
        lines += [f"## `{f['file']}`", "",
                  "| Id | Kind | Version | Required signals | What it checks |", "|---|---|---|---|---|"]
        for inv in f["invariants"]:
            total += 1
            sig = ", ".join(f"`{x}`" for x in inv.get("required_signals") or []) or "none"
            desc = " ".join(str(inv.get("description", "")).split())
            lines.append(f"| `{inv['id']}` | {inv.get('kind', '')} | {inv.get('version', '')} | {sig} | "
                         f"{md_escape(desc)} |")
        lines.append("")
    if "reference/invariant-catalogue.md" in staged_optional:
        lines += ["Parameters, witnesses and worked examples for each invariant are in the",
                  "[Invariant catalogue](invariant-catalogue.md).", ""]
    lines = ['!!! info "Generated"',
             "    The tables below are generated by `scripts/build_docs.py` from `invariants/*.yaml`.", ""] + lines
    template = (SITE / "_partials" / "invariants.md").read_text(encoding="utf-8")
    out = SITE / "reference" / "invariants.md"
    out.write_text(template.replace("{{INVARIANTS_TABLE}}", "\n".join(lines).rstrip() + "\n"),
                   encoding="utf-8", newline="\n")
    log(f"generated docs/site/reference/invariants.md ({total} invariants in {len(files)} files)")


# ---------------------------------------------------------------------------- mkdocs


def mkdocs_config(missing_optional: list[str]) -> str:
    """mkdocs.yml with nav entries for absent optional pages removed."""
    text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    keep = []
    for line in text.splitlines(keepends=True):
        m = re.search(r":\s*([\w./-]+\.md)\s*$", line)
        if m and m.group(1) in missing_optional:
            continue
        keep.append(line)
    return "".join(keep)


def build(out: Path, missing_optional: list[str]) -> None:
    cmd = [*MKDOCS, "build", "--strict", "--clean", "-f", "-", "-d", str(out)]
    log("running " + " ".join(cmd))
    res = subprocess.run(cmd, cwd=ROOT, input=mkdocs_config(missing_optional), text=True, encoding="utf-8",
                         env=dict(os.environ, NO_MKDOCS_2_WARNING="1"))
    if res.returncode != 0:
        raise SystemExit(f"[docs] mkdocs build failed (exit {res.returncode})")
    log(f"site written to {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="public/docs", help="output directory (default: public/docs)")
    ap.add_argument("--stage-only", action="store_true", help="stage and generate pages, skip mkdocs")
    args = ap.parse_args()
    if shutil.which("uv") is None:
        sys.exit("[docs] uv is not on PATH (pip install uv)")
    missing = stage()
    staged = {dst for src, (dst, _, _) in STAGED.items() if (ROOT / src).exists()}
    cli_reference()
    invariants_reference(staged)
    if not args.stage_only:
        out = Path(args.out)
        build(out if out.is_absolute() else ROOT / out, missing)


if __name__ == "__main__":
    main()
