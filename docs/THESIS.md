# WORLDLINE: thesis, prior art, and claims

## The problem

A change to a software system is usually judged against one world: the test suite's. Tests pass, the pipeline is green, an agent writes a confident explanation, and the release ships. The failures that matter rarely live in that world. They live in the world where the correcting report arrived ninety seconds later, where the permission was revoked mid-flight, where two correct components composed into an incorrect one. Nothing in a conventional pipeline asks about those worlds, and nothing distinguishes "the tests passed" from "this is known to hold".

WORLDLINE treats a software system together with its environment as an object that can be run in many worlds at once, and treats evidence about it as something that must be typed by how it was obtained.

## The contribution, stated so it can be falsified

1. **Counterfactual branching with causal minimization.** Given a deterministic scenario and a budget of environmental interventions (delay, drop, duplicate, reorder, corrupt, revoke, clock skew), WORLDLINE explores alternative worlds, detects guarantee violations with a three-valued checker, and reduces any failing world to a 1-minimal set of interventions with a witness. *Falsified if:* a random-schedule baseline with the same budget finds the same failures at the same rate, or if the minimal schedule is not 1-minimal under independent re-check.

2. **Epistemic separation as a checkable invariant.** Simulated truth, each role's received observations, and evaluator conclusions are separate stores with separate clocks. "A decision was made on information that had not yet arrived" is a first-class, witnessed FAIL, not an after-action anecdote. *Falsified if:* any view contains an event before its receipt time, or if a decision's recorded knowledge set exceeds what its role had received.

3. **Portable failure mechanism (within one substrate).** The minimal schedule is abstracted from concrete event ids to schema-level selectors and preconditions, forming a FailureGenome. Applied to a second scenario with a different domain, actors, and decision rules, it instantiates concrete interventions and either reproduces an analogous violation or reports that preconditions were not met. *Falsified if:* the genome reproduces on scenarios that were engineered to be immune (false positive), or fails on a held-out scenario built to the stated preconditions (false negative). *Claim ceiling:* transfer is across scenarios sharing the Event v1 substrate. It is not transfer across heterogeneous codebases. That remains the open research question, and we say so.

4. **Typed evidence and an independent authorization kernel.** Every claim carries one of PROVED_WITHIN_MODEL, EMPIRICALLY_VALIDATED, ATTESTED, FAILED, UNKNOWN. Evidence is hash-chained and Ed25519-sealed. Only the kernel may emit ATTESTED, and only after recomputing what agents reported. Release authorization binds an exact artifact digest to a policy over evidence kinds, and is revoked when a listed assumption is invalidated or evidence goes stale. *Falsified if:* an agent-produced record can satisfy an ATTESTED requirement, if a one-byte edit to the bundle verifies, or if stale or wrong-subject evidence authorizes.

## Prior art, and what is reused versus new

| Area | Prior work | What WORLDLINE reuses | What is different here |
|---|---|---|---|
| Deterministic simulation testing | FoundationDB simulation; Antithesis; Jepsen; TLA+/TLC model checking | Deterministic virtual clock, seeded PRNG, fault injection at the delivery layer | Branching from snapshots inside one run; three information states kept apart; minimization and abstraction as first-class outputs |
| Minimization | Zeller & Hildebrandt, delta debugging (ddmin) | ddmin directly | Applied to fault *schedules* with an independent 1-minimality witness recorded as evidence |
| Partial-order reduction, DPOR | Flanagan & Godefroid 2005 | Not yet; listed as roadmap for the explorer | |
| Epistemic logic, knowledge in distributed systems | Halpern & Moses 1990; Fagin et al., *Reasoning About Knowledge* | The distinction between what happened and what each agent knows | Made executable as AS_KNOWN_AT / AS_OBSERVED_AT queries and a runtime invariant with witnesses |
| Bitemporal data | Snodgrass; SQL:2011 temporal tables | event_time vs received_time | Per-role receipt rather than one system time |
| Information-flow control, non-interference | Denning 1976; Goguen & Meseguer 1982 | Label isolation as an absence invariant | Checked over traces, not types; limited to declared labels (claim ceiling) |
| Proof-carrying code | Necula 1997 | The idea that an artifact carries checkable evidence | Evidence is heterogeneous and typed by certainty; mostly empirical, explicitly not proofs |
| Supply-chain attestation | in-toto; SLSA; Sigstore | Signed, hash-chained attestations bound to artifact digests | Attestations are gated by *recomputation*; agent self-reports can never reach ATTESTED |
| Transparency logs | Certificate Transparency; Trillian | Hash chain with signed checkpoints | Small, offline, single signer (claim ceiling) |
| Metamorphic and property-based testing | Chen et al. 1998; QuickCheck; Hypothesis | Property-style oracles | Properties are temporal and epistemic over multi-role traces |
| Chaos engineering | Netflix Chaos Monkey; Gremlin | Fault vocabulary | Deterministic, replayable, minimized, and tied to a specific violated guarantee |
| Autonomous coding agents | GitLab Duo Agent Platform, Devin, SWE-agent | Agents propose investigations and repairs | Agents never evaluate themselves; the kernel is outside their write set |

Novelty claim, kept modest: WORLDLINE is a *distinct integration* of deterministic branching simulation, epistemic invariants, delta debugging, mechanism abstraction, and recomputation-gated attestation into one substrate, with the whole pipeline executed inside a GitLab DevSecOps lifecycle. No single component is new. The combination, the epistemic invariant with witnesses, and the FailureGenome abstraction-and-challenge loop are the parts we believe are new as implemented; we have not done a formal literature review sufficient to claim more.

## The defining experiment

Scenario A (`port_alert`): a port security incident with three roles at different latencies and a duty-officer decision rule. The baseline run decides "monitor" because a correction downgrading a sighting arrives in time. WORLDLINE explores interventions, finds worlds where the officer escalates on stale information, minimizes to a single intervention (delay the correction past the decision window), and emits a FAIL for `decision.consistent_with_truth` with the missing truth events as witness.

The genome is extracted: *delay the correcting report for a decision that depends on a correctable observation past the decision window*. Scenario B (`convoy_lostlink`) has a different domain, different roles, different rules, built without reference to the delay. The genome's preconditions are evaluated against B; the mechanism is instantiated; the challenge reproduces (or does not; both outcomes are recorded).

Baseline comparison: random fault schedules at the same budget; metrics are failing worlds found per 100 branches and oracle calls to a 1-minimal schedule. Numbers appear only in `experiments/out/benchmark.json`, written by the code that ran.

A GitLab Duo agent is then asked to repair: the repair must make the failing branch PASS without editing invariants or the kernel, and must add a regression scenario. The kernel recomputes, seals, and authorizes the exact commit digest. A second pipeline shows a stale or mismatching evidence bundle being DENIED.

## Success levels we claim at submission

- Level 1 (functional invention): claimed if the pipeline runs end to end from a clean clone.
- Level 2 (demonstrated advantage): claimed only if guided exploration beats random in the recorded benchmark.
- Level 3 (generalization): partially, if the genome reproduces on scenario B and does not on an immune variant.
- Level 4 to 6: not claimed. Roadmap in `docs/ROADMAP.md`.
