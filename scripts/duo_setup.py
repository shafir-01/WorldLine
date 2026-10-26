"""Create or update the WORLDLINE Duo Agent Platform catalog items from the repo's prompt files.

Idempotent: looks up existing items by name in the project before creating. Uses `glab api graphql`
so the user's own GitLab login is used; no token is stored here.

    uv run python scripts/duo_setup.py --project-id 88121109 [--dry-run]

Writes docs/duo/catalog_ids.json with the ids it created or found.
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


def enum(name: str) -> str:
    return name


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", type=int, required=True)
    ap.add_argument("--project-path", default="shafir2/WorldLine")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--visibility", default="PUBLIC", choices=["PRIVATE", "RESTRICTED", "PUBLIC"])
    args = ap.parse_args()
    pgid = f"gid://gitlab/Project/{args.project_id}"

    tools = tool_ids()
    have = existing_items(args.project_path)
    ids: dict[str, dict] = {"project_id": args.project_id, "agents": {}, "flows": {}}

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
            print(f"flow exists: {name} -> {have[name]['id']}")
            ids["flows"][name] = have[name]
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

    if not args.dry_run:
        out = DUO / "catalog_ids.json"
        out.write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
