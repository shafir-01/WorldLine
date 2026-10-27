# Author a scenario

A scenario is a YAML file that describes a world: what happens, who observes it and how fast, and who decides what from what they have received. It contains no code. This guide builds a small scenario step by step. For every field, see the [Scenario schema](../reference/scenario-schema.md).

## 1. Start from a copy

```bash
cp scenarios/port_alert.yaml scenarios/my_world.yaml
```

Give it a new `id` and `seed`. Every `*_ns` field accepts nanoseconds or a value with a unit suffix: `ns`, `us`, `ms`, `s`, `m`, or `h`.

```yaml
id: my_world
seed: 1
clock: {epoch_ns: 0, tick_ns: 1s, horizon_ns: 30m}
```

## 2. Declare who observes what

Each role sees truth through its own delivery policy and its own visibility labels:

```yaml
roles:
  - id: field_team
    visibility: ["*"]                       # sees every label
    delivery: {base_latency_ns: 2s, jitter_ns: 1s, drop_probability: 0.0, ordering: fifo}
  - id: hq
    visibility: [public, shared]            # never sees "private"
    delivery: {base_latency_ns: 40s, jitter_ns: 20s, drop_probability: 0.0, ordering: network}
  - id: evaluator
    visibility: ["*"]
    delivery: {base_latency_ns: 0, jitter_ns: 0, drop_probability: 0.0, ordering: fifo}
```

`ordering: network` lets jitter reorder deliveries, while `fifo` preserves source order.

## 3. Write the decision

An actor belongs to a role and decides at `at_ns` from the events its role has received within the last `window_ns`:

```yaml
actors:
  - id: duty_lead
    role: hq
    decision_rules:
      - id: act_or_wait
        at_ns: 900s
        window_ns: 600s
        when: "count(kind='report', payload.confidence>=0.6, not_corrected) >= 2"
        then: act
        otherwise: wait
```

The predicate language is closed and safe, with no `eval`:

- Functions: `count(...)`, `exists(...)`, and `acknowledged(kind='alert', within_ns=600s, ack_kind='ack')`.
- Filters: `field=value` or a comparison (`>=`, `<=`, `==`, `!=`, `>`, `<`) on `kind`, `source_id`, `id`, `event_time_ns`, `received_time_ns`, `priority`, `quality`, `unit`, or `payload.<path>`.
- Flags: `not_corrected` and `corrected` (whether a known `correction` event's `payload.corrects` names this event).
- Combinators: `and`, `or`, `not`, and parentheses.

A comparison against a missing field is false and never raises an error. A malformed rule fails when the scenario loads.

## 4. Write what happens

```yaml
injects:
  - {id: report-001, at_ns: 300s, source_id: sensor_a, kind: report, payload: {confidence: 0.8}, policy_labels: [shared]}
  - {id: report-002, at_ns: 500s, source_id: sensor_b, kind: report, payload: {confidence: 0.7}, policy_labels: [shared]}
  - id: correction-001
    at_ns: 780s
    source_id: sensor_b
    kind: correction
    payload: {corrects: report-002, reason: "duplicate track"}
    policy_labels: [shared]
    priority: 5
transitions:
  - {id: sensor_outage, kind: outage, from_ns: 1200s, to_ns: 1500s, source_id: sensor_a}
```

An outage silences a source for some or all roles. The events still happen in truth, but nobody hears about them.

## 5. Choose the invariants

```yaml
invariants:
  - epistemic.no_future_knowledge
  - absence.no_corrupted_in_decision
  - decision.consistent_with_truth
```

The available ids are listed in the [Invariants reference](../reference/invariants.md). An invariant whose required signal never occurs reports UNKNOWN, not PASS.

## 6. Run it and break it

```bash
uv run worldline run scenarios/my_world.yaml --out experiments/my
uv run worldline explore scenarios/my_world.yaml --budget 150 --seed 42 --out experiments/my
uv run worldline minimize experiments/my/exploration.json --out experiments/my
```

A healthy root world followed by failing branches is the interesting outcome. It means a plausible change in delivery breaks a guarantee, and `minimize` tells you which change.

## Rules of the repository

- Scenarios directly under `scenarios/` are **specimens**: deliberately defective and frozen. A repair lands on a copy in `scenarios/product/`, as described in `AGENTS.md`.
- Label synthetic scenarios as synthetic. A scenario with real data must say which parts are real and which are modelled. See [Real-data scenarios](real-data.md).
