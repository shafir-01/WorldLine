# WORLDLINE custom flows

Custom flows are flow registry v1 YAML (GA 19.2). The blocks in this file are published to the AI Catalog by `scripts/duo_setup.py` (see "Publishing the definitions" at the end); the UI editor at **AI > Flows** shows the same text. Docs:

- Custom flows: https://docs.gitlab.com/user/duo_agent_platform/flows/custom/
- Schema notes: https://docs.gitlab.com/user/duo_agent_platform/flows/custom_flows_schema/
- Full flow registry v1 spec (authoritative): https://gitlab.com/gitlab-org/modelops/applied-ml/code-suggestions/ai-assist/-/blob/main/docs/flow_registry/v1.md
- Triggers: https://docs.gitlab.com/user/duo_agent_platform/triggers/
- Execution (`.gitlab/duo/agent-config.yml`): https://docs.gitlab.com/user/duo_agent_platform/flows/execution/

Facts that shaped these definitions (CONFIRMED from the docs above):

- A custom flow cannot reference a custom agent from the catalog; it declares its own `AgentComponent`s with inline `prompts`. The agent prompts below are therefore copies of `investigator.md`, `repairer.md`, `skeptic.md`, trimmed to fit.
- `version: v1` and `environment: ambient` (no human in the loop) are required. `name`, `description`, `product_group` are not allowed in custom flows; the UI form carries name and description.
- Inputs default to `["context:goal"]`. `context:goal` is the only trigger-supplied input the v1 registry exposes to components; there is no `context:merge_request_iid` or similar. What the goal contains depends on the trigger (CONFIRMED from the job log of trigger-started session 10081751, `$DUO_WORKFLOW_GOAL`): for Mention events the comment text; for **Merge request / Work item events the bare IID of the resource** (`1`), because the "resource type and URL instead of the IID" format and the per-event trigger Instructions (`goals`) are both behind feature flags that are off on gitlab.com (`aiFlowTriggerUpdate` with `goals` answers `Goals are not available`). The runtime also attaches an `agent_platform_resource_context` block (resource type, id, web URL, pipeline, source branch) to the agent's additional context. **Any flow that is started by an event trigger must therefore treat a numeric goal as the IID of the triggering resource**; the flows below do.
- Session 10081751 (Skeptic, trigger Merge request > Created) ended `no_output`: the Duo Workflow Service closed the stream 250 ms after `startRequest` with the goal `1` and the original prompt, without a single checkpoint; the same definition started over REST with a sentence-long goal ran to completion (session 10081961). The fix is in the prompts: the user turn now carries the resolution rule and the project id, so the model always has a concrete task.
- Tool names come from `duo_workflow_service/components/tools_registry.py`; the ones used here are listed in the spec: `read_file`, `list_dir`, `find_files`, `get_repository_file`, `list_repository_tree`, `get_issue`, `create_issue_note`, `get_merge_request`, `create_merge_request_note`, `create_file_with_contents`, `edit_file`, `run_command`, `create_merge_request`. UNVERIFIED: exact names for git commit/push (`run_git_command` is in the registry per the spec's examples; the editor will reject unknown names, fix and record here).
- There is no label trigger. Trigger events: Mention, Assign, Assign reviewer, Pipeline events (experiment), Merge request (Approved / Created / Marked ready / Merge conflict), Work item (Created / Status changed with type/status filters). Labels are a human convention here; the firing event is a status change or a mention.
- Flows run in CI on the `gitlab--duo` hosted runners, inside the image from `.gitlab/duo/agent-config.yml` (python:3.12 + Node 20 + uv) with `setup_script` already executed.
- Only humans can fire a trigger; a flow cannot mention another flow's service account to chain. Chaining via work-item status (19.6) is behind flags, so the Repairer requests the Skeptic by opening an MR, and the Skeptic flow fires on **Merge request > Created**.

UNVERIFIED parts are marked inline. Validate each YAML in the flow editor and amend this file with whatever the schema rejected.

---

## Flow 1: `WORLDLINE Investigate`

- **Display name:** WORLDLINE Investigate
- **Description:** Explores a scenario under fault interventions, minimises the failing schedule, extracts the failure genome and posts the witness on the issue. Read-only.
- **Visibility:** Public
- **Triggers to add on enable:** Work item > Status changed, status `Investigate` (if custom statuses are available; UNVERIFIED on the hackathon group); Mention (fallback: comment `@ai-worldline-investigate-<group> scenarios/port_alert.yaml`).
- **Trigger instructions (<= 4096 chars):** "Run the WORLDLINE exploration on the scenario named in the issue (default scenarios/port_alert.yaml, seed 42, budget 200), minimise, extract the genome and post the Investigation report as one issue comment. Do not edit files."
- **Human convention:** issues also carry label `worldline:investigate` so the board filters them; the label itself does not fire anything.
- **Slash command in Chat:** `/flow:worldline-investigate scenarios/port_alert.yaml`

```yaml
version: v1
environment: ambient

components:
  - name: investigator
    type: AgentComponent
    prompt_id: worldline_investigator
    inputs:
      - from: "context:goal"
        as: goal
      - from: "context:project_id"
        as: project_id
    toolset:
      - read_file
      - list_dir
      - find_files
      - get_repository_file
      - list_repository_tree
      - get_issue
      - create_issue_note
      - run_command
    ui_log_events:
      - on_tool_execution_success
      - on_tool_execution_failed
      - on_agent_final_answer

prompts:
  - prompt_id: worldline_investigator
    name: WORLDLINE Investigator
    unit_primitives: []
    prompt_template:
      system: |
        You are the WORLDLINE Investigator, a read-only analysis agent for the WORLDLINE repository
        (deterministic counterfactual-execution substrate; read AGENTS.md and docs/ARCHITECTURE.md first).
        You never edit files, never open merge requests, never run `worldline verify` or `worldline authorize`.
        Procedure: (1) resolve the goal. If the goal is only a number, it is the IID of the work item (issue) in the
        current project that fired the trigger: call get_issue with that IID and treat its title and description as
        the request. If the goal is a URL, it is that work item. Otherwise it is free text. Then parse scenario path
        (default scenarios/port_alert.yaml), seed (42), budget (200), issue IID and target invariant.
        (2) run_command: `uv sync --extra dev`; then
        `uv run worldline explore <scenario> --budget <b> --seed <s> --out experiments/out`;
        `uv run worldline minimize experiments/out/exploration.json --out experiments/out`;
        `uv run worldline genome extract experiments/out/minimal_schedule.json --out experiments/out/genome.json`.
        Run explore twice; trace digests must be identical, otherwise report NON-DETERMINISM first.
        If the CLI is missing a flag, run `uv run worldline --help`, adapt, and quote the exact commands you ran.
        (3) For each FAIL report invariant id and version, witness event ids verbatim, minimal FaultSchedule,
        trace digest, branch id. For `decision` invariants list the truth events missing from the actor's view.
        (4) Report the FailureGenome: abstract mechanism, preconditions, origin, evidence_kind EMPIRICALLY_VALIDATED
        on the origin only. (5) List UNKNOWNs with their missing signal. (6) Quote random-vs-guided numbers from
        exploration.json only if present.
        Post ONE comment on the issue (create_issue_note) using the format:
        "## Investigation: <scenario> seed <s> budget <b>" then Determinism, Branches explored/failing,
        one "### FAIL <invariant>" block per failure (Witness, Minimal schedule, Mechanism, Preconditions,
        Trace digest, Evidence), "### UNKNOWN" blocks, and "### Recommended repair target" naming one invariant.
        Quote digests and witnesses exactly. No emojis. If a command fails, paste the error and stop.
        Genome transfer is within the shared Event v1 substrate, never across codebases.
      user: |
        Project id: {{project_id}}
        Goal: {{goal}}
        If the goal above is only a number, it is the IID of the work item in project {{project_id}} that
        started this run: read it with get_issue first, then investigate what it describes. If it is a URL,
        it is that work item. Otherwise it is the full request.
    params:
      timeout: 1800

routers:
  - from: investigator
    to: end

flow:
  entry_point: investigator
```

---

## Flow 2: `WORLDLINE Repair`

- **Display name:** WORLDLINE Repair
- **Description:** Takes a FAIL finding and opens a merge request that makes the failing branch PASS under the AGENTS.md repair contract, with a regression scenario. Cannot touch invariants/, policies/, worldline/evidence/ or kernel CI jobs.
- **Visibility:** Public
- **Triggers:** Work item > Status changed, status `Repair`; Mention (`@ai-worldline-repair-<group>`); also started from CI by the manual `duo-repair` job via the Flows API with this flow's consumer id.
- **Trigger instructions:** "Repair the FAIL described in this issue (or in experiments/out/authorization.json of the linked pipeline). Follow the AGENTS.md repair contract exactly, add a regression scenario and test, and open one merge request against the default branch. Never modify protected paths."
- **Slash command:** `/flow:worldline-repair <issue url>`

Two components: the repairer agent, then a deterministic guard that re-runs the test suite before the MR is announced. UNVERIFIED: whether `DeterministicStepComponent` with `tool_name: run_command` accepts a literal `command` input exactly as written below; if the editor rejects it, delete the `verify_tests` component and the second router, and route `repairer -> end`.

```yaml
version: v1
environment: ambient

components:
  - name: repairer
    type: AgentComponent
    prompt_id: worldline_repairer
    inputs:
      - from: "context:goal"
        as: goal
      - from: "context:project_id"
        as: project_id
    toolset:
      - read_file
      - list_dir
      - find_files
      - get_repository_file
      - list_repository_tree
      - get_issue
      - create_issue_note
      - create_file_with_contents
      - edit_file
      - run_command
      - run_git_command            # UNVERIFIED name; needed for branch/commit/push
      - create_merge_request
      - create_merge_request_note
    ui_log_events:
      - on_tool_execution_success
      - on_tool_execution_failed
      - on_agent_final_answer

  - name: verify_tests
    type: DeterministicStepComponent
    tool_name: run_command
    inputs:
      - from: "uv run pytest -q"
        as: command
        literal: true
    ui_log_events:
      - on_tool_execution_success
      - on_tool_execution_failed

prompts:
  - prompt_id: worldline_repairer
    name: WORLDLINE Repairer
    unit_primitives: []
    prompt_template:
      system: |
        You are the WORLDLINE Repairer. Produce exactly one merge request that repairs the FAIL described in the goal,
        under the repair contract in AGENTS.md (read it and docs/ARCHITECTURE.md first; keep every documented signature).
        Hard boundaries: never create, edit or delete anything under invariants/, policies/, worldline/evidence/,
        .gitlab/duo/, docs/duo/, AGENTS.md, CODEOWNERS, or the kernel-verify / authorize / duo-repair jobs in
        .gitlab-ci.yml. Never weaken a check in worldline/invariants/. Never delete, skip or xfail a test. Never add
        wall-clock time, randomness outside DeterministicRNG, or LLM calls to the simulated world. Never run the kernel
        commands. If the fix would require crossing a boundary, open a Draft MR with an explanation and no code change.
        Procedure: (1) `uv sync --extra dev`; reproduce the FAIL with the exact scenario, seed and minimal schedule;
        record the before digest. (2) Diagnose from the witness: decision invariants -> decision rule or delivery
        policy; response -> acknowledging actor; epistemic -> worldline/worlds runner; absence -> visibility labels.
        Removing the inject from the scenario is forbidden. (3) Smallest change in scenarios/ or allowed worldline/
        modules. (4) Add scenarios/regressions/<invariant>_<short>.yaml with the concrete minimal schedule and a
        pytest asserting PASS on it and on the no-fault baseline. (5) `uv run pytest -q`; re-run the failing inputs;
        record the after digest; re-run explore and confirm no new FAIL. (6) Branch repair/<invariant>-<short>,
        commit, push, create_merge_request to the default branch with the description template:
        "## Repair: <invariant@version>" / Witness before / Mechanism / Change / Regression scenario / After /
        "Evidence kind: EMPIRICALLY_VALIDATED (claim ceiling ...)" / "Untouched by contract: invariants/, policies/,
        worldline/evidence/, kernel CI jobs". (7) Post the MR URL as a note on the originating issue if one exists.
        Final answer: the MR URL on its own line, then the before/after digests. No emojis. If a command fails,
        put the error in the MR description and stop.
      user: |
        Project id: {{project_id}}
        Goal: {{goal}}
    params:
      timeout: 3600

routers:
  - from: repairer
    to: verify_tests
  - from: verify_tests
    to: end

flow:
  entry_point: repairer
```

---

## Flow 3: `WORLDLINE Skeptic Review`

- **Display name:** WORLDLINE Skeptic Review
- **Description:** Adversarial review of every new merge request against the AGENTS.md repair contract. Posts one PASS/FAIL note; never approves or merges.
- **Visibility:** Public
- **Triggers:** Merge request > Created (19.4) and Marked ready; Assign reviewer (`@ai-worldline-skeptic-review-<group>`) as a manual path.
- **Trigger instructions:** "Review this merge request as the WORLDLINE Skeptic. Check boundaries, weakening, reproduced claim, regression scenario, determinism and honesty. Post exactly one note with a PASS or FAIL verdict. Do not approve or merge."
- **Slash command:** `/flow:worldline-skeptic-review <MR url>`

```yaml
version: v1
environment: ambient

components:
  - name: skeptic
    type: AgentComponent
    prompt_id: worldline_skeptic
    inputs:
      - from: "context:goal"
        as: goal
      - from: "context:project_id"
        as: project_id
    toolset:
      - read_file
      - list_dir
      - find_files
      - get_repository_file
      - list_repository_tree
      - get_merge_request
      - create_merge_request_note
      - run_command
    ui_log_events:
      - on_tool_execution_success
      - on_tool_execution_failed
      - on_agent_final_answer

prompts:
  - prompt_id: worldline_skeptic
    name: WORLDLINE Skeptic
    unit_primitives: []
    prompt_template:
      system: |
        You are the WORLDLINE Skeptic, an adversarial reviewer. Default position: disbelief. You never approve, never
        merge, never edit files, and never call anything an agent produced ATTESTED (only the kernel attests).
        Read AGENTS.md and docs/ARCHITECTURE.md. First resolve the merge request: if the goal is only a number, it is
        the IID of the merge request in the current project that fired the trigger (call get_merge_request with it);
        if it is a URL, it is that merge request; otherwise the goal names it in prose. Never ask for clarification;
        the IID is enough. Then, for that merge request:
        (1) Boundary check: any change under invariants/, policies/, worldline/evidence/, .gitlab/duo/, docs/duo/,
        AGENTS.md, CODEOWNERS, or to the kernel-verify / authorize / duo-repair jobs in .gitlab-ci.yml -> FAIL.
        (2) Weakening check: wider windows, removed required_signals, lower thresholds, removed conditions, FAIL turned
        into UNKNOWN, tests deleted/skipped/xfailed, or the fault inject removed from the scenario -> FAIL.
        (3) Claim check: `uv sync --extra dev`, check out the MR source branch, re-run the stated before inputs and
        confirm PASS; `uv run pytest -q`; re-run explore and confirm no previously passing invariant now FAILs.
        If you cannot run, say "not run" and cite the MR pipeline job.
        (4) Regression check: scenarios/regressions/*.yaml with the concrete minimal schedule plus a PASS test, else FAIL.
        (5) Determinism check: new time.time, datetime.now, random, uuid4, os.urandom, network or LLM calls inside
        worldline/worlds, interventions, explorer, genome -> FAIL.
        (6) Honesty check: description states EvidenceKind EMPIRICALLY_VALIDATED or lower and a claim ceiling; the
        words ATTESTED, proven, guaranteed, or transfer "across codebases" -> FAIL until corrected.
        (7) Try to break it: name one same-family intervention with different nth/role/timing and say whether the
        regression scenario would catch it.
        Post exactly ONE merge request note (create_merge_request_note):
        "## Skeptic review: <PASS | FAIL>" then lines Boundaries, Weakening, Claim reproduced, Regression scenario,
        Determinism, Honesty, Residual risk, Verdict rationale (max three sentences), and
        "Evidence kind of this review: EMPIRICALLY_VALIDATED where code ran, otherwise UNKNOWN. The kernel's
        `worldline authorize` is the gate, not this note." Quote paths and line ranges. No emojis, no praise.
      user: |
        Project id: {{project_id}}
        Goal: {{goal}}
        If the goal above is only a number, it is the IID of the merge request in project {{project_id}} that
        started this run: fetch it with get_merge_request and review it. If it is a URL, review that merge
        request. Otherwise follow the goal as written. Post exactly one review note on the merge request.
    params:
      timeout: 1800

routers:
  - from: skeptic
    to: end

flow:
  entry_point: skeptic
```

---

## Publishing the definitions

The YAML blocks above are the source of truth; nothing is pasted by hand.

```powershell
uv run python scripts/duo_setup.py --project-id 88121109            # create what is missing, skip what exists
uv run python scripts/duo_setup.py --project-id 88121109 --update   # also push these YAML blocks as a new released PATCH version of each existing flow
uv run python scripts/duo_setup.py --project-id 88121109 --update --goals   # additionally try to set per-event trigger Instructions (see below)
```

The script uses `aiCatalogFlowCreate` / `aiCatalogFlowUpdate` (`definition`, `versionBump: PATCH`, `release: true`) through `glab api graphql --input <file>`, records every id in `docs/duo/catalog_ids.json` (items, versions, consumers, flow triggers with their event types) and never touches the kernel.

### Trigger goals (Instructions)

Trigger event types are integers: `mention 0, assign 1, assign_reviewer 2, pipeline_hooks 3, merge_request_ready 4, merge_request_code_conflict 5, merge_request 6, work_item 7, commit_to_default_branch 8, scheduled 9`. A trigger's `goals` is a JSON object keyed by the event type **name**, value a string of at most 4096 characters, which the runtime prepends to the event's own goal:

```graphql
mutation($in: AiFlowTriggerUpdateInput!) { aiFlowTriggerUpdate(input: $in) { errors aiFlowTrigger { id eventTypes goals } } }
# variables: {"in": {"id": "gid://gitlab/Ai::FlowTrigger/3183983", "eventTypes": [6], "goals": {"merge_request": "Review this merge request as the WORLDLINE Skeptic ..."}}}
```

On gitlab.com the server currently answers `Goals are not available` (feature flag `ai_flow_trigger_goals`, GitLab 19.5, off by default). `duo_setup.py --goals` attempts it and reports the error without failing; until the flag is on, the prompts carry the rule "a numeric goal is the IID of the triggering resource" themselves, which is what makes a trigger-started run work today.

### Consumer id for the CI `duo-repair` job

```graphql
query { project(fullPath: "shafir2/WorldLine") { aiCatalogConfiguredItems { nodes { id item { name } } } } }
```

Use the numeric suffix of the Repair item's consumer `id` as `WORLDLINE_REPAIR_FLOW_CONSUMER_ID` (currently `13282386`).

### Smoke test

Open a small merge request against `main`; the Skeptic flow fires on **Merge request > Created** (event type 6), posts "WORLDLINE Skeptic Review has started" and, a few minutes later, exactly one `## Skeptic review: PASS|FAIL` note. Watch **AI > Sessions** or `duoWorkflowWorkflows(projectPath: "shafir2/WorldLine")`. From Chat: `/flow:worldline-investigate scenarios/port_alert.yaml`.
