# WORLDLINE technical report: measurements, limitations, open questions

Companion to [THESIS.md](THESIS.md) (problem, contribution, prior art) and [ARCHITECTURE.md](ARCHITECTURE.md) (contracts). This document records only what was measured. Regenerate every number with the commands given; nothing here was typed from expectation.

## Environment

Windows 11 10.0.26300, AMD64, CPython 3.12.15, uv 0.13.5. Wall times are for this machine and are not portable.

## Experiment 1: counterfactual exploration, guided vs random

Command: `uv run python experiments/benchmark.py --seeds 5 --budget 150 --out experiments/out/benchmark.json`

Scenario `port_alert`, schedule size 3, budget 150 candidate schedules per strategy. "Failing" means a branch has a FAIL on an invariant that PASSes on the root trace. Branches are deduplicated by schedule hash, and schedules violating the time rule (receipt before event) are recorded as rejected, which is why node counts are below 151.

| seed | strategy | branches run | failing | failing per 100 | first `decision.consistent_with_truth` FAIL at index | wall ms |
|---|---|---|---|---|---|---|
| 1 | guided | 64 | 22 | 34.9 | 1 | 255 |
| 1 | random | 71 | 18 | 25.7 | 18 | 278 |
| 2 | guided | 76 | 36 | 48.0 | 2 | 292 |
| 2 | random | 78 | 24 | 31.2 | 19 | 301 |
| 3 | guided | 47 | 22 | 47.8 | 3 | 213 |
| 3 | random | 87 | 30 | 34.9 | 29 | 328 |
| 4 | guided | 54 | 26 | 49.1 | 2 | 239 |
| 4 | random | 65 | 24 | 37.5 | 2 | 262 |
| 5 | guided | 71 | 30 | 42.9 | 9 | 280 |
| 5 | random | 78 | 18 | 23.4 | 2 | 299 |

Means: guided 44.5 failing per 100 (median 47.8), random 30.5 (median 31.2). First decision failure index: guided mean 3.4, random mean 14.0. Guided ran fewer distinct branches because its front-biased sampling repeats schedules more often; those repeats are deduplicated and counted against it in the rate, not hidden.

What this does and does not show. With n = 5 seeds on one scenario, the direction is consistent across every seed for the failing rate, and in 4 of 5 seeds for time-to-first-decision-failure (seed 5 is a tie at index 2 vs 9 in random's favour). No confidence intervals are reported because the sample is too small to make them meaningful; the honest statement is "consistent direction, small sample, one scenario". A second scenario family and 30+ seeds are needed before claiming an effect size.

## Experiment 2: minimization

Five failing schedules of size 3 or 4 from the guided runs (seeds 1 to 5) minimized against `decision.consistent_with_truth`.

| seed | original size | minimal size | oracle calls | 1-minimal verified | minimal intervention |
|---|---|---|---|---|---|
| 1 | 4 | 1 | 3 | yes | corrupt `correction-001.payload.corrects` |
| 2 | 3 | 1 | 2 | yes | drop `correction-001` to `harbour_ops` |
| 3 | 4 | 1 | 4 | yes | revoke `harbour_ops` feed from 1350 s |
| 4 | 3 | 1 | 4 | yes | corrupt `ack-001.payload.acknowledges` |
| 5 | 4 | 1 | 4 | yes | drop `correction-001` |

Every case reduced to one intervention and passed the independent 1-minimality check (the minimal schedule fails; every single-removal variant passes). The five mechanisms are four distinct failure classes of the same decision rule: lose the correction (drop, corrupt its reference), cut the role's feed before the decision, and break the acknowledgement chain. Separately, the demo run (seed 42) minimized a 3-intervention schedule to "delay `correction-001` past the decision", and random exploration found a fifth class, duplication of a sighting, which flips the decision because the rule lacks de-duplication.

## Experiment 3: genome transfer with negative control

Command: `uv run worldline demo --budget 150 --seed 42`, challenge step.

Genome `genome:94f0ad69dd5483d0`, mechanism "delay the last before-decision `correction` event (corrects a dependency) seen by the deciding role until after the decision", violated invariant `decision.consistent_with_truth`, preconditions: `has_decision_rule`, `decision_rule_depends_on_a_corrected_kind`, `exists_kind:correction:with_payload_field:corrects`, `event_received_by_decision_role_before_decision`.

| target scenario | preconditions met | instantiated as | reproduced |
|---|---|---|---|
| `convoy_lostlink` | yes | delay `retract-001` to `ops_center` by 111.46 s | **yes**: watch officer chose `reroute`, truth as of 1200 s says `continue` |
| `convoy_lostlink_immune` | yes | delay `retract-001` to `ops_center` | **no**: rule needs two credible reports; one is present either way |

The immune variant is identical to `convoy_lostlink` except for the threshold in the reroute rule. It was written to meet the preconditions and be immune, so the genome's "instantiated, not reproduced" outcome is the correct negative. This is one positive and one negative case. It is not a generalization claim; it is evidence that the abstraction machinery distinguishes at least one susceptible structure from one immune structure in a domain it was not extracted from.

The witness on `convoy_lostlink` lists five truth events missing from `ops_center`'s view at decision time: four scout telemetry events (not visible to ops by policy label) and `retract-001` (the delayed retraction). Only the last is caused by the intervention; the invariant's witness reports all missing information, not only the fault-caused subset. That is a design choice worth revisiting: a "fault-attributed witness" (set difference against the root trace's missing set) would be sharper. The causal diff already computes it.

## Experiment 4: evidence kernel and authorization

Command: demo run, steps `report`, `verify`, `authorize`.

- Agent side emitted 15 records, all EMPIRICALLY_VALIDATED or FAILED or UNKNOWN; none ATTESTED (attempting that raises at `Bundle.add`).
- Kernel recomputed root verdicts, minimal-schedule verdicts, both exploration strategies, the genome challenge and controls, 1-minimality, and the JUnit totals; 16 ATTESTED records, 0 mismatches. Bundle sealed, 31 records, verification `ok=True`.
- `authorize` for the exact commit digest: AUTHORIZED, 5 of 5 requirements.
- A one-record edit to the bundle: DENIED with finding `integrity:record_edited@1`.
- Tests additionally cover: dropped record, reordered records, wrong key, truncated tail, unsealed tail, stale evidence (max age), wrong artifact digest, downgraded kind, FAILED invariant, forged agent verdict (becomes `integrity:agent_report_mismatch`, DENIED), and assumption revocation flipping AUTHORIZED to DENIED.

Claim ceiling: the kernel protects against agents misreporting what the pipeline computed and against bundle tampering. It does not protect against a compromised CI runner or a stolen signing key; those are the standard supply-chain assumptions and are listed as such.

## Experiment 5: the agent loop on GitLab (observed, not designed)

Three custom Duo agents and three flows were created through the catalog API and enabled on the project. Observed sequence on issue 1:

1. Investigator session (hosted, Claude Sonnet 5.5 via Vertex, image python:3.12): ran `explore` twice, confirmed byte-identical trace digests, minimized a 3-intervention schedule to the delayed correction, posted a report with exact witnesses and digests. Correct in every checkable detail.
2. Repairer session: opened a merge request with a grace-window rule in a regression scenario, five tests including a non-vacuity test against the original scenario, no protected path touched, and an honest limitations section. All 163 tests pass.
3. GitLab's built-in Code Review flow requested changes: with the chosen cutoff the rule can never produce `escalate` for that inject set, so the PASS results demonstrate a constant rule, not a correct one. The finding is right. Neither the test suite nor the kernel would have caught it: tests only asserted the default option, and the kernel recomputes claims, it does not judge whether a repair is meaningful.
4. A follow-up Repairer session was dispatched with the review to add an escalation-reachability variant.

What this shows. The kernel stops agents from misreporting what ran. It does not stop a repair from being vacuous. That gap belongs to the Skeptic role and to a stronger repair contract: a repair must come with a scenario where the non-default decision is still reachable. The contract in AGENTS.md should be amended accordingly. Our own Skeptic flow failed to start under the merge-request trigger because its definition read an input that trigger does not supply; it ran correctly when started with an explicit goal.

## Experiment 6: first kernel run on the GitLab runner

The first main-branch pipeline with the signing key present (pipeline 2991432180) passed test, explore, pages and kernel-verify, then **authorize DENIED** the commit: `missing: no acceptable record for 'tests:unit'`, `downgraded: tests:unit is UNKNOWN < EMPIRICALLY_VALIDATED`. Cause: the test job published `report.xml` only as a JUnit report, which GitLab does not hand to downstream jobs as a file, so the kernel could not recompute the test claim and typed it UNKNOWN. The policy requires EMPIRICALLY_VALIDATED for that claim, so the release was refused. This is the intended behaviour of the kernel under a missing signal and was fixed by publishing the file as a regular artifact. Both outcomes are in the pipeline history.

## Determinism

Same scenario, seed and schedule produce byte-identical trace digests in-process and across a fresh interpreter (tested by subprocess). Root `port_alert` digest with seed 20261026: `sha256:d16ac439…`. Any change to delivery policy, rule, or inject changes it.

## Limitations

1. Two scenarios, one dominant invariant family. The headline is one mechanism class.
2. Transfer is between scenarios on the shared Event v1 substrate. The question that matters most for the WORLDLINE hypothesis, transfer to an instrumented real service, is untested (see ROADMAP).
3. No partial-order reduction; many explored branches are equivalent.
4. Delay values come from a fixed menu and from "past the decision plus margin"; a solver would find the flip window directly.
5. Witnesses report all missing information at decision time, not only the fault-attributed subset.
6. Benchmark sample is 5 seeds on one machine; no intervals.
7. Nothing is PROVED_WITHIN_MODEL. All evidence is empirical.
8. Duo agent sessions are not yet part of the recorded evidence (producer `agent:<name>` records exist in the schema; wiring the session transcript is roadmap).

## Open questions worth a researcher's time

- What is the right abstraction level for a genome so that recall on held-out susceptible scenarios stays high while false positives on immune variants stay near zero? Current preconditions are hand-designed.
- Can the `decision` invariant be generalized to a relational property: "the decision is invariant under any world that differs only in information the role is not permitted to see"? That is a hyperproperty and needs two-trace checking.
- Does guided exploration's advantage survive when decision rules are learned or opaque rather than declared?
