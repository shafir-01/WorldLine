# GitLab setup for WORLDLINE (owner: shafir-01)

Step-by-step to get the project public on GitLab.com with GitLab Duo Agent Platform agents, flows, triggers and the CI kernel. Written against the GitLab 19.x docs as of 2026-10-26; every UI path and version note has its doc URL. Anything marked UNVERIFIED could not be confirmed on an official page.

Read `docs/HACKATHON_REQUIREMENTS.md` first: the Agent Platform only works in a namespace that is entitled (hackathon group, Premium/Ultimate, or Free + GitLab Credits).

## 0. Decide the namespace

Two options. Do A unless the organisers confirm any public project qualifies.

**A. Hackathon-provisioned project (recommended).** After contributor onboarding (https://contributors.gitlab.com/transcend-hackathon, ~24 h approval) you get a project under `gitlab.com/gitlab-ai-hackathon/transcend/<id>`. Duo Agent Platform is already turned on for the group. You are Maintainer/Owner of that project, not of the group, so the group-level steps in section 3 are already done for you; skip to section 4. Rename the project to `worldline` (Settings > General > Project name; also change the path under Advanced > Change path, if the group allows) and set visibility Public (Settings > General > Visibility, project features, permissions). If the group forbids renaming, keep the id and put "WORLDLINE" in the description.

**B. Your own namespace `shafir-01/worldline`.** Flows need Premium/Ultimate or GitLab Credits. Only use it as the canonical public URL with a push mirror from A (Settings > Repository > Mirroring repositories, Push), or if you have a trial.

```powershell
# glab auth (once)
glab auth login --hostname gitlab.com

# Option B: create the public project in your namespace
glab repo create worldline --public --description "WORLDLINE: counterfactual computing substrate; branch executions, isolate the causal difference, type the evidence, gate the release." --defaultBranch main

# Push (run from C:\Users\shafe\Desktop\Projects\WorldLine; the folder is not a git repo yet)
git init -b main
git remote add origin https://gitlab.com/shafir-01/worldline.git   # or the provisioned project URL
git add -A
git commit -m "WORLDLINE initial import"
git push -u origin main
```

Verify the MIT license is detected: project home page > About sidebar shows "MIT License". GitLab reads the root `LICENSE` file.

## 1. Protect the default branch and the kernel paths

```powershell
# Protected branch: only Maintainers push/merge, require CODEOWNERS approval
glab api --method POST projects/:fullpath/protected_branches -f name=main -f push_access_level=40 -f merge_access_level=40 -f code_owner_approval_required=true
```

Then in **Settings > Merge requests**: set "Merge checks" to require pipeline success, and under **Settings > Repository > Protected branches** confirm `main` has "Require approval from code owners" on. `CODEOWNERS` at the repo root routes `invariants/`, `policies/`, `worldline/evidence/`, `.gitlab-ci.yml`, `.gitlab/duo/`, `AGENTS.md`, `CODEOWNERS` to `@shafir-01`. The Duo docs explicitly recommend protecting `.gitlab/duo/agent-config.yml` this way because its `setup_script` runs with the triggering user's token (https://docs.gitlab.com/user/duo_agent_platform/flows/execution/agent-config-yaml/).

## 2. CI/CD variables

```powershell
# Ed25519 signing key for the kernel (generate locally, never commit the private key)
# Requires python with cryptography (uv add cryptography, or run inside the project venv)
uv run python -c "from cryptography.hazmat.primitives.asymmetric import ed25519; from cryptography.hazmat.primitives import serialization as s; k=ed25519.Ed25519PrivateKey.generate(); open('kernel_private.pem','wb').write(k.private_bytes(s.Encoding.PEM,s.PrivateFormat.PKCS8,s.NoEncryption())); open('policies/kernel_public.pem','wb').write(k.public_key().public_bytes(s.Encoding.PEM,s.PublicFormat.SubjectPublicKeyInfo))"

# Protected + masked variable holding the PEM (type File so the job gets a path)
glab variable set WORLDLINE_KERNEL_KEY --protected --masked --type file --value "$(Get-Content -Raw kernel_private.pem)"
Remove-Item kernel_private.pem

# Project access token for the optional duo-repair CI job (section 7). Scope: api. Role: Developer.
# Create in Settings > Access tokens, then:
glab variable set WORLDLINE_DUO_TOKEN --protected --masked --value "<token>"
# Numeric consumer id of the enabled Repairer flow (section 6, step 4)
glab variable set WORLDLINE_REPAIR_FLOW_CONSUMER_ID --value "<id>"
```

`glab variable set` maps to Settings > CI/CD > Variables. Protected means only pipelines on protected branches/tags see it; `.gitlab-ci.yml` kernel jobs use `rules:` so they only run there. UNVERIFIED: whether `--masked` accepts a multi-line PEM on your glab version; if it rejects it, drop `--masked` (the variable is still protected and not printed by our jobs).

## 3. Turn on GitLab Duo and the Agent Platform (top-level group Owner only)

Skip if you are in the hackathon group. Doc: https://docs.gitlab.com/user/duo_agent_platform/turn_on_off/ and https://docs.gitlab.com/user/duo_agent_platform/flows/foundational_flows/#turn-foundational-flows-on-or-off

1. Group > **Settings > GitLab Duo > Change configuration**.
2. **GitLab Duo availability**: On (or Always on).
3. If you have no Duo Pro/Enterprise seats: **GitLab Duo Core**: tick "Turn on features for GitLab Duo Core".
4. **GitLab Duo Agent Platform**: tick "Turn on GitLab Duo Agentic Chat, agents, and flows" (on by default).
5. **Flow execution**: tick "Allow flow execution" and "Allow foundational flows". Leave custom agents and custom flows on (they are on by default; same page).
6. Save. Changes can take up to 10 minutes.

Project level (Maintainer): **Settings > General > GitLab Duo**: turn on GitLab Duo, Allow flow execution, Allow foundational flows. Then check the **CI/CD runner** row on the same page; GitLab.com hosted runners work out of the box (flows run in CI jobs tagged `gitlab--duo`, doc: https://docs.gitlab.com/user/duo_agent_platform/flows/execution/).

Agent configuration file: `.gitlab/duo/agent-config.yml` is already in the repo and is read only from the default branch. Settings > General > GitLab Duo also offers "Agent configuration file > Generate" (19.6+), not needed.

## 4. Create the three custom agents

Doc: https://docs.gitlab.com/user/duo_agent_platform/agents/custom/ (GA 18.8, project-level enablement 18.10). Custom agents are created in the **UI form only**; there is no YAML file format for them in the docs, so the prompts live in `docs/duo/*.md` and are pasted in.

For each of Investigator, Repairer, Skeptic:

1. Project > **AI > Agents > New agent**.
2. **Basic information**: Display name `WORLDLINE Investigator` (then `WORLDLINE Repairer`, `WORLDLINE Skeptic`); Description: copy the one-line description from the matching `docs/duo/*.md`.
3. **Visibility & access**: **Public** (judges must be able to see it in Explore > AI Catalog).
4. **Prompts**: paste the full "System prompt" block from `docs/duo/investigator.md`, `repairer.md`, `skeptic.md`.
5. **Available tools**: select the tools listed in the "Tools" section of the same file. Tool names in the dropdown are UI labels; pick the closest match (read file/repository, list files, get issue, get MR, create MR note, create MR, run command/pipeline tools where offered). Some tools exist only in IDEs; skip them.
6. **Create agent**, then **Enable** it for the project (AI > Agents > Managed tab > agent > Enable > pick project).

Test: open an issue, GitLab Duo sidebar > **Add new chat** > choose `WORLDLINE Skeptic` > ask "Review the latest FAIL witness in experiments/out". Chat is useful for the demo but does not count as the automation.

## 5. Service accounts (automatic)

You do not create them. Enabling a flow in a group creates `ai-<flow>-<group>` in the top-level group and adds it to the project as Developer (https://docs.gitlab.com/user/duo_agent_platform/flows/custom/, https://docs.gitlab.com/user/duo_agent_platform/composite_identity/). If `main` has push rules (e.g. commit author must be a member), allow the service account (https://docs.gitlab.com/user/duo_agent_platform/troubleshooting/#configure-push-rules-to-allow-a-service-account). MRs the flow opens are attributed to the human who triggered the event.

## 6. Create the flows and their triggers

Docs: https://docs.gitlab.com/user/duo_agent_platform/flows/custom/ (custom flows GA 19.2), schema https://docs.gitlab.com/user/duo_agent_platform/flows/custom_flows_schema/ and the flow registry v1 spec https://gitlab.com/gitlab-org/modelops/applied-ml/code-suggestions/ai-assist/-/blob/main/docs/flow_registry/v1.md, triggers https://docs.gitlab.com/user/duo_agent_platform/triggers/

Important facts that shape the design:

- A custom flow **cannot call a custom agent**; it defines its own agents inline in YAML (`AgentComponent` + `prompts`). So the flows in `docs/duo/flows.md` embed the Investigator/Repairer/Skeptic prompts again. The custom agents (section 4) are the Chat-facing versions.
- **There is no label trigger.** Trigger event types are: Mention, Assign, Assign reviewer, Pipeline events (experiment, 18.9), Merge request (Approved / Created / Marked ready / Merge conflict), Work item (Created / Status changed; type and status filters 19.4-19.6). The original plan "trigger on label `worldline:investigate`" is implemented as: **Work item > Status changed**, with a custom status named `Investigate` (requires work item custom statuses, Premium/Ultimate group setting **Settings > Issues > Statuses**; UNVERIFIED on the hackathon group), **plus** a Mention trigger as the universal fallback (`@ai-worldline-investigate-<group>` in a comment). Keep the label `worldline:investigate` as a human convention and documented in the issue template, but the thing that fires is the status change or the mention.
- Triggers can only be activated by humans; a flow cannot trigger another flow by mention (exception: status-change chaining, 19.6, behind flags).
- Each trigger condition can carry **Instructions** (max 4096 chars, 19.5, behind `ai_flow_trigger_goals`, may be hidden).

Steps, per flow (Investigate first, Repair second):

1. Project > **AI > Flows > New flow**.
2. Display name `WORLDLINE Investigate` / `WORLDLINE Repair` / `WORLDLINE Skeptic Review`; description from `docs/duo/flows.md`; visibility **Public**.
3. **Configuration > Flow**: paste the YAML from `docs/duo/flows.md`. The editor validates against the registry schema; fix any key it rejects and note it in `flows.md`.
4. **Create flow**, then **Enable** (Managed tab > flow > Enable > this project). In the enable dialog, **Add triggers**:
   - Investigate: event **Work item**, action **Status changed**, status filter `Investigate` (if statuses exist); plus event **Mention**. Instructions: "Run `uv run worldline explore` on the scenario named in the issue, minimise, extract the genome, and post a FAIL witness summary. Do not edit files."
   - Repair: event **Work item**, action **Status changed**, status `Repair`; plus **Mention**. Instructions: "Make the failing branch PASS without touching invariants/, policies/, worldline/evidence/ or kernel CI jobs; add a regression scenario; open an MR."
   - Skeptic Review: event **Merge request**, action **Created** (19.4) and **Marked ready**. Instructions: "Review the diff against AGENTS.md repair contract; post one MR note with verdict PASS/FAIL and reasons; never approve."
5. After enabling, note the service account names (`ai-worldline-investigate-<group>` etc.) and the flow's **consumer id** for the API (GraphQL `aiCatalogConfiguredItems` on the project; numeric suffix of the GID; store as `WORLDLINE_REPAIR_FLOW_CONSUMER_ID`).
6. Manage later under **AI > Triggers** (Maintainer) or the flow page > Trigger conditions.

Run a flow by hand from Agentic Chat in the project: `/flow:worldline-investigate scenarios/port_alert.yaml` (slug of the display name; https://docs.gitlab.com/user/gitlab_duo_chat/agentic_chat/).

## 7. Triggering a flow from CI (the `duo-repair` job)

CONFIRMED: `POST /api/v4/ai/duo_workflows/workflows` is GA in 19.5 (https://docs.gitlab.com/api/duo_agent_platform_flows/). With `ai_catalog_item_consumer_id`, `goal`, `project_id`, `start_workflow: true` it starts an enabled catalog flow and returns `web_url` of the session. `.gitlab-ci.yml` has a **manual** `duo-repair` job that calls it with `WORLDLINE_DUO_TOKEN`. Caveats: the token is a project access token with `api` scope (the flows' own `GITLAB_TOKEN` only has `ai_workflows` scope); if the group has `enforce_composite_identity_for_api_started_workflows` on, the call returns 403 and you must use the UI/mention path instead. The job is optional for the submission; mention-triggering is the primary path.

## 8. Evidence of agent sessions (for the video and the judges)

- Project > **AI > Sessions**: every flow run, status, Details tab with full or concise logs; Cancel session at the bottom. Retention 30 days after last activity (https://docs.gitlab.com/user/duo_agent_platform/sessions/).
- The MR/issue the flow worked on gets system notes linking the session.
- `GET /api/v4/ai/duo_workflows/workflows/:id/trace.jsonl` (experiment) returns the session chat log as JSON Lines; save one into `experiments/out/duo_sessions/` as an `ATTESTED`-free artefact (producer `agent:*`, kind `EMPIRICALLY_VALIDATED` at best; the kernel never upgrades it).
- Group Owners: **Settings > GitLab Duo > Change governance > Audit events** (beta, storage off by default) gives per-session audit rows and a downloadable JSON record (https://docs.gitlab.com/user/duo_agent_platform/ai-audit-events/). UNVERIFIED on the hackathon group.

## 9. GitLab Pages

Pages is on by default for public projects on GitLab.com. The `pages` job publishes `public/` from `ui/dist` plus `experiments/out`. URL: Deploy > Pages. If "Use unique domain" is on, the URL is `https://<project>-<hash>.gitlab.io`; the Vite build uses `base: './'` so either works.

## 10. Order of operations for the last 24 hours

1. Onboarding approved? If not, use Option B with a trial and keep going.
2. Push repo, protect `main`, set variables (sections 0-2).
3. First pipeline green (`test`, `explore`, `pages`); kernel jobs need `policies/kernel_public.pem` committed.
4. Agents (section 4), flows + triggers (section 6).
5. Create issue "Port alert: stale sighting causes wrong decision", set status Investigate (or mention the service account), record the session.
6. Record the video from the session page, the MR, the DENIED/AUTHORIZED pipeline, Pages.
