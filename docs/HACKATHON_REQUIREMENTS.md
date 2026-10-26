# GitLab Transcend "Life After Code" hackathon: requirements as researched on 2026-10-26

Status legend: **CONFIRMED** = read on an official page (Devpost hackathon site via search-engine snippets, docs.gitlab.com, about.gitlab.com, gitlab.com). **UNVERIFIED** = only seen in third-party write-ups, or inferred; re-check on the live Devpost page before submitting.

The Devpost pages (`/`, `/rules`, `/resources`, `/details/*`, `/updates`) return HTTP 403 to non-browser fetches, so everything attributed to Devpost below comes from search-engine snippets of those pages. Open them in a browser and reconcile before the deadline.

Official pages:

- Hackathon home: https://gitlab-transcend.devpost.com/
- Rules: https://gitlab-transcend.devpost.com/rules
- Resources: https://gitlab-transcend.devpost.com/resources
- Contributor onboarding guide (JS-rendered, empty to a plain fetch): https://contributors.gitlab.com/transcend-hackathon
- Hackathon GitLab group (participant projects live here): https://gitlab.com/gitlab-ai-hackathon/transcend/
- Discord: GitLab Community Discord, channel `#transcend-hackathon`

## 1. Deadline

| Item | Status | Detail |
|---|---|---|
| Submission deadline | CONFIRMED | **27 Oct 2026, 13:00 UTC** (the landing page shows 9:00am EDT on 27 Oct; same instant). |
| Submission period start | CONFIRMED | Submissions opened 5 Oct 2026. Path B automation/deployment work must be dated on or after 5 Oct. |

## 2. What must be submitted (Devpost fields)

| Item | Status | Detail |
|---|---|---|
| Public GitLab project | CONFIRMED (snippet) | "A public GitLab project with source, assets, and run instructions." Hosting is GitLab, not GitHub. |
| MIT license | CONFIRMED (snippet) | An MIT `LICENSE` file, and the license must show in the project **About** section (GitLab detects a root `LICENSE` automatically). Our `LICENSE` is already MIT, (c) 2026 shafir-01. |
| Pipeline history | CONFIRMED (snippet) | "Visible pipeline history showing the automation running." Pipelines must actually have run on the public project; judges look at Build > Pipelines. |
| Demo video | CONFIRMED (snippet) | Under **3 minutes**, uploaded to **YouTube**, publicly visible, must show the automation running (agents/flows reacting to a trigger and taking an action). |
| Text description | CONFIRMED (snippet) | Devpost text description of the project. |
| Autonomy level | CONFIRMED (snippet) | Pick exactly one of Assisted / Supervised / Hands-off (section 4). |
| Path | CONFIRMED (snippet) | Pick Path A or Path B (section 3). |
| Path B extras | CONFIRMED (snippet) | Deployed project URL, link to the original project, and an explanation of what changed during the submission period. Not applicable to WORLDLINE (Path A). |
| Must use Duo Agent Platform | CONFIRMED | "Every project must use GitLab Duo Agent Platform features, such as agents, flows, or MCP clients." The earlier 2026 GitLab AI Hackathon stated "Chat alone won't qualify"; UNVERIFIED that the Transcend rules repeat that sentence, but the wording implies it. |
| Other Devpost fields (team, built-with tags, cover image) | UNVERIFIED | Standard Devpost form fields; not seen in snippets. |

## 3. Path A vs Path B

| Path | Status | Definition |
|---|---|---|
| **Path A, Start Fresh** | CONFIRMED | "Your project must be new work, created for this hackathon. Deployment is optional. Existing projects are not allowed on this track." |
| **Path B, Bring Your Own** | CONFIRMED | Start from an existing MIT-licensed open-source project, import it to GitLab, add the agentic automation, deploy it. Automation and deployment on or after 5 Oct 2026. |

WORLDLINE is **Path A**. Deployment (GitLab Pages for the UI) is optional but strengthens the demo.

## 4. Autonomy tiers

| Tier | Status | Definition (paraphrased from the rules page snippet) |
|---|---|---|
| **Assisted** | CONFIRMED (snippet) | Agents propose actions and wait for the participant's go-ahead before proceeding; the human signs off on each step. |
| **Supervised** | CONFIRMED (snippet) | Agents run multi-step workflows by themselves, clearing gates and making decisions along the way; the human checks only the end result. |
| **Hands-off** | CONFIRMED (snippet) | The human sets the intent and steps away; no human in the middle from code to production (agents handle review, merging, staging, production promotion). |
| Prize categories | CONFIRMED (snippet) | Best Assisted / Best Supervised / Best Hands-off for each of Path A and Path B. Total pool $45,000 (HackList listing). Per-prize amounts UNVERIFIED. |

Recommended tier for WORLDLINE: **Supervised**. The Investigator and Repairer flows run unattended from an issue event / MR event and open an MR; the kernel (`worldline authorize`) is a deterministic gate; a human reviews only the final MR. Claiming Hands-off would require auto-merge to a protected branch, which conflicts with the CODEOWNERS protection the architecture relies on.

## 5. Judging criteria

| Item | Status | Detail |
|---|---|---|
| Criteria | CONFIRMED (snippet) | Technological Implementation ("How thoroughly and skillfully does the project use GitLab to automate the post-code DevSecOps lifecycle?"), Design, Potential Impact, Innovation / Quality of the Idea. |
| Weights | UNVERIFIED | Presumably equal, on a 5-point scale (the bonus text says "highest possible final score is 5.2", implying a 5.0 base maximum). |
| Stage 1 viability pass/fail | UNVERIFIED | Seen on the sister event (gitlab.devpost.com/rules), not confirmed for Transcend. |
| Judges | CONFIRMED (HackList, secondary) | GitLab, Google, Anthropic. |

## 6. Google Cloud bonus

| Item | Status | Detail |
|---|---|---|
| Bonus size | CONFIRMED (snippet) | Up to **0.2 bonus points**, added once after judges score. Max final score 5.2. |
| Conditions | CONFIRMED (snippet) | (a) Google Cloud deployment code in the public GitLab repo; (b) link to the live project on Google Cloud; (c) public and available to judges. |
| Which GCP service | UNVERIFIED | Any. Serving the static `ui/dist` from Cloud Run (nginx) or Firebase Hosting would qualify; GitLab Pages alone does not. Optional for WORLDLINE. |

## 7. Contributor onboarding and Duo access (this decides whether we can submit at all)

| Item | Status | Detail |
|---|---|---|
| Onboarding step exists | CONFIRMED (snippet) | Devpost "Getting started": "Start GitLab Transcend Hackathon contributor onboarding (5 minutes, plus about 24 hours for approval)". This is the step that enables Duo for participants. |
| Approval delay | CONFIRMED (snippet) | About 24 hours. **With the deadline at 27 Oct 13:00 UTC, onboarding must be started now if it has not been.** |
| Where onboarding happens | CONFIRMED (URL) | https://contributors.gitlab.com/transcend-hackathon (GitLab.com sign-in; JS-rendered). |
| Provisioned project per participant | CONFIRMED (pattern) | gitlab.com hosts projects `gitlab-ai-hackathon/transcend/<numeric id>`, each opened by a "Welcome to the Transcend Hackathon" work item. In the earlier 2026 AI Hackathon the same group pattern (`gitlab-ai-hackathon/participants/<id>`) provisioned one project per approved participant with Duo enabled, agent/flow templates, and flow service accounts named `@ai-<flow>-gitlab-ai-hackathon`. |
| Must the repo live inside the hackathon group? | UNVERIFIED | The Devpost snippet says only "a public GitLab project". Safest plan: develop in the provisioned `gitlab-ai-hackathon/transcend/<id>` project (Duo Agent Platform is entitled at group level there) and, if you want the `shafir-01/worldline` URL, keep a push mirror there. Ask in `#transcend-hackathon` and record the answer in this file. |
| Is a Duo trial needed? | CONFIRMED (docs) | Agent Platform (agents, flows, triggers, custom flows) needs Premium/Ultimate, or Free tier on GitLab.com **with purchased GitLab Credits** (18.10+). A personal namespace without a subscription cannot run flows. Hackathon onboarding is the free route: the hackathon group carries the entitlement. Fallback: GitLab Ultimate trial (https://about.gitlab.com/free-trial/); UNVERIFIED whether the trial includes Agent Platform credits. |
| Default Duo namespace | CONFIRMED (earlier hackathon welcome issue) | For IDE / `duo` CLI testing set **Default GitLab Duo namespace** to the hackathon group (User settings > Preferences > GitLab Duo). Without it custom agents cannot be tested locally. |
| Support | CONFIRMED | Discord `#transcend-hackathon`; office hours announced there; in-project mention `@gitlab-org/developer-relations/contributor-success`. |

## 8. Derived submission checklist for WORLDLINE

1. Contributor onboarding approved (check e-mail and Discord).
2. Public project on GitLab.com, MIT `LICENSE` at root, visible in About.
3. README with run instructions (`uv sync --extra dev`, `uv run pytest`, `uv run worldline ...`).
4. Three **custom agents** (Investigator, Repairer, Skeptic) and at least one **custom flow**, Public visibility, enabled in the project.
5. Pipeline history: several pipelines, including one where `authorize` is DENIED and one AUTHORIZED, plus at least one Duo session visible under **AI > Sessions** with the MR it opened.
6. YouTube video under 3:00: create an issue, show the flow session start, the MR, the kernel verdict, the Pages UI.
7. Devpost form: Path A, autonomy Supervised, text description, repo URL, video URL, optional Google Cloud URL.

## 9. Sources

- https://gitlab-transcend.devpost.com/ (snippets)
- https://gitlab-transcend.devpost.com/rules (snippets)
- https://gitlab-transcend.devpost.com/resources (snippets)
- https://gitlab.com/gitlab-ai-hackathon/transcend/39104977/-/work_items/1
- https://gitlab.com/gitlab-ai-hackathon/participants/621485/-/issues/1 (earlier 2026 AI Hackathon, pattern reference)
- https://docs.gitlab.com/user/duo_agent_platform/
- https://docs.gitlab.com/user/duo_agent_platform/turn_on_off/
- https://docs.gitlab.com/subscriptions/gitlab_credits/
- https://hacklist.io/ (secondary: prize pool, judges)
