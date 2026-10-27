<!-- WORLDLINE repair. One invariant per MR. The Skeptic Review flow reads this description; the
     mr-evidence job posts the pipeline's verdicts as a note. Neither replaces the kernel. -->

## Repair: `<invariant_id>@<version>`

Closes #<issue>

- **Witness before:** [`<event-id>`, ...] on branch `<branch_id>`, trace `sha256:<before digest>`
- **Mechanism:** <one sentence, from the genome>
- **Change:** <what, where, why: a decision rule, a delivery policy, required signals, or runner code>
- **Product copy:** `scenarios/product/<file>.yaml` (specimen `scenarios/<file>.yaml` untouched)
- **Regression scenario:** `scenarios/regressions/<invariant>_<short>.yaml`
- **After:** `<invariant>` PASS on the product copy under the same seed and minimal schedule, trace `sha256:<after digest>`; `uv run pytest -q`: <n> passed
- **Reachability:** <the variant or test in which the repaired rule still chooses its non-default option>
- **Evidence kind:** `EMPIRICALLY_VALIDATED`. Claim ceiling: this scenario family, this seed, this horizon. The kernel re-derives everything; this MR does not attest.

## Repair contract (AGENTS.md)

- [ ] **Product, not specimen:** the failing branch yields PASS on `scenarios/product/<file>.yaml`; the specimen is not edited
- [ ] **Specimen still fails:** a test shows the specimen still FAILs under the same schedule, so the test case was not deleted
- [ ] **No weakening:** `invariants/`, `policies/`, `worldline/evidence/` untouched; no window widened, no condition removed, no FAIL turned into UNKNOWN in `worldline/invariants/`
- [ ] **Regression:** `scenarios/regressions/<invariant>_<short>.yaml` with the concrete minimal schedule, plus a test asserting PASS
- [ ] **Tests:** `uv run pytest -q` passes; no test deleted, skipped or xfailed
- [ ] **Description:** invariant id and version, witness before, mechanism, change, claim ceiling (never `ATTESTED`)
- [ ] **Determinism:** no wall-clock time, no randomness outside `DeterministicRNG`, no LLM call in the decision path
- [ ] **Non-vacuity:** the repaired rule still reaches its non-default option (for example `escalate`) when the evidence supports it; a rule that can no longer reach it has been disabled, not repaired
- [ ] **Not a deletion:** the fault inject was not removed or softened so that the failure cannot occur

## Review

- [ ] WORLDLINE Skeptic Review note posted (`## Skeptic review: PASS`)
- [ ] GitLab Duo Code Review read and its threads resolved
- [ ] `mr-evidence` note shows the expected verdicts; the kernel decides on `main` after merge

/label ~"worldline:repair"
