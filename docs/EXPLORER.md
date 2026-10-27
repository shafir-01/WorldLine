# Explorer

`worldline.explorer.explore` branches the root world of a scenario under fault schedules and checks every invariant on each branch. `minimize` shrinks a failing schedule with ddmin. This page covers strategies, equivalence reduction, parallel runs and delay thresholds. Code: `worldline/explorer/explore.py`, `reduction.py`, `parallel.py`.

```python
explore(scenario, invariants, *, budget, seed, strategy="guided", schedule_size=1, now_ns=0,
        reduce=False, workers=1) -> ExplorationResult
```

`budget` is the number of proposals. A repeated schedule (same hash) uses up a proposal without running. Every output is a pure function of `(scenario, invariants, seed, strategy, schedule_size, budget, reduce)`. The only fields that change between runs are `wall_ns` and `cost_ns`, which are timings.

## Strategies

| strategy | candidate pool | order |
|---|---|---|
| `random` | every default mutator proposes over all truth events | uniform picks |
| `guided` | same pool, decision-relevant candidates first (kinds a rule reads, deciding roles) | half-normal bias to the front |
| `windowed` | only decision-window candidates (below) | predicted flips first, each tier shuffled by seed; one sweep at `schedule_size=1`, extra slots drawn from the pool otherwise |

The windowed strategy works rule by rule. For each decision rule (actor role R, `at_ns`, `window_ns`), it looks at every root delivery to R whose kind the rule's predicate can read, with root receipt time `rt`, and proposes these candidates:

- **exit**: `delay(by = at_ns - rt + 1)` when `rt <= at_ns`. This is the smallest delay that leaves the event unknown at the decision.
- **enter**: `delay(by = at_ns - window_ns - rt)` when the event arrives before the window. This is the smallest delay that brings it inside the window.
- **flip**: `delay(by = flip_threshold(...))`. This is the smallest delay that changes the rule's option (see Thresholds).
- **drop**, and **corrupt** of each payload field the predicate reads, when the event is known at the decision. Corrupt values come from the seeded RNG.

Every candidate is evaluated on the predicted view, and the ones that change a decision relative to the root go first. On `port_alert` the pool has 22 candidates, 9 of which are predicted flips. The first branch that fails `decision.consistent_with_truth` is at index 1 for every seed tested. The windowed strategy never proposes anything outside decision windows. It cannot find failures that need other faults, for example `response.alert_ack` failures caused by delaying the alert at harbour_ops, which no rule reads.

## Delivery model

The runner gives every surviving (role, truth event) pair a *pre-hook* receipt time: base latency plus seeded jitter, FIFO-clamped, after the visibility, suppression and drop draws. The fault schedule cannot change this time. It acts only through the delivery hook, `hook(event, role, pre_hook_rt) -> deliveries`.

`build_model` records every pre-hook triple once, by running the root with a recording identity hook, so the runner's own code computes the triples. `DeliveryModel.predict(schedule)` then applies `hook_for_schedule(schedule)` to the recorded triples, applies the horizon cut and the runner's time and scenario checks, and sorts each view by `(received_time_ns, sort_key)`. It returns `None` exactly when the runner would raise. It does not derive RNG streams, evaluate rules or hash traces.

`tests/test_reduction.py` checks the predicted views against `run()` on 250 random schedules covering every operator, comparing ids, receipt times, content hashes and decisions.

## Reduction

With `reduce=True`, each proposal gets a signature (`schedule_signature`). A proposal is skipped when its signature equals the root's or one already proposed for running. Skipped proposals are recorded in `ExplorationResult.skipped_equivalent` as `{schedule, equivalent_to, signature}`. Reduction never changes which schedules are proposed or their order: the explored nodes are the unreduced nodes minus the skipped ones.

### Signature definition

`signature = H(D, I, K)` over the predicted views:

- **D (decisions)**: one entry per decision rule (role R, `at_ns`, `window_ns`, readable kinds `Kr`). Each entry lists, in receipt order, the deliveries to R with `rt <= at_ns` and kind in `Kr`, as `(id, content hash, rt >= at_ns - window_ns, rt if the predicate reads received_time_ns)`. `Kr` is every kind the predicate filters on, plus `correction` for `(not_)corrected` and `ack_kind` for `acknowledged`. A call with no `kind ==` filter makes `Kr` all kinds. In short: the per-role receipt ordering restricted to decision-relevant events, together with the delivered, dropped and corrupted ids inside each decision window. A dropped event is absent, and a corrupted event has a different content hash.
- **I (invariants)**: one entry per invariant, holding the inputs its checker reads that a schedule can change:

  | kind | projected input |
  |---|---|
  | always, decision | none (truth only, or a function of the options fixed by D) |
  | response | trigger and response deliveries in that role, with exact receipt times and link field |
  | latency | per delivery of the kind: is `rt - event_time > bound` |
  | ordering | per causal link: did the cause arrive no later than the referencing event |
  | duplicate | per decision: origins with two or more known copies |
  | quorum | per decision: distinct sources among known, uncorrected, matching events |
  | absence over decisions | per decision: known ids matching the pattern (for example, corrupted) |
  | absence over views, label isolation | matching ids per role |
  | no_future_knowledge | deliveries received before their event time |
  | permitted_only | per decision: known ids whose labels the role may not see |
  | anything else | **exact**: the full predicted views (`projection_modes` reports which) |

- **K**: the set of kinds present in views, which `required_signals` reads.

### Soundness argument

Truth is fixed by the scenario. Each decision option is a function of the rule's known and windowed sets, and the predicate reads only the kinds in `Kr` (and receipt times only when it says so). D fixes those sets up to events the predicate cannot read, so it fixes every option. Each checker's result is a function of truth, the options, and the inputs listed in I and K. Equal signatures therefore give equal verdict **results** for every invariant, which means equal `failing_invariants`.

This holds only if `predict` is exact. It uses the runner's own hook and pre-hook times, and the tests compare it against the runner.

What the reduction **can miss**: nothing about which invariants fail, provided the projections mirror the checker. If a checker changes and its projection does not, a skip can become unsound. The guard is the "exact" fallback for unknown kinds, plus two tests that run every grouped schedule: all single interventions the mutators can propose on `port_alert`, and 200 random pairs and triples. Both assert identical results within each class. The benchmark re-checks every skip against the unreduced run (`equivalence_violations`).

What it **does not preserve**: witnesses, explanations, `trace_digest`, `known_event_ids`. A skipped branch's FAIL can name different event ids than its representative's. A schedule the model predicts the runner will reject is always run, so its rejection is recorded as before.

### Measured on port_alert

Seeds 1-5, budget 150, schedule size 3, all 12 invariants (core plus extended) at `projected`. Source: `experiments/out/benchmark.json`, `exploration.aggregate` and `reduction_summary`. The table shows means.

| strategy | runs | runs with reduce | runs skipped | failing-set coverage | violations |
|---|---|---|---|---|---|
| guided | 150.0 | 126.6 | 15.6 % | equal on all seeds | 0 |
| random | 149.8 | 108.2 | 27.8 % | equal on all seeds | 0 |
| windowed | 149.6 | 122.6 | 18.1 % | equal on all seeds | 0 |

The saving is modest because size-3 schedules rarely collide on every component. Single interventions collapse far more: the 264 distinct single interventions the mutators and the windowed pool propose on `port_alert` include 19 that the runner rejects. The remaining 245 fall into 84 classes, and 141 of them are equivalent to the root (`test_signature_sound_exhaustive_single_interventions` runs all of them). Signature cost is about 0.25 ms against about 2-3 ms per run.

## Parallelism

`workers=N` runs the planned branches in a `concurrent.futures.ProcessPoolExecutor` with the `spawn` start method. The scenario goes to each worker as `Scenario.to_dict()` and the invariants as dicts, once per worker through the pool initializer. Branches are sent in contiguous chunks and merged in submission order (`Executor.map`). Proposal and reduction run in the parent before dispatch, so the result is identical for every worker count (tested). Scripts that call `explore(workers>1)` need an `if __name__ == "__main__":` guard.

On this machine (8 logical CPUs, Windows, spawn), with budget 300 and branches of about 2-3 ms, worker start-up roughly cancels out the gain. The best case was 4 workers, at 1.1-1.3x. Two workers gave about 1.0x, and 8 workers were slower than serial (0.74-0.93x). See the `parallel` block of the benchmark. Parallelism pays only when branches are expensive (larger scenarios, or many invariants).

## Thresholds

```python
flip_threshold(scenario, event_id, role, rule_id, *, root=None, model=None) -> int | None
```

This returns the smallest `by_ns` for which `delay(event_id, role, by_ns)` changes `rule_id`'s option. It returns `None` if no delay does, or if `role` is not the rule's actor role. `role=None` delays the event to every role.

The rule's input changes with the delay `d` only when the delayed delivery crosses one of these points:

- the decision, at `at_ns - rt + 1`;
- the window start, at `at_ns - window_ns - rt`;
- the horizon, at `horizon - rt + 1`;
- another delivery to the same role, at `rt_o - rt` and `rt_o - rt + 1`;
- a `received_time_ns` literal in the predicate.

Between these breakpoints the option is constant. The function evaluates the rule once per breakpoint, in increasing order, on the predicted view: one solve, no sweep.

On `port_alert`, correction-001 reaches harbour_ops at `rt = 1409.781717903 s` and the decision is at `at_ns = 1500 s`. The threshold is therefore `90_218_282_098 ns`: a delay of 90.218282097 s still lets the event arrive exactly at `at_ns`, and one more nanosecond flips `escalate_or_monitor` to `escalate`. The runner confirms both sides.

When ddmin's minimal schedule is a single concrete `delay`, `MinimizationResult.threshold_ns` holds the smallest per-rule flip threshold `t` for which the oracle fails at `t` and passes at `t - 1`. Otherwise it is `None`. For the 4-intervention regression schedule in `tests/test_explorer.py`, the value is `90_218_282_098`.

## API added (backward compatible)

- `explore(..., reduce=False, workers=1)`, plus `strategy="windowed"`.
- `ExplorationResult.skipped_equivalent` (defaults to `[]`), and `n_skipped_equivalent` in `to_dict()`.
- `MinimizationResult.threshold_ns` (defaults to `None`).
- `worldline.explorer.reduction`: `build_model`, `DeliveryModel`, `schedule_signature`, `projection_modes`, `flip_threshold`, `delay_breakpoints`, `window_candidates`, `rule_kinds`.
- `worldline.explorer.parallel.run_branches`.
- `worldline.explorer.explore`: `STRATEGIES`, `delay_threshold`.
