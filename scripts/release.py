#!/usr/bin/env python3
"""Cut a WORLDLINE release locally: bump the version, write the CHANGELOG entry, commit, tag.

    uv run python scripts/release.py patch            # 0.1.3 -> 0.1.4
    uv run python scripts/release.py minor            # 0.1.3 -> 0.2.0
    uv run python scripts/release.py --version 1.0.0  # explicit
    uv run python scripts/release.py patch --dry-run  # print the entry, change nothing

Steps:
  1. read the version from worldline/__init__.py and compute the next one;
  2. collect `git log` subjects since the last tag (`git describe --tags --abbrev=0`) and group them
     conventional-ish: `feat:`/`fix:`/`docs:`/`ci:`... prefixes when present, otherwise keywords in the
     subject ("CI:", "README", "Report", "fix", ...);
  3. insert `## [X.Y.Z] - YYYY-MM-DD` below `## [Unreleased]` in CHANGELOG.md, carrying over any
     hand-written Unreleased bullets, and update the compare links;
  4. write the new version, `git commit` only those two files, create the annotated tag `vX.Y.Z`.

Nothing is pushed. Push the tag yourself (`git push gitlab vX.Y.Z`): the tag pipeline runs the kernel
gate, then `release`, `package` (PyPI registry) and `package-image` (container registry).
Use --no-commit to stop after editing the files (no commit, no tag).
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "worldline" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
REPO_URL = "https://gitlab.com/shafir2/WorldLine"
VERSION_RE = re.compile(r'^__version__\s*=\s*"(?P<v>\d+\.\d+\.\d+)"', re.M)
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
UNRELEASED = "## [Unreleased]"

# (section, conventional prefixes, subject keywords) in display order; first match wins.
GROUPS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("Added", ("feat", "add"), ("add ", "new ", "introduce", "enable", "support")),
    ("Fixed", ("fix", "bug", "hotfix"), ("fix", "bug", "correct", "tolerat")),
    ("CI and releases", ("ci", "build", "release"),
     ("ci:", "pipeline", "release", "deploy", "docker", "package", "registry")),
    ("Documentation", ("docs", "doc"), ("readme", "docs", "report", "thesis", "submission", "wording", "notes")),
    ("Tests", ("test", "tests"), ("test",)),
    ("Changed", ("refactor", "perf", "chore", "style", "change"), ()),
]
CONVENTIONAL = re.compile(r"^(?P<type>[a-zA-Z]+)(\([^)]*\))?!?:\s*(?P<rest>.+)$")


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def current_version() -> str:
    m = VERSION_RE.search(INIT.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f'no __version__ = "X.Y.Z" in {INIT}')
    return m["v"]


def bump(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def last_tag() -> str | None:
    r = subprocess.run(["git", "describe", "--tags", "--abbrev=0"], cwd=ROOT, capture_output=True, text=True)
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def commits_since(tag: str | None) -> list[tuple[str, str]]:
    rng = f"{tag}..HEAD" if tag else "HEAD"
    out = git("log", "--no-merges", "--format=%h%x1f%s", rng)
    pairs = [line.split("\x1f", 1) for line in out.splitlines() if "\x1f" in line]
    return [(sha, subject) for sha, subject in pairs]


# Free-form "Area: subject" prefixes used in this repository's history.
AREA_HINTS = {"ci": "CI and releases", "deploy-gcp": "CI and releases", "report": "Documentation",
              "readme": "Documentation", "docs": "Documentation", "thesis": "Documentation",
              "public docs": "Documentation"}


def classify(subject: str) -> tuple[str, str]:
    """Section and cleaned subject for one commit subject."""
    m = CONVENTIONAL.match(subject)
    if m:
        t = m["type"].lower()
        for section, prefixes, _ in GROUPS:
            if t in prefixes:
                rest = m["rest"]
                return section, rest[:1].upper() + rest[1:]
    area = subject.split(":", 1)[0].strip().lower() if ":" in subject else ""
    if area in AREA_HINTS:
        return AREA_HINTS[area], subject
    low = subject.lower()
    for section, _, keywords in GROUPS:
        if any(k in low for k in keywords):
            return section, subject
    return "Changed", subject


def _parse_sections(carried: str) -> tuple[list[str], dict[str, list[str]]]:
    """Split hand-written notes into a preamble and `### Section` bodies (order kept)."""
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: list[str] = preamble
    for line in carried.strip().splitlines():
        if line.startswith("### "):
            current = sections.setdefault(line[4:].strip(), [])
        else:
            current.append(line)
    return preamble, sections


def render_entry(version: str, date: str, commits: list[tuple[str, str]], carried: str) -> str:
    """The release entry: hand-written notes first, then commits they do not already mention, per section."""
    preamble, sections = _parse_sections(carried)
    for name, _, _ in GROUPS:
        sections.setdefault(name, [])
    for sha, subject in commits:
        if re.match(r"^release v?\d+\.\d+\.\d+", subject, re.I) or f"({sha})" in carried:
            continue
        section, text = classify(subject)
        body = sections[section]
        while body and not body[-1].strip():
            body.pop()
        body.append(f"- {text} ({sha})")
    lines = [f"## [{version}] - {date}", ""]
    if any(l.strip() for l in preamble):
        lines += ["\n".join(preamble).strip(), ""]
    for name, body in sections.items():
        text = "\n".join(body).strip()
        if text:
            lines += [f"### {name}", "", text, ""]
    if len(lines) == 2:
        lines += ["- No changes recorded.", ""]
    return "\n".join(lines)


def _unreleased_bounds(text: str) -> tuple[int, int]:
    if UNRELEASED not in text:
        raise SystemExit(f"{CHANGELOG} has no '{UNRELEASED}' heading")
    start = text.index(UNRELEASED) + len(UNRELEASED)
    nxt = text.find("\n## [", start)
    if nxt == -1:  # no released section yet: stop at the link references, if any
        refs = re.search(r"^\[[^\]]+\]:\s", text[start:], re.M)
        nxt = start + refs.start() - 1 if refs else len(text)
    return start, nxt


def unreleased_text(text: str) -> str:
    """Hand-written notes under `## [Unreleased]` (carried into the new release entry)."""
    if UNRELEASED not in text:
        return ""
    start, end = _unreleased_bounds(text)
    return text[start:end].strip()


def update_changelog(text: str, version: str, entry: str, prev_tag: str | None) -> str:
    start, end = _unreleased_bounds(text)
    body = text[:start] + "\n\n" + entry.rstrip() + "\n" + text[end:]
    tag = f"v{version}"
    link_unrel = f"[Unreleased]: {REPO_URL}/-/compare/{tag}...HEAD"
    link_new = (f"[{version}]: {REPO_URL}/-/compare/{prev_tag}...{tag}" if prev_tag
                else f"[{version}]: {REPO_URL}/-/tags/{tag}")
    if re.search(r"^\[Unreleased\]:.*$", body, re.M):
        body = re.sub(r"^\[Unreleased\]:.*$", lambda _m: link_unrel + "\n" + link_new, body, count=1, flags=re.M)
    else:
        body = body.rstrip() + "\n\n" + link_unrel + "\n" + link_new + "\n"
    return body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bump the version, update CHANGELOG.md, commit and tag (no push).")
    ap.add_argument("part", nargs="?", choices=("major", "minor", "patch"), default="patch")
    ap.add_argument("--version", help="Explicit new version X.Y.Z (overrides part).")
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="Release date (default today).")
    ap.add_argument("--dry-run", action="store_true", help="Print the CHANGELOG entry; change nothing.")
    ap.add_argument("--no-commit", action="store_true", help="Edit files only; no commit, no tag.")
    args = ap.parse_args(argv)

    old = current_version()
    new = args.version or bump(old, args.part)
    if not SEMVER.match(new):
        raise SystemExit(f"not a X.Y.Z version: {new}")
    tag = f"v{new}"
    exists = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], cwd=ROOT,
                            capture_output=True).returncode == 0
    if exists:
        raise SystemExit(f"tag {tag} already exists")

    prev = last_tag()
    commits = commits_since(prev)
    text = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else f"# Changelog\n\n{UNRELEASED}\n"
    entry = render_entry(new, args.date, commits, unreleased_text(text))
    print(f"{old} -> {new}  ({len(commits)} commits since {prev or 'the first commit'})\n")
    print(entry)
    if args.dry_run:
        return 0

    INIT.write_text(VERSION_RE.sub(f'__version__ = "{new}"', INIT.read_text(encoding="utf-8"), count=1),
                    encoding="utf-8")
    CHANGELOG.write_text(update_changelog(text, new, entry, prev), encoding="utf-8")
    print(f"updated {INIT.relative_to(ROOT)} and {CHANGELOG.relative_to(ROOT)}")
    if args.no_commit:
        print(f'next: review, commit, then `git tag -a {tag} -m "WORLDLINE {tag}"`')
        return 0

    paths = [INIT.relative_to(ROOT).as_posix(), CHANGELOG.relative_to(ROOT).as_posix()]
    git("add", "--", *paths)
    git("commit", "-m", f"Release {tag}", "--", *paths)
    git("tag", "-a", tag, "-m", f"WORLDLINE {tag}")
    print(f"committed and tagged {tag} (not pushed). Push with:\n  git push gitlab HEAD {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
