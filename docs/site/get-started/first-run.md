# First run

This page runs one world, then many, then the full chain, against the bundled `port_alert` scenario. The outputs shown are from real runs. Digests depend on the code version, so yours may differ.

## 1. Run one world

```bash
uv run worldline run scenarios/port_alert.yaml --out experiments/out
```

```text
scenario port_alert  root trace digest sha256:d16ac4392733589d5dbc12c893be62d64a28e73184c8eb8ae0a2bbb18354dd17
  decision duty_officer/escalate_or_monitor at 1500s -> monitor (knew 17 events)
  decision coast_watch/stand_down_if_acknowledged at 2400s -> stand_down (knew 22 events)
  PASS    absence.no_corrupted_in_decision  no decision relied on an event matching {'corrupted': True}
  PASS    decision.consistent_with_truth  all 2 decision(s) match the rule evaluated over truth
  PASS    epistemic.label_isolation  role 'coast_guard' never received an event labelled 'harbour_private'
  PASS    epistemic.no_future_knowledge  no view or decision knows anything before it was received
  PASS    response.alert_ack  all 1 alert events acknowledged within 300000000000ns by harbour_ops
```

The root world is healthy: every invariant passes. Run it again and the digest is byte-identical, because the clock is virtual and all randomness is seeded.

## 2. Branch it into many worlds

```bash
uv run worldline explore scenarios/port_alert.yaml --budget 150 --seed 42 --out experiments/out
```

The explorer tries up to 150 fault schedules with each of two strategies. **Guided** prioritizes deliveries that decision rules depend on, reading only the scenario. **Random** is the baseline. It prints a comparison table and writes `exploration.json`, along with traces and verdicts for the root and every failing branch.

## 3. Reduce a failure to its cause

```bash
uv run worldline minimize experiments/out/exploration.json --out experiments/out
```

```text
[minimize] ...: 3 -> 1 interventions, 3 oracle calls, one-minimal True, witness ['correction-001', 'ais-006', 'patrol-001']
```

Three interventions were tried, and one explains the failure: delaying `correction-001` to `harbour_ops` past the decision.

## 4. Or run the whole chain

```bash
uv run worldline demo --out experiments/out --budget 150 --seed 42
```

```text
[explore] scenarios\port_alert.yaml budget 150 seed 42
| strategy | nodes | failing | fail/100 | first decision FAIL idx | wall ms |
| guided   | 69    | 23      | 33.8     | 6                       | 279     |
| random   | 66    | 20      | 30.8     | None                    | 262     |
[minimize] ...: 3 -> 1 interventions, 3 oracle calls, one-minimal True, ...
[genome] genome:94f0ad69dd5483d0: delay the last before decision 'correction' event (corrects dependency)
         seen by the decision role until after the decision, so that decision.consistent_with_truth fails.
[challenge] convoy_lostlink reproduced=True; controls: [('convoy_lostlink_immune', False)]
[report] 15 agent claims, 97 files
[verify] ok=True attested=16 mismatches=0 root sha256:e115b258...
[authorize] AUTHORIZED  (all 5 requirement(s) met by 15 record(s))
[authorize tampered invariant:absence.no_corrupted_in_decision] DENIED
(integrity:record_edited@1: record 1 content does not match its hash)
```

The demo also runs the unit tests first, because the release policy requires a `tests:unit` claim.

## 5. Look at it

```bash
cd ui && npm ci && npm run build && npm run preview
```

The viewer reads `experiments/out/` and opens on the Story view.

Next: [Read the output](read-the-output.md).
