# Devpost submission draft: WORLDLINE

Fill the Devpost form from this file. Replace the two placeholders (video URL, agent session links) once they exist. Everything else is final unless numbers are regenerated.

## Project name

WORLDLINE

## Tagline (under 100 characters)

Run software in many worlds, isolate the one difference that breaks it, and let nothing ship on untyped evidence.

## Path and autonomy

Path A (new project, created after 5 October 2026). Autonomy tier: **Supervised**. Agents investigate and propose repairs as merge requests; an independent kernel in CI recomputes their claims and decides whether the exact commit is authorized. A human merges.

## Links

- Repository (public, MIT): https://gitlab.com/shafir2/WorldLine
- Live viewer (GitLab Pages, built from the pipeline's own output): https://worldline-3e380d.gitlab.io
- Pipelines: https://gitlab.com/shafir2/WorldLine/-/pipelines
- First proof-carrying release (kernel AUTHORIZED, 14 of 14 requirements; signed bundle and authorization as assets): https://gitlab.com/shafir2/WorldLine/-/releases/v0.1.3-transcend
- Public verifier (Google Cloud Run, live): https://worldline-verifier-301330323690.europe-north1.run.app (viewer at /, API at /api/verify, /api/authorize, /api/evidence, /api/version)
- Investigation issue with the real witness: https://gitlab.com/shafir2/WorldLine/-/work_items/1
- Duo agent sessions: Investigator https://gitlab.com/shafir2/WorldLine/-/automate/agent-sessions/10080962; Repairer https://gitlab.com/shafir2/WorldLine/-/automate/agent-sessions/10081188 and follow-up https://gitlab.com/shafir2/WorldLine/-/automate/agent-sessions/10081957; Skeptic https://gitlab.com/shafir2/WorldLine/-/automate/agent-sessions/10081961
- Repair merge request (left open on purpose, see description): https://gitlab.com/shafir2/WorldLine/-/merge_requests/1
- Video (under 3 minutes): `[YouTube URL]`

## Inspiration

A change is judged against one world: the test suite's. Tests pass, the pipeline is green, an agent writes a confident explanation, the release ships. The failures that matter live in the other worlds: the one where the correcting report arrived ninety seconds later, where a permission was revoked mid-run, where two correct components composed into a wrong decision. Nothing in a conventional pipeline asks about those worlds, and nothing distinguishes "the tests passed" from "this is known to hold". We also did not want to build one more agent that grades its own homework.

## What it does

WORLDLINE runs a system together with its environment deterministically, branches the history with environmental interventions (delay, drop, duplicate, reorder, corrupt, revoke, clock skew), and checks three-valued invariants with witnesses. The one that drives the demo is `decision.consistent_with_truth`: an actor's rule-based decision differs from what the same rule says over the truth, because of information that had not yet arrived.

When a world fails, delta debugging reduces it to a 1-minimal set of interventions and re-checks minimality independently. The mechanism is abstracted into a FailureGenome with structural selectors and preconditions, no event ids, and used to challenge a second scenario from a different domain. A deliberately immune variant is the negative control.

Every claim is typed: PROVED_WITHIN_MODEL, EMPIRICALLY_VALIDATED, ATTESTED, FAILED or UNKNOWN. Evidence is hash-chained and Ed25519-sealed. Agents can never emit ATTESTED. The kernel recomputes what agents report; a mismatch becomes a FAILED integrity record that denies the release. Authorization binds to the exact commit digest and is revoked when an assumption is invalidated. Releases are proof-carrying: every tag ships the sealed evidence bundle and the kernel's authorization as release assets, and a public verifier service on Cloud Run, holding only the public key, lets anyone re-check a bundle and the release decision without trusting the pipeline.

On GitLab: the pipeline runs test, explore, kernel-verify, authorize, release and pages, with GitLab SAST, secret detection and dependency scanning. Three custom Duo agents (Investigator, Repairer, Skeptic) and three flows are enabled on the project. The Investigator re-ran the exploration in a hosted session, confirmed identical digests and posted the witness. The Repairer opened a merge request under a repair contract. GitLab's Code Review found the repair vacuous; the Repairer addressed it; our Skeptic agent still failed it for sidestepping the fault rather than tolerating it. Every step is a real session with a transcript. The merge request is left open because the Skeptic is right, and that is the point: agents never evaluate themselves, and the kernel jobs are protected by CODEOWNERS.

## The measured result

Scenario A is a synthetic port security incident. Scenario B is a synthetic logistics convoy, written without reference to A's defect. From `uv run worldline demo --budget 150 --seed 42` on one machine:

- Guided exploration: 69 branches, 23 failing, first decision failure at branch 6. Random baseline: 66 branches, 20 failing, none within budget.
- A 3-intervention failing schedule minimized to 1 (delay the correction past the decision), 3 oracle calls, 1-minimality verified.
- Genome challenge on B: reproduced (retraction delayed to the operations centre flips "continue" to "reroute"). Immune control: preconditions met, not reproduced.
- Kernel: 31 evidence records, 16 attested, 0 agent-report mismatches. Exact commit AUTHORIZED. A one-record edit to the bundle: DENIED.
- Across 5 seeds (budget 150): guided finds 44.5 failing worlds per 100 branches versus 30.5 for random; first decision failure at index 3.4 versus 14.0; every one of 5 minimizations reached a single intervention.

Claim ceiling: EMPIRICALLY_VALIDATED on two synthetic scenarios, one machine. Transfer is across scenarios sharing the Event v1 substrate, not across heterogeneous codebases.

The pipeline also deploys the verifier to Cloud Run through a manual `deploy-gcp` job that only unlocks after the kernel authorizes the commit; the live service reports the pipeline commit.

## How we built it

Python 3.12 with uv; PyYAML, Typer, Rich, cryptography; pytest and Hypothesis (158 tests). A Vite, React and TypeScript viewer with hand-written SVG, no backend. GitLab CI with protected kernel jobs. GitLab Duo Agent Platform agents and flows created through the GraphQL catalog API from prompt files kept in the repository. No LLM anywhere in the simulated world or the checker.

## Challenges

Keeping three information states apart (simulated truth, each role's received observations, evaluator conclusions) in storage and in every query, so the checker can never accidentally know what a role did not. Making guided exploration honest: it reads only the declared decision rules, never runtime truth. Designing a genome abstract enough to transfer yet concrete enough not to fire on the immune control. Building a kernel that agents cannot satisfy by asserting success.

## What we learned

Random fault injection finds plenty of failures; the hard part is reducing them to one cause and stating what class of system it applies to. Three-valued verdicts matter: UNKNOWN with a named missing signal prevented several false claims. And "the agent said the tests passed" is not evidence until something independent recomputes it.

## What's next

Partial-order reduction in the explorer; a held-out corpus of scenarios for genome precision and recall; symbolic solving of the delay window instead of sweeping; relational (two-trace) epistemic properties; and the real test of the hypothesis, pointing a genome extracted from the simulator at an instrumented service. Roadmap with falsifying experiments: docs/ROADMAP.md.

## Built with

python, uv, pyyaml, typer, cryptography, pytest, hypothesis, typescript, react, vite, gitlab-ci, gitlab-duo-agent-platform, gitlab-pages
