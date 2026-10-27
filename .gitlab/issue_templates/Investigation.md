<!-- WORLDLINE investigation. One invariant, one finding. The investigation-issue CI job fills this
     in automatically from the explore output; use this template when you open one by hand. -->

## Counterfactual finding

- **Scenario:** `scenarios/<file>.yaml` (seed `42`, budget `200`)
- **Invariant:** `<invariant_id>@<version>` is **FAIL** (or UNKNOWN, with the missing signal)
- **Decision affected:** `<actor>/<rule_id>` chose `<option>` where truth gives `<option>`
- **Source:** pipeline <link>, commit `<sha>`

### Witness

Event ids exactly as in the VerdictRecord. For `decision`, these are the truth events the actor had not received at decision time.

`<event-id>`, `<event-id>`

### Minimal fault schedule

1-minimal, from `experiments/out/minimal_schedule.json`:

```json
[]
```

In plain English:

1. <e.g. Delay `correction-001` by 300 s.>

### Failure genome

- Id: `genome:<id>`, evidence kind `EMPIRICALLY_VALIDATED` on the origin scenario only
- Mechanism: <mechanism_sentence>
- Challenge: <reproduced on / did not reproduce on, and the controls>

### Reproduce

```
uv sync --extra dev
uv run worldline explore scenarios/<file>.yaml --budget 200 --seed 42 --out experiments/out
uv run worldline minimize experiments/out/exploration.json --out experiments/out
uv run worldline genome extract experiments/out/minimal_schedule.json --out experiments/out/genome.json
uv run worldline genome challenge experiments/out/genome.json scenarios/convoy_lostlink.yaml --seed 42 --out experiments/out/challenge.json
```

Expected minimal trace digest: `sha256:<digest>`. Run it twice: a different digest is a determinism bug and takes priority over everything else.

### Repair contract (AGENTS.md)

A repair MR for this issue is acceptable only when every box can be ticked:

- [ ] The failing branch (same seed, same minimal schedule) yields PASS on the **product copy** `scenarios/product/<file>.yaml`; new digest quoted
- [ ] The specimen `scenarios/<file>.yaml` is untouched, and a test shows it still FAILs under the same schedule
- [ ] No invariant weakened: `invariants/`, `policies/`, `worldline/evidence/` untouched; no check in `worldline/invariants/` more permissive
- [ ] Regression scenario `scenarios/regressions/<invariant>_<short>.yaml` plus a test asserting PASS
- [ ] `uv run pytest -q` passes; no test deleted, skipped or xfailed
- [ ] MR description states invariant and version, witness before, mechanism, change, claim ceiling
- [ ] No wall-clock time, no randomness outside `DeterministicRNG`, no LLM call in the decision path
- [ ] Non-vacuous: the repaired rule still reaches its non-default option when the evidence supports it

### Hand-off

- Start the Investigator: comment `@ai-worldline-investigate-<group> investigate scenarios/<file>.yaml seed 42 budget 200`
- Start the Repairer once the witness is confirmed: comment `@ai-worldline-repair-<group> repair this issue under the AGENTS.md contract`

Claim ceiling: EMPIRICALLY_VALIDATED on synthetic scenarios; ATTESTED only after kernel recomputation.

/label ~"worldline:investigate"
