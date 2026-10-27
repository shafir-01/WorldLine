# WORLDLINE

**A counterfactual computing substrate: run a system in many worlds, isolate the one difference that breaks a guarantee, turn it into a portable failure mechanism, and let nothing deploy on evidence it cannot type.**

[![pipeline](https://gitlab.com/shafir2/WorldLine/badges/main/pipeline.svg)](https://gitlab.com/shafir2/WorldLine/-/pipelines)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python 3.12](https://img.shields.io/badge/python-3.12-3776ab)
![tests](https://img.shields.io/badge/tests-342%20passing-brightgreen)
[![docs](https://img.shields.io/badge/docs-site-0b7285)](https://worldline-3e380d.gitlab.io/docs/)
[![verifier](https://img.shields.io/badge/verifier-live-2f9e44)](https://worldline-verifier-301330323690.europe-north1.run.app)
![claims](https://img.shields.io/badge/claims-typed%20by%20certainty-6f42c1)

> Every number in this file was written by code that ran; see [Evidence and claim ceilings](#evidence-and-claim-ceilings). All scenarios are synthetic.

![WORLDLINE story view](docs/screens/story.png)

| Timeline with information lag and the FAIL witness | Evidence chain and release authorization |
|---|---|
| ![Timeline](docs/screens/timeline.png) | ![Evidence](docs/screens/evidence.png) |

## Contents

[Why](#why) · [What it does](#what-it-does) · [The headline result](#the-headline-result) · [Features](#features) · [Tech stack](#tech-stack) · [Getting started](#getting-started) · [Usage](#usage) · [Project structure](#project-structure) · [Architecture](#architecture) · [GitLab Duo integration](#gitlab-duo-integration) · [Testing](#testing) · [Deployment](#deployment) · [Evidence and claim ceilings](#evidence-and-claim-ceilings) · [Roadmap and limitations](#roadmap-and-limitations) · [Contributing](#contributing) · [License](#license) · [Author](#author)

## Why

A change is normally judged against one world: the test suite's. Tests pass, the pipeline is green, an agent writes a confident explanation, the release ships. The failures that matter live in other worlds: the one where the correcting report arrived ninety seconds later, where a permission was revoked mid-run, where two correct components composed into a wrong decision. Nothing in a conventional pipeline asks about those worlds, and nothing distinguishes "the tests passed" from "this is known to hold".

WORLDLINE asks: **what would this system do if the world were different, and which differences actually matter?** And it refuses to let an agent certify its own work.

## What it does

1. **Runs a scenario deterministically** with a virtual clock. Simulated truth, each role's received observations, and evaluator conclusions are separate stores with separate clocks. A role only ever knows what it has received.
2. **Branches the history** by injecting environmental interventions at the delivery layer: delay, drop, duplicate, reorder, corrupt, revoke, clock skew.
3. **Checks three-valued invariants** (PASS, FAIL, UNKNOWN) with witnesses: temporal response, label isolation, no-future-knowledge, and `decision.consistent_with_truth`, which fires when an actor's rule-based decision differs from what the same rule says over truth, because of information that had not yet arrived.
4. **Minimizes** any failing world with delta debugging to a 1-minimal set of interventions and independently re-checks minimality.
5. **Extracts a FailureGenome**: the mechanism abstracted to structural selectors ("the last correcting report a deciding role received before its decision") and preconditions, with no event ids.
6. **Challenges a second scenario** with the genome. Different domain, roles, rules. Reports reproduced, not reproduced, or preconditions unmet. A deliberately immune variant acts as the negative control.
7. **Runs the same analysis on real inputs.** A scenario can take its truth stream from recorded public data with provenance (the included one is the USGS record of the 2025 Kamchatka M8.8 sequence, whose magnitude revisions are real corrections), and the checker, minimizer and genome can drive an actual HTTP service instead of a model. Delivery timing, roles and decision rules remain declared models and are labelled as such.
8. **Types every claim** as PROVED_WITHIN_MODEL, EMPIRICALLY_VALIDATED, ATTESTED, FAILED or UNKNOWN, chains and Ed25519-seals the evidence, and **authorizes a release** only when a policy over those kinds is met for the exact artifact digest. Agents can never emit ATTESTED; the kernel recomputes what they report, and a mismatch becomes a FAILED integrity record that denies the release.

## The headline result

Scenario A, `port_alert`: a synthetic port security incident with three roles at different latencies. The duty officer decides at 1500 s whether to escalate, based on sightings not yet corrected. In the baseline, a correction arrives in time and the decision is "monitor".

Scenario B, `convoy_lostlink`: a synthetic logistics convoy with a drone scout. Different roles, event kinds, decision rules and timings, written without reference to A's defect.

What the pipeline did on this machine (`uv run worldline demo --budget 150 --seed 42`):

| Step | Result |
|---|---|
| Explore A, guided | 69 branches, 23 failing, first decision failure at branch 6 |
| Explore A, random baseline | 66 branches, 20 failing, no decision failure within budget |
| Minimize a 3-intervention failing schedule | 1 intervention, 3 oracle calls, 1-minimality verified |
| Minimal intervention | delay `correction-001` to `harbour_ops` past the decision |
| Genome | "delay the last correcting report a deciding role received before its decision until after the decision" |
| Challenge B | **reproduced**: delaying `retract-001` to `ops_center` flips the watch officer to "reroute" while truth says "continue" |
| Immune control (B with a two-report rule) | preconditions met, **not reproduced** |
| Kernel verify | 31 records, 16 ATTESTED by kernel, 0 agent-report mismatches |
| Authorize exact commit | AUTHORIZED, 5 of 5 requirements |
| Authorize tampered bundle | DENIED: `integrity:record_edited@1` |

Benchmark across seeds 1 to 5, budget 150 (`experiments/out/benchmark.json`):

| Metric (mean over 5 seeds) | guided | random |
|---|---|---|
| failing branches per 100 run | 44.5 | 30.5 |
| index of first `decision.consistent_with_truth` failure | 3.4 | 14.0 |
| wall ms per exploration | 256 | 293 |

Minimization over five failing schedules of size 3 to 4: every one reduced to a single intervention, mean 3.4 oracle calls, 1-minimality verified in all five. The five minimal mechanisms were not all the same: drop the correction, corrupt the correction's `corrects` field, revoke the deciding role's feed from 1350 s, and corrupt the alert acknowledgement. Random exploration also found a mechanism the guided strategy did not prioritise: duplicating a sighting flips the decision, because the rule has no de-duplication.

Where the port-alert genome has been tested since, with the same mechanism instantiated by structure, never by id:

| Target | Domain | Truth stream | Outcome |
|---|---|---|---|
| `convoy_lostlink` | logistics convoy | simulated | reproduced |
| `convoy_lostlink_immune` | same, two-report rule | simulated | preconditions met, not reproduced |
| `triage_board` | hospital emergency department | simulated | reproduced |
| `real/usgs_…` | earthquake advisories | recorded USGS events and revisions | reproduced; the real-data genome also reproduces back on `port_alert` |
| `examples/service_demo` | approvals HTTP service | simulated, driven over HTTP | reproduced against real request handling |

Claim ceiling: EMPIRICALLY_VALIDATED on the scenarios listed, on one machine. Transfer is across scenarios and one example service sharing the Event v1 substrate; it has not been shown across arbitrary third-party codebases.

## Features

- Deterministic virtual clock, forkable seeded PRNG, hash-chained append-only event logs; same inputs give byte-identical digests.
- Four notions of time that are never merged: event time, receipt time per role, derivation time, simulated-truth time.
- Snapshot and branch with an identical replayed prefix.
- `AS_KNOWN_AT(t)` and `AS_OBSERVED_AT(t)` queries per role.
- Safe predicate DSL for decision rules (no `eval`).
- Seven intervention operators and mutators; schedules that violate the time rule are recorded as rejected, never hidden.
- Three-valued invariant checker with minimal witnesses; UNKNOWN names the missing signal.
- Guided versus random exploration on one budget; the guided strategy reads only the scenario, never runtime truth.
- Delta debugging with an independent 1-minimality witness, and an analytic flip threshold for single-delay causes.
- Equivalence reduction that skips worlds the checker cannot tell apart, a decision-window strategy that finds decision failures at the first branch, and optional parallel runs; all deterministic.
- Twelve declared invariants: temporal response, label isolation, no future knowledge, decision consistency with truth, fault-attributed witnesses, latency bounds, causal ordering, duplicate suppression, independent-source quorum, permitted-only knowledge.
- Scenario JSON Schema with `worldline validate` (path and line, did-you-mean suggestions) and `worldline init` scaffolds; authoring tutorial in docs/AUTHORING.md.
- Real-data adapter (USGS) with pinned snapshots and provenance; HTTP adapter that drives a real ASGI service.
- FailureGenome extraction, preconditions, and cross-scenario challenge with negative control.
- Typed evidence records, Ed25519-sealed bundles with distinct tamper findings (edited, dropped, reordered, wrong key, truncated), policy-based authorization, assumption revocation.
- A kernel that recomputes agent claims before attesting them.
- Viewer: a seven-step Story in plain language with a working release verifier, and Inspect views that each answer one question (which worlds broke, what each role knew when, the single cause, why the release was allowed).
- Public verifier service on Cloud Run with structured logging, security headers, rate limits, readiness probe, and a release-verify endpoint; runbook in docs/OPERATIONS.md.
- Documentation site built from the repository and published under /docs; Playwright end-to-end and axe accessibility tests with a performance budget.
- GitLab CI pipeline: test, explore, kernel-verify, authorize, release, pages; kernel jobs isolated by protected refs and CODEOWNERS.
- Proof-carrying releases: every tag ships the signed evidence bundle and the kernel's authorization as release assets.
- SAST, secret detection and dependency scanning from GitLab's managed templates.

## Tech stack

Python 3.12, PyYAML, Typer, Rich, cryptography (Ed25519); pytest and Hypothesis. Viewer: Vite 6, React 18, TypeScript 5, hand-written CSS and SVG, no UI kit. uv for environments. GitLab CI and GitLab Duo Agent Platform.

## Getting started

Prerequisites: Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). Node 20 or newer only for the viewer.

Install as a tool (releases publish to the project's package registry; see [docs/INSTALL.md](docs/INSTALL.md)):

```bash
uv tool install --index https://gitlab.com/api/v4/projects/88121109/packages/pypi/simple worldline
worldline --version
worldline resources        # where the bundled scenarios, invariants and policy live
```

Or from source:

```bash
git clone https://gitlab.com/shafir2/WorldLine.git
cd WorldLine
uv sync --extra dev
uv run pytest -q
uv run worldline demo --out experiments/out --budget 150 --seed 42
```

The demo ends with `AUTHORIZED` followed by a `DENIED` on a deliberately tampered bundle, and leaves every artifact in `experiments/out/`.

Viewer:

```bash
cd ui
npm ci
npm run build      # copies ../experiments/out into dist/data
npm run preview
```

No environment variables are needed locally. CI needs one protected variable, `WORLDLINE_KERNEL_KEY`, holding the Ed25519 private key produced by `uv run worldline keygen`; the matching public key is committed at `policies/kernel_public.pem`.

## Usage

```bash
# one world
uv run worldline run scenarios/port_alert.yaml --out experiments/out

# many worlds, both strategies, writes exploration.json and traces of failing branches
uv run worldline explore scenarios/port_alert.yaml --budget 200 --seed 42 --out experiments/out

# reduce the chosen failing schedule to a 1-minimal one, with causal diff against root
uv run worldline minimize experiments/out/exploration.json --out experiments/out

# abstract the mechanism, then challenge another scenario (immune control picked up automatically)
uv run worldline genome extract experiments/out/minimal_schedule.json --out experiments/out/genome.json
uv run worldline genome challenge experiments/out/genome.json scenarios/convoy_lostlink.yaml --seed 42 --out experiments/out/challenge.json

# agent-side evidence, then the independent kernel
uv run worldline report experiments/out --code-commit $(git rev-parse HEAD)
uv run worldline verify experiments/out --signing-key policies/kernel_private.pem --public-key policies/kernel_public.pem --out experiments/out/bundle.json
uv run worldline authorize experiments/out/bundle.json --policy policies/release.yaml --public-key policies/kernel_public.pem --artifact-digest $(git rev-parse HEAD) --out experiments/out/authorization.json

# show that invalidating an assumption revokes authorization
uv run worldline authorize experiments/out/bundle.json --policy policies/release.yaml --public-key policies/kernel_public.pem --artifact-digest $(git rev-parse HEAD) --invalidate-assumption "bounded horizon" --out /tmp/revoked.json

# a scenario whose truth stream is real USGS data (offline from the committed snapshot; drop --offline to re-fetch)
uv run worldline real usgs --start 2025-07-29T23:20:00Z --end 2025-07-30T01:40:00Z --minmag 5.5 --offline

# the same root/explore/minimize/genome-challenge pipeline against a real FastAPI service (needs --extra service)
uv run worldline http demo --budget 60 --seed 42 --out experiments/out_http

# benchmark guided vs random over seeds
uv run python experiments/benchmark.py --seeds 5 --budget 150 --out experiments/out/benchmark.json
```

Real data: `scenarios/real/` holds a scenario whose events, magnitudes and revision times are real USGS records, while its roles, latencies and decision rule are labelled models. See [docs/REAL_DATA.md](docs/REAL_DATA.md) for provenance, results and the claim ceiling.

Writing a scenario: copy `scenarios/port_alert.yaml`. Roles declare delivery latency, jitter, ordering and visible policy labels. Actors declare decision rules in the predicate DSL, for example `count(kind='sighting', payload.confidence>=0.6, not_corrected) >= 2`. Invariants live in `invariants/core.yaml`.

## Project structure

```
worldline/
  core/            Event v1, hash-chained EventLog, VirtualClock, DeterministicRNG
  worlds/          scenario grammar, deterministic runner, snapshot/branch, views, predicate DSL
  interventions/   delay/drop/duplicate/reorder/corrupt/revoke/clock_skew, mutators
  invariants/      three-valued checker with witnesses
  explorer/        explore (guided vs random), ddmin, minimize, causal_diff
  genome/          FailureGenome extract and challenge
  evidence/        typed records, sealed Bundle, Policy, authorize, kernel recompute
  adapters/        real-data adapters (usgs: USGS FDSN events -> scenario with provenance)
                   http/: drive a real ASGI service with truth + fault schedules (docs/REAL_SERVICE.md)
  cli/             `worldline` command
examples/service_demo/  example FastAPI approvals service the http adapter runs against
service/           FastAPI public verifier (Cloud Run): viewer + /api/verify + /api/authorize
scenarios/         port_alert, convoy_lostlink, convoy_lostlink_immune (negative control); real/ (USGS-derived)
data/real/usgs/    pinned USGS snapshot with provenance (source URL, fetch time, SHA-256, public domain)
invariants/        core.yaml
policies/          release.yaml, kernel_public.pem
experiments/       benchmark.py; out/ is generated and ignored
ui/                Vite + React viewer
tests/             342 tests
docs/              ARCHITECTURE, THESIS (prior art), REPORT (measurements), ROADMAP, GitLab setup, Duo agent prompts
.gitlab-ci.yml     test -> explore -> kernel-verify -> authorize -> release -> pages
.gitlab/ci/        security.yml (SAST, secret detection, dependency scanning), gcp.yml (deploy-gcp)
AGENTS.md, CODEOWNERS, .gitlab/duo/   agent guidance and protected paths
```

## Architecture

```mermaid
flowchart LR
  S[scenario.yaml] --> R[worlds.run<br/>virtual clock, per-role delivery]
  R --> T[Trace: truth, views, decisions]
  T --> C[invariants.check<br/>PASS / FAIL / UNKNOWN + witness]
  I[interventions<br/>FaultSchedule] --> R
  C -- FAIL --> D[explorer.ddmin<br/>1-minimal schedule]
  D --> G[genome.extract<br/>structural selectors + preconditions]
  G --> H[genome.challenge<br/>scenario B + immune control]
  T & C & D & H --> E[agent evidence<br/>EMPIRICALLY_VALIDATED / FAILED / UNKNOWN]
  E --> K[kernel: recompute, ATTEST, seal Ed25519]
  K --> A[authorize artifact digest<br/>AUTHORIZED / DENIED]
```

Trust boundary: everything left of the kernel may be produced or modified by agents. The kernel reads only the committed public key, the policy, and the bundle, recomputes verdicts from traces, and is the sole producer of ATTESTED records. `invariants/`, `policies/`, `worldline/evidence/` and the kernel CI jobs are protected by CODEOWNERS. Full contracts in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); thesis and prior art in [docs/THESIS.md](docs/THESIS.md).

## GitLab Duo integration

The repository ships three custom Duo agents and three flows, defined as text in [docs/duo/](docs/duo/) and created in the project's AI catalog by `scripts/duo_setup.py`:

| Agent / flow | Trigger | Role |
|---|---|---|
| Investigator / WORLDLINE Investigate | work item, mention | Re-runs the exploration on a GitLab-hosted runner, checks determinism, minimizes, posts the witness and genome on the issue. Read-only. |
| Repairer / WORLDLINE Repair | mention | Proposes a fix as a merge request under the repair contract in [AGENTS.md](AGENTS.md): the product scenario must pass under the specimen's minimal fault schedule, the specimen must still fail, no invariant may be weakened, and the non-default decision must remain reachable. |
| Skeptic / WORLDLINE Skeptic Review | merge request | Reviews a merge request for claims not backed by a test, digest or verdict file, for protected-path edits, and for vacuous repairs. |

Agents never evaluate their own work. `invariants/`, `policies/`, `worldline/evidence/` and the kernel CI jobs are protected by CODEOWNERS; the `kernel-verify` and `authorize` stages decide. Example run: [issue 1](https://gitlab.com/shafir2/WorldLine/-/work_items/1) and [merge request 1](https://gitlab.com/shafir2/WorldLine/-/merge_requests/1), with the agent runs linked from their notes. Setup: [docs/GITLAB_SETUP.md](docs/GITLAB_SETUP.md).

## Testing

```bash
uv run pytest -q                 # 342 tests: unit, property, integration, CLI, service, adapters
uv run pytest -q tests/test_evidence.py   # tamper findings, agent cannot self-attest, revocation
cd ui && npm run build           # type-checks with strict TypeScript
```

Pipeline: alongside the WORLDLINE jobs, the `test` stage runs GitLab SAST, secret detection and dependency scanning, included from `.gitlab/ci/security.yml`. Findings appear under Secure > Vulnerability report.

## Deployment

**Public verifier on Google Cloud Run**, live at https://worldline-verifier-301330323690.europe-north1.run.app. `service/` is a FastAPI application that serves the viewer and exposes the authorization kernel as a public API holding only the public key: `POST /api/verify` returns the tamper findings for any bundle, `POST /api/authorize?artifact_digest=…` returns the policy decision for an exact artifact, `GET /api/evidence` returns the deployed release's bundle and authorization. Anyone can check a release without trusting the pipeline that produced it. Try it against the current release bundle:

```bash
curl -sL https://gitlab.com/api/v4/projects/88121109/packages/generic/worldline-evidence/v0.1.3-transcend/bundle.json -o bundle.json
curl -s -X POST -H 'Content-Type: application/json' --data-binary @bundle.json \
  'https://worldline-verifier-301330323690.europe-north1.run.app/api/authorize?artifact_digest=1bfce46216da6cf05085684a26f7074b9cbf6ad9'
```

Build and deploy:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud run deploy worldline-verifier --source . --region europe-north1 --allow-unauthenticated
```

The manual `deploy-gcp` CI job does the same from a protected ref once `GCP_PROJECT` and `GCP_SA_KEY` are set. Details and IAM roles: [service/README.md](service/README.md).

**Releases.** Every `v*` tag produces a GitLab release whose assets are the signed evidence bundle and the kernel's authorization for that exact commit, together with the genome, minimal schedule, challenge result and viewer index. A tag the kernel denies never becomes a release. The first one is [v0.1.3-transcend](https://gitlab.com/shafir2/WorldLine/-/releases/v0.1.3-transcend): AUTHORIZED, 14 of 14 requirements, assets downloadable without signing in. Anyone can re-check a release offline with `uv run worldline authorize` and the public key committed at that tag. Cutting and verifying releases: [docs/RELEASES.md](docs/RELEASES.md).

The `pages` CI job builds the viewer and publishes it with the pipeline's own `experiments/out` as data, so the published page shows what the pipeline actually computed. The viewer is static and can also be served from any bucket or Cloud Run container; there is no backend.

## Evidence and claim ceilings

| Claim | Kind | Basis |
|---|---|---|
| Same inputs give identical trace digests | EMPIRICALLY_VALIDATED | tests incl. subprocess re-run |
| Views never contain information before receipt | EMPIRICALLY_VALIDATED | `epistemic.no_future_knowledge` on every trace, plus tests |
| Minimal schedules are 1-minimal | EMPIRICALLY_VALIDATED | independent `one_minimal_check` on every minimization |
| Guided exploration finds more failing worlds than random | EMPIRICALLY_VALIDATED | 5 seeds, one scenario, one machine |
| Genome transfers between scenarios | EMPIRICALLY_VALIDATED | one positive scenario, one immune control |
| Tampered or agent-forged evidence cannot authorize | EMPIRICALLY_VALIDATED | tests and demo |
| Anything about real deployments or heterogeneous codebases | UNKNOWN | not attempted |

Nothing here is PROVED_WITHIN_MODEL yet. All scenarios are synthetic and labelled as such.

## Roadmap and limitations

Limitations now: one mechanism class drives the headline even though it now reproduces in four domains; transfer is within the Event v1 substrate and one example service, not across arbitrary codebases; the real-data scenario's delivery latencies and decision rule are models; equivalence reduction is sound for the declared invariants but not a full partial-order reduction; parallel runs gain little on short branches. The roadmap, with the experiment that would settle each item, is in [docs/ROADMAP.md](docs/ROADMAP.md). A first run against real request-handling code (an example service, with WORLDLINE still driving delivery timing) is described in [docs/REAL_SERVICE.md](docs/REAL_SERVICE.md).

## Contributing

Open an issue with a scenario that you believe should fail and does not, or a genome that reproduces where it should not. Pull requests must keep `uv run pytest -q` green and must not touch `invariants/`, `policies/` or `worldline/evidence/` without a CODEOWNERS review.

## License

MIT. See [LICENSE](LICENSE).

## Author

shafir-01
