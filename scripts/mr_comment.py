"""Post WORLDLINE evidence into GitLab: one merge request note, or one tracking issue.

Standard library only, so the CI jobs need no `uv sync`. Reads the explore / kernel outputs in
`experiments/out` and talks to the REST API of the running GitLab instance.

    python scripts/mr_comment.py mr     --out experiments/out            # post or update the MR evidence note
    python scripts/mr_comment.py issue  --out experiments/out            # open or update the investigation issue
    python scripts/mr_comment.py mr     --out experiments/out --dry-run  # print the Markdown, call nothing

Idempotent. The MR note carries the marker MR_MARKER and is edited in place on every pipeline;
the investigation issue carries a marker keyed by invariant id and genome id and is updated, not
duplicated, when the same finding comes back.

Authentication, in order: WORLDLINE_BOT_TOKEN (project access token, role Reporter or higher,
scope `api`; sent as PRIVATE-TOKEN), then CI_JOB_TOKEN (sent as JOB-TOKEN). GitLab's job-token
allow-list does not include notes or issues, so without WORLDLINE_BOT_TOKEN the call is refused;
the script then prints the Markdown to the job log, keeps it as an artifact and exits 0.

The kernel decision is reported only when authorization.json was produced for this commit
(its artifact_digest equals CI_COMMIT_SHA); otherwise the note says the kernel did not run on
this ref. A stale or foreign decision is never shown as if it applied to this commit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MR_MARKER = "<!-- worldline:mr-evidence -->"
ISSUE_MARKER_PREFIX = "<!-- worldline:investigation"
INVESTIGATE_LABEL = "worldline:investigate"
INVESTIGATE_ACCOUNT_PREFIX = "ai-worldline-investigate-"
DEFAULT_CLAIM_CEILING = "EMPIRICALLY_VALIDATED on synthetic scenarios; ATTESTED only after kernel recomputation"
NO_KERNEL = "kernel did not run on this ref"

REPAIR_CONTRACT = [
    "The failing branch (same seed, same minimal schedule) now yields PASS on the **product copy** `scenarios/product/<file>`; the new trace digest is quoted",
    "The specimen under `scenarios/` is untouched and a test shows it still FAILs under the same schedule",
    "No invariant weakened: `invariants/`, `policies/`, `worldline/evidence/` untouched; no check in `worldline/invariants/` became more permissive",
    "Regression scenario `scenarios/regressions/<invariant>_<short>.yaml` with the concrete minimal schedule, plus a test asserting PASS",
    "`uv run pytest -q` passes; no test deleted, skipped or marked xfail",
    "Description states invariant id and version, witness before the fix, mechanism, change, and the claim ceiling (`EMPIRICALLY_VALIDATED`, never `ATTESTED`)",
    "No wall-clock time, no randomness outside `DeterministicRNG`, no LLM call in the decision path",
    "Non-vacuous: a variant or test shows the repaired rule still reaches its non-default option when the evidence supports it",
]


# --------------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------------

def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_evidence(out: Path) -> dict[str, Any]:
    """Every file is optional; a missing file is reported, never invented."""
    ev: dict[str, Any] = {name: _read(out / f"{name}.json") for name in
                          ("index", "minimal_schedule", "genome", "challenge", "authorization", "bundle")}
    ms = ev["minimal_schedule"] or {}
    files = ms.get("trace_files") or {}
    ev["root_verdicts"] = _read(out / "verdicts" / "root.json")
    ev["minimal_verdicts"] = _read(out / (files.get("minimal_verdicts") or "verdicts/minimal.json"))
    return ev


def kernel_decision(ev: dict[str, Any], commit_sha: str | None) -> dict[str, Any] | None:
    """The authorization, but only if it was issued for this commit."""
    auth = ev.get("authorization")
    if not isinstance(auth, dict) or "decision" not in auth:
        return None
    if commit_sha and auth.get("artifact_digest") != commit_sha:
        return None
    return auth


# --------------------------------------------------------------------------------------------
# Plain English
# --------------------------------------------------------------------------------------------

def _secs(ns: Any) -> str:
    try:
        v = int(ns) / 1e9
    except (TypeError, ValueError):
        return str(ns)
    return f"{v:g} s" if v == int(v) or abs(v) >= 1 else f"{int(ns)} ns"


def describe_intervention(iv: dict[str, Any]) -> str:
    """One sentence per intervention, e.g. 'Delay `correction-001` by 300 s.'"""
    op = iv.get("op", "?")
    t = iv.get("target") or {}
    p = iv.get("params") or {}
    ev = f"`{t['event_id']}`" if "event_id" in t else "the selected event"
    role = t.get("role") or p.get("role")
    to = f" for role `{role}`" if role else ""
    if op == "delay":
        s = f"Delay {ev}{to} by {_secs(p.get('by_ns'))}"
    elif op == "drop":
        s = f"Drop {ev}{to}" if role else f"Drop {ev} for every role"
    elif op == "duplicate":
        n = int(p.get("count", 1))
        s = f"Deliver {ev}{to} {n} extra time{'s' if n != 1 else ''}"
        if p.get("spacing_ns"):
            s += f", {_secs(p['spacing_ns'])} apart"
    elif op == "reorder":
        s = f"Reorder {ev}{to}: move it {_secs(p.get('by_ns'))} later, past the next event of its kind"
    elif op == "corrupt":
        s = f"Corrupt {ev}{to}: set `{p.get('field')}` to `{json.dumps(p.get('value'))}`"
    elif op == "revoke":
        s = f"Revoke {ev} from role `{role}` from {_secs(p.get('from_ns'))} on" if role else f"Revoke {ev}"
    elif op == "clock_skew":
        s = f"Skew the clock of source `{p.get('source_id')}` by {_secs(p.get('offset_ns'))}"
    else:
        s = f"{op} {ev}{to} {json.dumps(p, sort_keys=True)}"
    return s + "."


def describe_schedule(schedule: list[dict[str, Any]] | None) -> list[str]:
    return [describe_intervention(iv) for iv in schedule or []]


# --------------------------------------------------------------------------------------------
# Markdown pieces
# --------------------------------------------------------------------------------------------

def _code_list(ids: list[str] | None) -> str:
    return ", ".join(f"`{i}`" for i in ids) if ids else "none"


def verdict_table(root: list[dict] | None, minimal: list[dict] | None) -> list[str]:
    if not root and not minimal:
        return ["_No verdict files in this pipeline's explore output._"]
    r = {v["invariant_id"]: v for v in root or []}
    m = {v["invariant_id"]: v for v in minimal or []}
    lines = ["| Invariant | Root trace | Minimal trace | Witness (minimal) |", "|---|---|---|---|"]
    for inv in sorted(set(r) | set(m)):
        rv, mv = r.get(inv) or {}, m.get(inv) or {}
        ver = (mv or rv).get("invariant_version")
        name = f"`{inv}@{ver}`" if ver else f"`{inv}`"
        cell = lambda v: f"**{v['result']}**" if v.get("result") in ("FAIL", "UNKNOWN") else v.get("result", "n/a")  # noqa: E731
        wit = _code_list(mv.get("witness")) if mv.get("result") == "FAIL" else (
            "missing: " + ", ".join(mv.get("missing_data") or []) if mv.get("result") == "UNKNOWN" else "")
        lines.append(f"| {name} | {cell(rv)} | {cell(mv)} | {wit} |")
    return lines


def _challenge_line(ch: dict | None) -> str:
    if not ch:
        return "not run in this pipeline"
    p = ch.get("primary") or {}
    parts = [f"{'reproduced' if p.get('reproduced') else 'did not reproduce'} on `{p.get('scenario_id', '?')}`"]
    for c in ch.get("controls") or []:
        parts.append(f"{'reproduced' if c.get('reproduced') else 'did not reproduce'} on control `{c.get('scenario_id', '?')}`")
    verdict = "challenge passed" if ch.get("success") else "challenge did not pass"
    return f"{verdict}: " + "; ".join(parts)


def _claim_ceiling(ev: dict[str, Any]) -> str:
    return (ev.get("index") or {}).get("claim_ceiling") or DEFAULT_CLAIM_CEILING


def _kernel_lines(ev: dict[str, Any], commit_sha: str | None) -> list[str]:
    auth = kernel_decision(ev, commit_sha)
    if auth is None:
        return [f"**Kernel decision:** {NO_KERNEL}. `kernel-verify` and `authorize` need `WORLDLINE_KERNEL_KEY`, "
                "which only protected refs can read; the decision is made on the protected target branch after merge."]
    sat, miss = auth.get("satisfied") or [], auth.get("missing") or []
    lines = [f"**Kernel decision: {auth['decision']}** (policy `{auth.get('policy', '?')}`, {len(sat)} satisfying "
             f"record(s), {len(miss)} missing requirement(s), artifact `{auth.get('artifact_digest', '?')}`)"]
    for m in miss:
        lines.append(f"- missing: `{m.get('claim_pattern', m) if isinstance(m, dict) else m}`")
    for reason in (auth.get("reasons") or [])[:5]:
        lines.append(f"- {reason}")
    cp = (ev.get("bundle") or {}).get("checkpoint") or {}
    if cp.get("root"):
        lines.append(f"- Evidence bundle root `{cp['root']}`, signer `{cp.get('signer_key_id', '?')}`")
    return lines


def render_mr_note(ev: dict[str, Any], ctx: dict[str, str]) -> str:
    ms = ev.get("minimal_schedule") or {}
    g = ev.get("genome") or {}
    sha = ctx.get("commit_sha") or ""
    out = [MR_MARKER, "## WORLDLINE evidence for this merge request", ""]
    out += _kernel_lines(ev, sha or None)
    out += ["", "### Invariant verdicts", ""]
    out += verdict_table(ev.get("root_verdicts"), ev.get("minimal_verdicts"))
    out += ["", "### Minimal fault schedule", ""]
    if ms:
        target = ms.get("target_invariant", "?")
        out.append(f"Scenario `{str(ms.get('scenario_path', '?')).replace(chr(92), '/')}`, target `{target}`, "
                   f"{len(ms.get('original') or [])} -> {len(ms.get('minimal') or [])} intervention(s), "
                   f"1-minimal verified: {'yes' if ms.get('one_minimal_verified') else 'no'}.")
        out.append("")
        out += [f"1. {s}" for s in describe_schedule(ms.get("minimal"))] or ["_empty schedule_"]
        if ms.get("witness"):
            out += ["", f"Witness: {_code_list(ms['witness'])}"]
        if ms.get("explanation"):
            out.append(f"Why: {ms['explanation']}")
    else:
        out.append("_No minimal schedule in this pipeline's explore output._")
    out += ["", "### Failure genome", ""]
    if g:
        out.append(f"- `{g.get('id', '?')}` ({g.get('evidence_kind', '?')}): {g.get('mechanism_sentence', '')}")
    else:
        out.append("- not extracted in this pipeline")
    out.append(f"- Challenge: {_challenge_line(ev.get('challenge'))}")
    links = []
    if ctx.get("pipeline_url"):
        links.append(f"- Pipeline: {ctx['pipeline_url']}")
    if ctx.get("job_url"):
        links.append(f"- Evidence files: {ctx['job_url']}/artifacts/browse/experiments/out/")
    if sha:
        links.append(f"- Commit: `{sha}`")
    if links:
        out += ["", "### Links", ""] + links
    out += ["", f"Claim ceiling: {_claim_ceiling(ev)}. Agents report; the kernel decides.", ""]
    return "\n".join(out)


def issue_marker(ev: dict[str, Any]) -> str:
    ms, g = ev.get("minimal_schedule") or {}, ev.get("genome") or {}
    inv = ms.get("target_invariant") or g.get("violated_invariant") or "unknown"
    return f"{ISSUE_MARKER_PREFIX} invariant={inv} genome={g.get('id', 'none')} -->"


def render_issue(ev: dict[str, Any], ctx: dict[str, str]) -> tuple[str, str]:
    ms, g, ch = ev.get("minimal_schedule") or {}, ev.get("genome") or {}, ev.get("challenge") or {}
    inv = ms.get("target_invariant") or g.get("violated_invariant") or "unknown"
    gid = g.get("id", "no genome")
    scenario = str(ms.get("scenario_path") or ctx.get("scenario") or "scenarios/port_alert.yaml").replace("\\", "/")
    diff = ((ms.get("causal_diff") or {}).get("decisions") or [{}])[0]
    actor = diff.get("actor")
    title = f"Investigate: {inv} FAIL on {ms.get('scenario_id', 'scenario')} ({gid})"
    if actor and diff.get("option_b") and diff.get("option_a"):
        headline = f"`{actor}` chose `{diff['option_b']}` where truth gives `{diff['option_a']}`"
    else:
        headline = f"`{inv}` fails under a minimal fault schedule"
    b = [issue_marker(ev), f"## Counterfactual finding: {headline}", "",
         f"WORLDLINE explored alternative worlds of `{scenario}` and found a world in which invariant **`{inv}` FAILS**.", "",
         "**Minimal intervention (1-minimal" + (", independently verified" if ms.get("one_minimal_verified") else "") + "):**",
         "```json", json.dumps(ms.get("minimal") or [], indent=2), "```"]
    b += [f"- {s}" for s in describe_schedule(ms.get("minimal"))]
    b += ["", f"**Witness:** {_code_list(ms.get('witness'))}", "",
          f"**Explanation:** {ms.get('explanation', 'n/a')}", "",
          f"**Failure genome `{gid}`:** {g.get('mechanism_sentence', 'not extracted')}", "",
          f"**Transfer:** {_challenge_line(ch)}.", "",
          "### Verdicts", ""]
    b += verdict_table(ev.get("root_verdicts"), ev.get("minimal_verdicts"))
    seed, budget = ctx.get("seed", "42"), ctx.get("budget", "200")
    ch_scen = ctx.get("challenge_scenario", "scenarios/convoy_lostlink.yaml")
    b += ["", "### Reproduce", "", "```",
          "uv sync --extra dev",
          f"uv run worldline explore {scenario} --budget {budget} --seed {seed} --out experiments/out",
          "uv run worldline minimize experiments/out/exploration.json --out experiments/out",
          "uv run worldline genome extract experiments/out/minimal_schedule.json --out experiments/out/genome.json",
          f"uv run worldline genome challenge experiments/out/genome.json {ch_scen} --seed {seed} --out experiments/out/challenge.json",
          "```", f"Expected minimal trace digest: `{ms.get('trace_digest', 'n/a')}`", "",
          "### Repair contract (AGENTS.md)", ""]
    b += [f"- [ ] {item}" for item in REPAIR_CONTRACT]
    b += ["", "The kernel (`kernel-verify`, `authorize`) decides whether the repair is sufficient. Do not self-certify.", ""]
    src = []
    if ctx.get("pipeline_url"):
        src.append(f"pipeline {ctx['pipeline_url']}")
    if ctx.get("commit_sha"):
        src.append(f"commit `{ctx['commit_sha']}`")
    if src:
        b.append("Source: " + ", ".join(src) + ".")
    b += [f"Claim ceiling: {_claim_ceiling(ev)}.", ""]
    return title, "\n".join(b)


def investigate_request(account: str | None, scenario: str, seed: str, budget: str) -> str:
    who = f"@{account}" if account else "the WORLDLINE Investigate flow (mention its service account)"
    return (f"{who} investigate {scenario} seed {seed} budget {budget}: confirm determinism, the witness and the "
            "genome above, and post one Investigation comment. Do not edit files.")


# --------------------------------------------------------------------------------------------
# GitLab REST
# --------------------------------------------------------------------------------------------

class GitLab:
    def __init__(self, api: str, project: str, token: str, header: str):
        self.api, self.project, self.token, self.header = api.rstrip("/"), project, token, header

    @classmethod
    def from_env(cls) -> "GitLab":
        api = os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")
        project = os.environ.get("CI_PROJECT_ID", "")
        if os.environ.get("WORLDLINE_BOT_TOKEN"):
            return cls(api, project, os.environ["WORLDLINE_BOT_TOKEN"], "PRIVATE-TOKEN")
        if os.environ.get("CI_JOB_TOKEN"):
            return cls(api, project, os.environ["CI_JOB_TOKEN"], "JOB-TOKEN")
        raise SystemExit("no token: set WORLDLINE_BOT_TOKEN (project access token, scope api) or run in CI")

    def call(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> Any:
        url = f"{self.api}/projects/{urllib.parse.quote(str(self.project), safe='')}/{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={self.header: self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def paged(self, path: str, query: dict) -> list[dict]:
        out, page = [], 1
        while True:
            batch = self.call("GET", path, query={**query, "per_page": 100, "page": page}) or []
            out += batch
            if len(batch) < 100:
                return out
            page += 1


def upsert_mr_note(gl: GitLab, mr_iid: str, body: str) -> str:
    notes = gl.paged(f"merge_requests/{mr_iid}/notes", {"sort": "asc", "order_by": "created_at"})
    for n in notes:
        if MR_MARKER in (n.get("body") or ""):
            gl.call("PUT", f"merge_requests/{mr_iid}/notes/{n['id']}", {"body": body})
            return f"updated note {n['id']}"
    n = gl.call("POST", f"merge_requests/{mr_iid}/notes", {"body": body})
    return f"created note {n['id']}"


def find_investigate_account(gl: GitLab) -> str | None:
    if os.environ.get("WORLDLINE_INVESTIGATE_ACCOUNT"):
        return os.environ["WORLDLINE_INVESTIGATE_ACCOUNT"].lstrip("@")
    try:
        members = gl.call("GET", "members/all", query={"query": INVESTIGATE_ACCOUNT_PREFIX, "per_page": 20}) or []
    except urllib.error.HTTPError:
        return None
    names = sorted(m["username"] for m in members if m.get("username", "").startswith(INVESTIGATE_ACCOUNT_PREFIX))
    return names[0] if names else None


def upsert_issue(gl: GitLab, ev: dict[str, Any], ctx: dict[str, str]) -> str:
    title, body = render_issue(ev, ctx)
    marker = issue_marker(ev)
    existing = [i for i in gl.paged("issues", {"labels": INVESTIGATE_LABEL, "state": "opened"})
                if marker in (i.get("description") or "")]
    if existing:
        iid = existing[0]["iid"]
        gl.call("PUT", f"issues/{iid}", {"title": title, "description": body})
        note = f"Evidence refreshed by {ctx.get('pipeline_url', 'a pipeline')}; same invariant and genome."
        if os.environ.get("WORLDLINE_RETRIGGER") == "1":
            acct = find_investigate_account(gl)
            note += "\n\n" + investigate_request(acct, _scenario(ev, ctx), ctx.get("seed", "42"), ctx.get("budget", "200"))
        gl.call("POST", f"issues/{iid}/notes", {"body": note})
        return f"updated issue #{iid}"
    issue = gl.call("POST", "issues", {"title": title, "description": body, "labels": INVESTIGATE_LABEL})
    acct = find_investigate_account(gl)
    gl.call("POST", f"issues/{issue['iid']}/notes",
            {"body": investigate_request(acct, _scenario(ev, ctx), ctx.get("seed", "42"), ctx.get("budget", "200"))})
    return f"created issue #{issue['iid']} {issue.get('web_url', '')} (Investigator: {acct or 'not found'})"


def _scenario(ev: dict[str, Any], ctx: dict[str, str]) -> str:
    ms = ev.get("minimal_schedule") or {}
    return str(ms.get("scenario_path") or ctx.get("scenario") or "scenarios/port_alert.yaml").replace("\\", "/")


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------

def ci_context() -> dict[str, str]:
    env = os.environ
    return {k: v for k, v in {
        "commit_sha": env.get("CI_COMMIT_SHA", ""),
        "pipeline_url": env.get("CI_PIPELINE_URL", ""),
        "job_url": env.get("CI_JOB_URL", ""),
        "seed": env.get("WORLDLINE_SEED", "42"),
        "budget": env.get("WORLDLINE_BUDGET", "200"),
        "scenario": env.get("WORLDLINE_SCENARIO", ""),
        "challenge_scenario": env.get("WORLDLINE_CHALLENGE_SCENARIO", "scenarios/convoy_lostlink.yaml"),
    }.items() if v}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["mr", "issue"])
    ap.add_argument("--out", type=Path, default=Path("experiments/out"))
    ap.add_argument("--dry-run", action="store_true", help="print the Markdown and call nothing")
    ap.add_argument("--save", type=Path, help="also write the Markdown to this file")
    args = ap.parse_args(argv)

    ev = load_evidence(args.out)
    ctx = ci_context()
    if args.mode == "mr":
        text = render_mr_note(ev, ctx)
    else:
        title, text = render_issue(ev, ctx)
        text = f"# {title}\n\n{text}"
    if args.save:
        args.save.write_text(text, encoding="utf-8")
    if args.dry_run:
        sys.stdout.write(text)
        return 0
    if args.mode == "issue" and not ev.get("minimal_schedule"):
        print("no minimal_schedule.json in the explore output: nothing to investigate")
        return 0

    try:
        gl = GitLab.from_env()
        if args.mode == "mr":
            iid = os.environ.get("CI_MERGE_REQUEST_IID")
            if not iid:
                print("not a merge request pipeline (CI_MERGE_REQUEST_IID unset); note not posted")
                sys.stdout.write(text)
                return 0
            print(upsert_mr_note(gl, iid, text))
        else:
            print(upsert_issue(gl, ev, ctx))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        print(f"GitLab API refused the call: HTTP {e.code} {detail}", file=sys.stderr)
        if gl.header == "JOB-TOKEN":
            print("CI_JOB_TOKEN cannot write notes or issues. Add a project access token as the masked, protected "
                  "CI variable WORLDLINE_BOT_TOKEN (role Reporter, scope api). See docs/WORKFLOW.md.", file=sys.stderr)
        sys.stdout.write("\n" + text)
        return 0 if args.mode == "mr" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
