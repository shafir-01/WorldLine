"""Create or update the WORLDLINE Duo Agent Platform catalog items from the repo's prompt files.

Idempotent: looks up existing items by name in the project before creating. Uses `glab api graphql`
so the user's own GitLab login is used; no token is stored here.

    uv run python scripts/duo_setup.py --project-id 88121109            # create missing agents and flows
    uv run python scripts/duo_setup.py --project-id 88121109 --update   # also push docs/duo/flows.md as a new
                                                                        # released PATCH version of each flow
    uv run python scripts/duo_setup.py --project-id 88121109 --goals    # also set per-event trigger Instructions
                                                                        # (needs feature flag ai_flow_trigger_goals)
    uv run python scripts/duo_setup.py --project-id 88121109 --dry-run

Writes docs/duo/catalog_ids.json with the ids it created or found: items, latest versions, the
project consumers and their flow triggers (event types, goals).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "docs" / "duo"
GLAB = shutil.which("glab") or r"C:\Users\shafe\AppData\Local\Programs\glab\glab.exe"

AGENTS = {
    "WORLDLINE Investigator": {
        "file": "investigator.md",
        "description": "Read-only: explores a WORLDLINE scenario under fault interventions, minimises the failing schedule, extracts the failure genome and posts the witness on the issue.",
        "tools": ["Read File", "Read Files", "List Dir", "Find Files", "Grep", "Get Repository File", "List Repository Tree",
                  "Get Issue", "Get Work Item", "Create Issue Note", "Create Work Item Note", "Run Command", "Get Project"],
    },
    "WORLDLINE Repairer": {
        "file": "repairer.md",
        "description": "Proposes a repair as a merge request under the AGENTS.md repair contract. Never touches invariants/, policies/, worldline/evidence/ or kernel CI jobs; never self-certifies.",
        "tools": ["Read File", "Read Files", "List Dir", "Find Files", "Grep", "Edit File", "Create File With Contents",
                  "Create Branch", "Create Commit", "Create Merge Request", "Run Command", "Run Tests", "Get Issue",
                  "Create Issue Note", "Get Repository File", "List Repository Tree", "Run Git Command"],
    },
    "WORLDLINE Skeptic": {
        "file": "skeptic.md",
        "description": "Reviews merge requests for claims not backed by typed evidence; checks that protected paths were not touched and that the kernel, not the author, decides.",
        "tools": ["Get Merge Request", "List Merge Request Diffs", "Build Review Merge Request Context", "Create Merge Request Note",
                  "Create Merge Request Diff Note", "Submit MR Review", "Read File", "Get Repository File", "Grep", "Run Command",
                  "Get Pipeline Errors", "Get Pipeline Failing Jobs", "Get Job Logs"],
    },
}

FLOWS = [
    ("WORLDLINE Investigate", "Explores a scenario under fault interventions, minimises, extracts the genome, posts the witness. Read-only."),
    ("WORLDLINE Repair", "Repairs a witnessed failure as a merge request under the AGENTS.md repair contract; the kernel decides."),
    ("WORLDLINE Skeptic Review", "Reviews a merge request for unbacked claims and protected-path edits."),
]

# Ai::FlowTrigger::EVENT_TYPES (integer ids used by eventTypes; names are the keys of `goals`).
EVENT_TYPES = {
    "mention": 0, "assign": 1, "assign_reviewer": 2, "pipeline_hooks": 3, "merge_request_ready": 4,
    "merge_request_code_conflict": 5, "merge_request": 6, "work_item": 7, "commit_to_default_branch": 8, "scheduled": 9,
}
EVENT_NAMES = {v: k for k, v in EVENT_TYPES.items()}

# Per-event trigger Instructions (<= 4096 chars). Prepended by the runtime to the event's own goal
# (which, with the gitlab.com flags as of 19.x, is the bare IID of the merge request or work item).
TRIGGER_GOALS = {
    "WORLDLINE Investigate": {
        "work_item": "Investigate the work item whose IID follows as the WORLDLINE Investigator: read it with get_issue, run the exploration on the scenario it names (default scenarios/port_alert.yaml, seed 42, budget 200), minimise, extract the genome and post one Investigation comment. Do not edit files.",
        "mention": "Run the WORLDLINE exploration as the Investigator on the scenario named in the comment (default scenarios/port_alert.yaml, seed 42, budget 200), minimise, extract the genome and post one Investigation comment on the issue. Do not edit files.",
    },
    "WORLDLINE Repair": {
        "mention": "Repair the FAIL described in this issue under the AGENTS.md repair contract: make the failing branch PASS, add a regression scenario and test, open one merge request against the default branch. Never modify invariants/, policies/, worldline/evidence/ or kernel CI jobs.",
    },
    "WORLDLINE Skeptic Review": {
        "merge_request": "Review the merge request whose IID follows as the WORLDLINE Skeptic: fetch it with get_merge_request, check boundaries, weakening, reproduced claim, regression scenario, determinism and honesty against AGENTS.md, and post exactly one note with a PASS or FAIL verdict. Do not approve or merge.",
    },
}


def gql(query: str, variables: dict | None = None) -> dict:
    """POST a GraphQL body through glab via a temp file (command-line quoting on Windows mangles long queries)."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"query": query, "variables": variables or {}}, fh)
        body = fh.name
    try:
        out = subprocess.run([GLAB, "api", "graphql", "--input", body], capture_output=True, text=True, encoding="utf-8")
    finally:
        Path(body).unlink(missing_ok=True)
    text = out.stdout.strip() or out.stderr.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        sys.exit(f"glab returned non-JSON:\n{text[:800]}")
    if "errors" in data and data["errors"]:
        print("GraphQL errors:", json.dumps(data["errors"], indent=1)[:1200], file=sys.stderr)
    return data


def system_prompt(md: Path) -> str:
    text = md.read_text(encoding="utf-8")
    m = re.search(r"## System prompt\s*```[a-z]*\n(.*?)\n```", text, re.S)
    if not m:
        sys.exit(f"no system prompt block in {md}")
    return m.group(1).strip()


def flow_definitions(md: Path) -> dict[str, str]:
    text = md.read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"## Flow \d+: `([^`]+)`.*?```yaml\n(.*?)\n```", text, re.S):
        out[m.group(1)] = m.group(2)
    return out


def tool_ids() -> dict[str, str]:
    d = gql("{ aiCatalogBuiltInTools { nodes { id title } } }")
    return {n["title"]: n["id"] for n in d["data"]["aiCatalogBuiltInTools"]["nodes"]}


def existing_items(project_path: str) -> dict[str, dict]:
    d = gql("""query($s: String!) { aiCatalogItems(search: $s, first: 50) { nodes { id name itemType latestVersion { id } project { fullPath } } } }""",
            {"s": "WORLDLINE"})
    nodes = (d.get("data") or {}).get("aiCatalogItems", {}).get("nodes") or []
    return {n["name"]: n for n in nodes if (n.get("project") or {}).get("fullPath") == project_path}


def current_definition(item_id: str) -> str | None:
    """YAML of the flow's latest version (a released version cannot be edited; an identical one is not re-pushed)."""
    d = gql("""query($id: AiCatalogItemID!) { aiCatalogItem(id: $id) {
                 latestVersion { ... on AiCatalogFlowVersion { definition } } } }""", {"id": item_id})
    v = (((d.get("data") or {}).get("aiCatalogItem") or {}).get("latestVersion") or {})
    text = v.get("definition")
    return text.strip() if isinstance(text, str) else None


def consumers(project_path: str, items: dict[str, dict]) -> dict[str, dict]:
    """The project's consumer of each item, with its flow triggers (event types and goals)."""
    out = {}
    for name, item in items.items():
        d = gql("""query($p: ID!, $i: AiCatalogItemID!) { project(fullPath: $p) { aiCatalogItemConsumerForItem(id: $i) {
                     id enabled webPath serviceAccount { username }
                     flowTriggers { id eventTypes active description goals } } } }""", {"p": project_path, "i": item["id"]})
        c = ((d.get("data") or {}).get("project") or {}).get("aiCatalogItemConsumerForItem")
        if not c:
            print(f"consumer missing: {name} is not enabled on {project_path}")
            continue
        for t in c.get("flowTriggers") or []:
            t["eventTypeNames"] = [EVENT_NAMES.get(e, str(e)) for e in t.get("eventTypes") or []]
        out[name] = c
    return out


def set_trigger_goals(name: str, consumer: dict, dry_run: bool) -> None:
    wanted = TRIGGER_GOALS.get(name) or {}
    for trig in consumer.get("flowTriggers") or []:
        names = trig.get("eventTypeNames") or []
        goals = {k: v for k, v in wanted.items() if k in names}
        if not goals:
            continue
        if trig.get("goals") == goals:
            print(f"  trigger {trig['id']}: goals already set")
            continue
        if dry_run:
            print(f"  would set goals on {trig['id']} for {sorted(goals)}")
            continue
        d = gql("""mutation($in: AiFlowTriggerUpdateInput!) { aiFlowTriggerUpdate(input: $in) {
                     errors aiFlowTrigger { id eventTypes goals } } }""",
                {"in": {"id": trig["id"], "eventTypes": trig["eventTypes"], "goals": goals}})
        res = (d.get("data") or {}).get("aiFlowTriggerUpdate") or {}
        if res.get("errors"):
            # "Goals are not available" = feature flag ai_flow_trigger_goals is off for this namespace.
            print(f"  trigger {trig['id']}: {res['errors']} (prompts in flows.md carry the rule instead)")
        else:
            print(f"  trigger {trig['id']}: goals set for {sorted(goals)}")
            trig["goals"] = goals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", type=int, required=True)
    ap.add_argument("--project-path", default="shafir2/WorldLine")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--update", action="store_true", help="push the YAML in docs/duo/flows.md to existing flows as a released PATCH version")
    ap.add_argument("--goals", action="store_true", help="set per-event trigger Instructions from TRIGGER_GOALS")
    ap.add_argument("--visibility", default="PUBLIC", choices=["PRIVATE", "RESTRICTED", "PUBLIC"])
    args = ap.parse_args()
    pgid = f"gid://gitlab/Project/{args.project_id}"

    tools = tool_ids()
    have = existing_items(args.project_path)
    ids: dict[str, dict] = {"project_id": args.project_id, "agents": {}, "flows": {}, "consumers": {}}

    for name, spec in AGENTS.items():
        prompt = system_prompt(DUO / spec["file"])
        tids = []
        for t in spec["tools"]:
            if t not in tools:
                print(f"  warning: tool {t!r} not in catalog, skipped", file=sys.stderr)
            else:
                tids.append(tools[t])
        if name in have:
            print(f"agent exists: {name} -> {have[name]['id']}")
            ids["agents"][name] = have[name]
            continue
        if args.dry_run:
            print(f"would create agent {name} with {len(tids)} tools, prompt {len(prompt)} chars")
            continue
        d = gql("""mutation($in: AiCatalogAgentCreateInput!) { aiCatalogAgentCreate(input: $in) {
                     errors item { id name latestVersion { id } webPath } } }""",
                {"in": {"projectId": pgid, "name": name, "description": spec["description"], "systemPrompt": prompt,
                        "tools": tids, "visibility": args.visibility, "release": True}})
        res = d["data"]["aiCatalogAgentCreate"]
        if res["errors"]:
            print(f"agent {name}: {res['errors']}")
        else:
            print(f"created agent {name} -> {res['item']['id']}")
            ids["agents"][name] = res["item"]

    defs = flow_definitions(DUO / "flows.md")
    for name, desc in FLOWS:
        if name not in defs:
            print(f"  warning: no YAML for flow {name}", file=sys.stderr)
            continue
        if name in have:
            ids["flows"][name] = have[name]
            if not args.update:
                print(f"flow exists: {name} -> {have[name]['id']} (use --update to push the YAML)")
                continue
            if current_definition(have[name]["id"]) == defs[name]:
                print(f"flow unchanged: {name} -> {have[name]['id']} (definition equals the released version)")
                continue
            if args.dry_run:
                print(f"would update flow {name} ({have[name]['id']}), definition {len(defs[name])} chars, PATCH release")
                continue
            d = gql("""mutation($in: AiCatalogFlowUpdateInput!) { aiCatalogFlowUpdate(input: $in) {
                         errors item { id name latestVersion { id versionName } webPath } } }""",
                    {"in": {"id": have[name]["id"], "description": desc, "definition": defs[name],
                            "versionBump": "PATCH", "release": True}})
            res = (d.get("data") or {}).get("aiCatalogFlowUpdate") or {"errors": ["no payload"]}
            if res.get("errors"):
                print(f"flow {name}: {res['errors']}")
            else:
                print(f"updated flow {name} -> version {res['item']['latestVersion']}")
                ids["flows"][name] = res["item"]
            continue
        if args.dry_run:
            print(f"would create flow {name}, definition {len(defs[name])} chars")
            continue
        d = gql("""mutation($in: AiCatalogFlowCreateInput!) { aiCatalogFlowCreate(input: $in) {
                     errors item { id name latestVersion { id } webPath } } }""",
                {"in": {"projectId": pgid, "name": name, "description": desc, "definition": defs[name],
                        "visibility": args.visibility, "release": True}})
        res = d["data"]["aiCatalogFlowCreate"]
        if res["errors"]:
            print(f"flow {name}: {res['errors']}")
        else:
            print(f"created flow {name} -> {res['item']['id']}")
            ids["flows"][name] = res["item"]

    cons = consumers(args.project_path, {**ids["agents"], **ids["flows"]})
    for name, c in cons.items():
        trig = ", ".join(f"{t['id'].rsplit('/', 1)[-1]}:{'+'.join(t['eventTypeNames'])}" for t in c.get("flowTriggers") or [])
        print(f"consumer {name} -> {c['id']} enabled={c['enabled']} triggers=[{trig}]")
        if args.goals and name in TRIGGER_GOALS:
            set_trigger_goals(name, c, args.dry_run)
    ids["consumers"] = cons

    if not args.dry_run:
        out = DUO / "catalog_ids.json"
        out.write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
