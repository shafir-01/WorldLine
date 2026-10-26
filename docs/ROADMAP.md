# WORLDLINE roadmap beyond the hackathon

The hackathon artifact is laboratory one. This is the research program it opens. Each item names the question, the experiment that would settle it, and what would count as failure.

## Near term (weeks)

- **Partial-order reduction in the explorer.** Many fault schedules are equivalent up to commuting independent deliveries. Implement DPOR-style sleep sets over per-role delivery events. Settled by: branches explored to find the same failures drops by a measured factor on `port_alert`. Fails if equivalence detection misses a failing world found by exhaustive search on a small scenario.
- **Held-out scenario corpus.** Ten scenarios in three domains (port security, convoy logistics, hospital triage) built by someone who has not seen the genomes. Measure genome precision and recall. Fails if recall on scenarios meeting stated preconditions is below the random-intervention baseline.
- **Immune variants.** For each genome, a scenario engineered to meet preconditions but to be immune (e.g. decision rule requires explicit acknowledgement of corrections). The genome must report "preconditions met, not reproduced", never a false FAIL.
- **Information-flow invariants through derivation.** Extend `absence.label_isolation` from direct events to aggregates, counts, and cached projections (PolicyProof lineage). Fails if a labelled datum influences a count visible to an unauthorised role without a FAIL.

## Medium term (months)

- **Symbolic time.** Replace concrete delay sweeps with constraint solving over receipt-time intervals: find the delay window that flips a decision in one solver call. Baseline: the current sweep.
- **Clock uncertainty (TimeSkew).** Model each source's clock as rate, offset and jitter; reconstruct partial orders; invariants over the reconstruction envelope rather than a forced total order.
- **Genome transfer across code, not just scenarios.** Instrument a real service (e.g. a small FastAPI app with a cache) with the Event v1 adapter; ask whether a genome extracted from the simulator reproduces against the instrumented app. This is the real test of the WORLDLINE hypothesis; the hackathon does not answer it.
- **Agent-session evidence.** Capture GitLab Duo session transcripts as evidence records with producer `agent:<name>`, so an authorization can show exactly which agent actions preceded which verifications.
- **Statistical evidence kind.** Add confidence intervals and n to EMPIRICALLY_VALIDATED records; refuse claims below a sample threshold.

## Long term (research)

- **A verification calculus for epistemic properties.** Relational (two-trace) properties such as "role R's decisions are identical in any two worlds that differ only in information R is not permitted to see" (a non-interference statement over decisions). Requires hyperproperty checking.
- **Self-improving exploration.** Learn which mutator families produce failures per scenario shape; measure information gained per oracle call; guard against the explorer weakening its own guarantees by keeping the checker frozen and independently versioned.
- **Federated genome library.** Share genomes, never traces or source, across participating projects. Open question: can a genome leak scenario-specific information? Needs a privacy analysis before any sharing.
- **Compositional guarantees.** Given invariants proven per component within models, derive which system-level invariants follow and where composition breaks them; the explorer should target exactly those seams.

## Explicitly not planned

- Live operational military systems, weapon functions, classified data, or any claim of certification or accreditation. Everything stays synthetic or authorised.
