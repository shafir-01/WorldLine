# GitLab setup for WORLDLINE (owner: shafir-01)

How the project is set up on GitLab.com: protected refs, CI variables, GitLab Duo Agent Platform agents and flows with their triggers, the release flow and Pages. Written against the GitLab 19.x docs; every UI path and version note has its doc URL. Anything marked UNVERIFIED could not be confirmed on an official page or on this project.

Project: https://gitlab.com/shafir2/WorldLine (project id `88121109`). The Agent Platform only works in a namespace that is entitled: Premium/Ultimate, GitLab Credits, or a group where Duo Agent Platform is already turned on. Entitlement details are in `docs/HACKATHON_REQUIREMENTS.md`.

## 0. Create and push the project

```powershell
# glab auth (once)
glab auth login --hostname gitlab.com

# Create the public project (skip if it already exists)
glab repo create WorldLine --public --description "WORLDLINE: counterfactual computing substrate; branch executions, isolate the causal difference, type the evidence, gate the release." --defaultBranch main

git remote add gitlab https://gitlab.com/shafir2/WorldLine.git
git push -u gitlab main
```

Verify the MIT license is detected: project home page > About sidebar shows "MIT License". GitLab reads the root `LICENSE` file.

## 1. Protect the default branch, the release tags and the kernel paths

```powershell
# Protected branch: only Maintainers push/merge, require CODEOWNERS approval
glab api --method POST projects/88121109/protected_branches -f name=main -f push_access_level=40 -f merge_access_level=40 -f code_owner_approval_required=true

# Protected tags: only Maintainers create v* tags, so only they can cut a sealed release
glab api --method POST projects/88121109/protected_tags -f name="v*" -f create_access_level=40
```

Then in **Settings > Merge requests**: set "Merge checks" to require pipeline success, and under **Settings > Repository > Protected branches** confirm `main` has "Require approval from code owners" on. `CODEOWNERS` at the repo root routes `invariants/`, `policies/`, `worldline/evidence/`, `.gitlab-ci.yml`, `.gitlab/duo/`, `AGENTS.md`, `CODEOWNERS` to `@shafir-01`. The Duo docs recommend protecting `.gitlab/duo/agent-config.yml` this way because its `setup_script` runs with the triggering user's token (https://docs.gitlab.com/user/duo_agent_platform/flows/execution/agent-config-yaml/).

## 2. CI/CD variables

Three variables unlock the protected parts of the pipeline. Set them in **Settings > CI/CD > Variables** or with `glab variable set`.

| Variable | Type | What it unlocks |
|---|---|---|
| `WORLDLINE_KERNEL_KEY` | protected, File | The kernel's Ed25519 private key. `kernel-verify` seals the evidence bundle with it, which lets `authorize` reach AUTHORIZED and lets a `v*` tag become a release. Without it, or on an unprotected ref, `kernel-verify` fails with "requires a protected ref". |
| `GCP_PROJECT` | plain | The Google Cloud project id for the manual `deploy-gcp` job. |
| `GCP_SA_KEY` | protected, File | JSON key of the deploying service account, with the roles listed in `service/README.md`. Together with `GCP_PROJECT` it makes `deploy-gcp` available on protected refs, where it deploys the public verifier to Cloud Run. |

```powershell
# Generate the kernel key pair locally; only policies/kernel_public.pem is committed
uv run worldline keygen
glab variable set WORLDLINE_KERNEL_KEY --protected --type file --value "$(Get-Content -Raw policies/kernel_private.pem)"
Remove-Item policies/kernel_private.pem

glab variable set GCP_PROJECT --value "<gcp-project-id>"
glab variable set GCP_SA_KEY --protected --type file --value "$(Get-Content -Raw sa-key.json)"
```

Protected means only pipelines on protected branches and tags see the variable; the kernel jobs use `rules:` so they only run there. UNVERIFIED: whether `--masked` accepts a multi-line PEM on every glab version. The variable is protected and no job prints it, so masking is optional.

Optional, only for the manual `duo-repair` job (section 7):

```powershell
# Project access token, scope api, role Developer (Settings > Access tokens)
glab variable set WORLDLINE_DUO_TOKEN --protected --masked --value "<token>"
# Numeric consumer id of the enabled Repair flow, from docs/duo/catalog_ids.json
glab variable set WORLDLINE_REPAIR_FLOW_CONSUMER_ID --value "<id>"
```

## 3. Turn on GitLab Duo and the Agent Platform (top-level group Owner only)

Skip if the group already has it on. Doc: https://docs.gitlab.com/user/duo_agent_platform/turn_on_off/ and https://docs.gitlab.com/user/duo_agent_platform/flows/foundational_flows/#turn-foundational-flows-on-or-off

1. Group > **Settings > GitLab Duo > Change configuration**.
2. **GitLab Duo availability**: On, or Always on.
3. Without Duo Pro or Enterprise seats: **GitLab Duo Core**: tick "Turn on features for GitLab Duo Core".
4. **GitLab Duo Agent Platform**: tick "Turn on GitLab Duo Agentic Chat, agents, and flows". It is on by default.
5. **Flow execution**: tick "Allow flow execution" and "Allow foundational flows". Leave custom agents and custom flows on.
6. Save. Changes can take up to 10 minutes.

Project level (Maintainer): **Settings > General > GitLab Duo**: turn on GitLab Duo, Allow flow execution, Allow foundational flows. GitLab.com hosted runners work out of the box; flows run in CI jobs tagged `gitlab--duo` (https://docs.gitlab.com/user/duo_agent_platform/flows/execution/).

Agent configuration file: `.gitlab/duo/agent-config.yml` is in the repo and is read only from the default branch.

## 4. Create the agents and flows with `scripts/duo_setup.py`

The three custom agents (WORLDLINE Investigator, Repairer, Skeptic) and the three flows (WORLDLINE Investigate, WORLDLINE Repair, WORLDLINE Skeptic Review) are defined as text in `docs/duo/` and published to the project's AI Catalog by one idempotent script. It calls the GraphQL API through your own `glab` login and stores no token.

```powershell
uv run python scripts/duo_setup.py --project-id 88121109 --dry-run    # show what would be created
uv run python scripts/duo_setup.py --project-id 88121109              # create missing agents and flows, skip existing ones
uv run python scripts/duo_setup.py --project-id 88121109 --update     # also push the YAML in docs/duo/flows.md as a new released PATCH version of each flow
uv run python scripts/duo_setup.py --project-id 88121109 --goals      # also set per-event trigger Instructions (section 6)
```

Other flags: `--project-path` (default `shafir2/WorldLine`) and `--visibility PRIVATE|RESTRICTED|PUBLIC` (default `PUBLIC`). Agents take their system prompt from `docs/duo/investigator.md`, `repairer.md` and `skeptic.md` and their tool list from the script; flows take their YAML from `docs/duo/flows.md`. The script writes every id it created or found to `docs/duo/catalog_ids.json`: items, latest versions, the project consumers and their flow triggers.

Manual fallback: create each agent in **AI > Agents > New agent** with the system prompt and tools from its `docs/duo/*.md` file, and each flow in **AI > Flows > New flow** with its YAML block from `docs/duo/flows.md`.
Docs: https://docs.gitlab.com/user/duo_agent_platform/agents/custom/ and https://docs.gitlab.com/user/duo_agent_platform/flows/custom/.

The script does not enable items. **Enable** each agent and flow for the project (AI > Agents or AI > Flows > Managed tab > item > Enable > this project) and add the triggers from section 6 in the enable dialog. A rerun of the script then reports each consumer and its triggers.

Test an agent from Chat: open an issue, GitLab Duo sidebar > **Add new chat** > choose `WORLDLINE Skeptic` > ask "Review the latest FAIL witness in experiments/out". Chat is useful for checking an agent; the triggers are the automation.

## 5. Service accounts (automatic)

They are not created by hand. Enabling a flow in a group creates `ai-<flow>-<group>` in the top-level group and adds it to the project as Developer (https://docs.gitlab.com/user/duo_agent_platform/flows/custom/, https://docs.gitlab.com/user/duo_agent_platform/composite_identity/). If `main` has push rules such as "commit author must be a member", allow the service account (https://docs.gitlab.com/user/duo_agent_platform/troubleshooting/#configure-push-rules-to-allow-a-service-account). Merge requests a flow opens are attributed to the human who triggered the event.

## 6. Flow triggers

Docs: https://docs.gitlab.com/user/duo_agent_platform/triggers/, schema https://docs.gitlab.com/user/duo_agent_platform/flows/custom_flows_schema/ and the flow registry v1 spec https://gitlab.com/gitlab-org/modelops/applied-ml/code-suggestions/ai-assist/-/blob/main/docs/flow_registry/v1.md

Facts that shape the design:

- A custom flow cannot call a custom agent; it defines its own agents inline in YAML (`AgentComponent` + `prompts`). The flows in `docs/duo/flows.md` therefore embed the Investigator, Repairer and Skeptic system prompts. The custom agents are the Chat-facing versions.
- There is no label trigger. Trigger event types are Mention, Assign, Assign reviewer, Pipeline events (experiment), Merge request (Approved / Created / Marked ready / Merge conflict) and Work item (Created / Status changed). The label `worldline:investigate` is a human convention for filtering; a work item event or a mention is what fires a flow.
- Triggers can only be activated by humans; a flow cannot trigger another flow by mention. The Repairer reaches the Skeptic by opening a merge request.
- For Merge request and Work item events the flow's goal is the bare IID of the triggering resource; for a Mention it is the comment text. The system prompts in `docs/duo/flows.md` treat a numeric goal as that IID.

Triggers to add when enabling each flow, with their goals:

| Flow | Events | Trigger goal |
|---|---|---|
| WORLDLINE Investigate | Work item; Mention | Read the work item, run the exploration on the scenario it names (default `scenarios/port_alert.yaml`, seed 42, budget 200), minimise, extract the genome and post one Investigation comment. Do not edit files. |
| WORLDLINE Repair | Mention | Repair the FAIL described in the issue under the AGENTS.md repair contract: make the failing branch PASS, add a regression scenario and test, open one merge request. Never modify `invariants/`, `policies/`, `worldline/evidence/` or kernel CI jobs. |
| WORLDLINE Skeptic Review | Merge request | Review the merge request against AGENTS.md: boundaries, weakening, reproduced claim, regression scenario, determinism, honesty. Post exactly one note with a PASS or FAIL verdict. Do not approve or merge. |

The exact texts are `TRIGGER_GOALS` in `scripts/duo_setup.py`, and `--goals` writes them to the triggers. Per-trigger Instructions sit behind the feature flag `ai_flow_trigger_goals` (19.5), which is off on gitlab.com: the server answers `Goals are not available`, the script reports it without failing, and the system prompts carry the same rule until the flag is on. UNVERIFIED: a Work item > Status changed trigger with a custom status filter such as `Investigate`, which needs work item custom statuses (Premium/Ultimate, **Settings > Issues > Statuses**).

Manage triggers later under **AI > Triggers** (Maintainer) or the flow page > Trigger conditions. Run a flow by hand from Agentic Chat in the project: `/flow:worldline-investigate scenarios/port_alert.yaml` (https://docs.gitlab.com/user/gitlab_duo_chat/agentic_chat/).

## 7. Starting a flow from CI (the `duo-repair` job)

`POST /api/v4/ai/duo_workflows/workflows` is GA in 19.5 (https://docs.gitlab.com/api/duo_agent_platform_flows/). With `ai_catalog_item_consumer_id`, `goal`, `project_id` and `start_workflow: true` it starts an enabled catalog flow and returns the run's `web_url`. `.gitlab-ci.yml` has a manual `duo-repair` job that calls it with `WORLDLINE_DUO_TOKEN`. The token is a project access token with `api` scope, because the flows' own `GITLAB_TOKEN` only has `ai_workflows` scope. If the group has `enforce_composite_identity_for_api_started_workflows` on, the call returns 403 and a mention is the way to start the flow.

## 8. Agent run records

- Project > **AI > Sessions** lists every flow run with its status and a Details tab with full or concise logs. Retention is 30 days after last activity (https://docs.gitlab.com/user/duo_agent_platform/sessions/).
- The issue or merge request a flow worked on gets system notes linking the run.
- `GET /api/v4/ai/duo_workflows/workflows/:id/trace.jsonl` (experiment) returns a run's log as JSON Lines. Saved under `experiments/out/duo_sessions/` it is agent evidence with producer `agent:*` and kind `EMPIRICALLY_VALIDATED` at best; the kernel never upgrades it.
- Group Owners: **Settings > GitLab Duo > Change governance > Audit events** (beta, storage off by default) gives per-run audit rows and a downloadable JSON record (https://docs.gitlab.com/user/duo_agent_platform/ai-audit-events/). UNVERIFIED on this project's group.

## 9. Pipeline and releases

Stages: `test`, `explore`, `kernel-verify`, `authorize`, `release`, `pages`. The `test` stage also runs the GitLab SAST, secret detection and dependency scanning templates included from `.gitlab/ci/security.yml`; findings appear under Secure > Vulnerability report. The manual `deploy-gcp` job from `.gitlab/ci/gcp.yml` deploys the public verifier from a protected ref once `GCP_PROJECT` and `GCP_SA_KEY` are set.

Release flow: push a tag matching `v*`. Because the tag is protected, `kernel-verify` can read `WORLDLINE_KERNEL_KEY` and seal the bundle, `authorize` decides for the tagged commit, and the tag-only jobs `release-assets` and `release` upload the evidence to the generic package registry and create the release. A DENIED tag never becomes a release. The first release is https://gitlab.com/shafir2/WorldLine/-/releases/v0.1.3-transcend: kernel decision AUTHORIZED, 14 of 14 requirements, with `bundle.json`, `authorization.json`, `genome.json`, `minimal_schedule.json`, `challenge.json` and `index.json` as assets that download without signing in. Cutting a release and verifying one offline: [docs/RELEASES.md](RELEASES.md).

```powershell
git tag -a v0.2.0 -m "WORLDLINE v0.2.0"
git push gitlab v0.2.0
```

## 10. GitLab Pages

Pages is on by default for public projects on GitLab.com. The `pages` job publishes `public/` from `ui/dist` plus `experiments/out`. URL: Deploy > Pages. With "Use unique domain" on, the URL is `https://<project>-<hash>.gitlab.io`; the Vite build uses `base: './'` so either works.

## 11. Order of operations

1. Push the repository, protect `main` and `v*`, set the variables (sections 0 to 2).
2. First pipeline green on `main`: `test`, `explore`, `kernel-verify`, `authorize`, `pages`. The kernel jobs need `policies/kernel_public.pem` committed.
3. Turn on Duo (section 3), run `scripts/duo_setup.py` (section 4), enable the items and add the triggers (section 6).
4. Open an issue that names a scenario and mention the Investigate service account; the run appears under AI > Sessions.
5. Tag a `v*` release and verify it offline (section 9).
