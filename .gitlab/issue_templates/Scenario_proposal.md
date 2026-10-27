<!-- Propose a new WORLDLINE scenario: a world worth exploring. A new scenario starts as a specimen
     (scenarios/<name>.yaml); a product copy under scenarios/product/ appears only when a repair lands. -->

## Scenario proposal: `<name>`

### The system and the decision

- **Domain:** <e.g. harbour operations, convoy routing>
- **Actors and roles:** <actor id, role, what it decides>
- **Decision rule(s):** `<rule_id>`: `<then>` when `<condition>`, otherwise `<otherwise>`, at `<at_ns>`
- **Why this decision matters:** <one or two sentences>

### Events and delivery

- **Event kinds (Event v1):** <kinds; which ones can be corrected or retracted, via the `corrects` field>
- **Delivery policy per role:** <latency, ordering, policy labels such as `harbour_private`>
- **Horizon:** <seconds>
- **Data source:** synthetic, or a pinned snapshot of real data (name the snapshot and its sha256; never live calls)

### Invariants it should exercise

Existing invariants only. New invariants go through CODEOWNERS review in `invariants/`.

- [ ] `decision.consistent_with_truth`
- [ ] `response.alert_ack`
- [ ] `epistemic.no_future_knowledge`
- [ ] `epistemic.label_isolation`
- [ ] `absence.no_corrupted_in_decision`

### Expected interest

- **Fault family expected to break it, and why:** <delay / drop / duplicate / reorder / corrupt / revoke / clock_skew>
- **Genome transfer:** should an existing genome (`genome:<id>`) reproduce here, or is this an immune control?
- **Signals that might be missing:** <list; an UNKNOWN means the scenario lacks instrumentation, not that it failed>

### Acceptance

- [ ] `uv run worldline run scenarios/<name>.yaml --seed 42` gives the same digest twice
- [ ] The baseline (no faults) passes every invariant it exercises
- [ ] The header carries `specimen: true` if it is meant to stay defective
- [ ] A test in `tests/` loads it and checks the baseline

/label ~"worldline:scenario"
