# Invariants

An invariant is a property a world must hold. WORLDLINE checks each one on a trace and returns a three-valued verdict:

- **PASS**: the property held on every instance it applies to.
- **FAIL**: a counterexample exists. `witness` is the event ids that show it.
- **UNKNOWN**: the trace lacks what the check needs. `missing_data` names it. This means the scenario needs more instrumentation, not that the invariant is broken.

Invariants are declared in YAML under `invariants/`. `load_invariants("invariants")` loads every file in the directory and rejects duplicate ids across files.

| File | Contents |
|---|---|
| `invariants/core.yaml` | `epistemic.no_future_knowledge`, `epistemic.label_isolation`, `response.alert_ack`, `absence.no_corrupted_in_decision`, `decision.consistent_with_truth`, `always.non_negative_capacity` |
| `invariants/extended.yaml` | the six invariants documented below |

A scenario lists the invariants it runs by default (`invariants:` in its YAML), so adding a file does not change what `worldline run`, `explore` or the kernel evaluate for an existing scenario. To change what an existing invariant means, bump its `version`. The kernel recomputes verdicts and records `invariant_version` with each one.

```python
from worldline.invariants import load_invariants, check, check_all

invs = load_invariants("invariants")
records = check_all(trace, invs, scenario=scenario, evaluate_rule=evaluator, reference=root_trace)
```

`reference` is optional. Only `decision.fault_attributed` reads it, and every other invariant ignores it.

The parameters in `extended.yaml` are set for `scenarios/port_alert.yaml`, and every extended invariant passes on that scenario's root world. To use one on another scenario, copy the declaration, give it a new id and change the params.

---

## `latency.bound`

**Question.** Does every event of a given kind reach a role within a time budget?

**Kind:** `latency`. **Params:** `role`, `kind`, `bound_ns`. The check holds when `received_time_ns - event_time_ns <= bound_ns` for every `kind` event in the role's view.

**Witness.** Every offending event id, in canonical order.

**UNKNOWN.** `view:<role>` when the role has no view. `<kind>` when the role never received an event of that kind.

```yaml
- id: latency.bound
  version: "1.0"
  kind: latency
  params: {role: harbour_ops, kind: correction, bound_ns: 60000000000}
```

On port_alert, delaying `correction-001` by 300 s gives FAIL with witness `[correction-001]`. That is the same fault that flips the duty officer's decision.

## `ordering.causal`

**Question.** Does each role receive a cause no later than its effect? Examples: the sighting before its correction, the alert before its ack.

**Kind:** `ordering`. **Params:**
- `link_fields`: payload fields that point at another event id. The default is `[corrects, acks, acknowledges, in_reply_to]`.
- `role`: optional. When omitted, every role's view is checked.

When event B's `payload.<field>` names event A, A (or a duplicate copy of A) must be received no later than B. Receiving both at the same instant passes. A reference to an id that is not in truth is ignored, because it says nothing about ordering. If A is in truth but never reaches the role, the check fails.

**Witness.** `[A, B]` for the first violation in receipt order. The explanation names the role.

**UNKNOWN.** `payload.<field>` for each link field, when no view event carries a link that resolves to a truth event. `view:<role>` when the named role has no view.

```yaml
- id: ordering.causal
  version: "1.0"
  kind: ordering
  params: {link_fields: [corrects, acks, acknowledges, in_reply_to]}
```

## `duplicate.suppressed`

**Question.** Did any decision count the same real-world event twice?

**Kind:** `duplicate`. **Params:**
- `rule_id`: optional, limits the check to one rule's decisions.
- `match`: optional event filter, same keys as `quorum.independent_sources`.
- `use_provenance`: default `true`.

Two events have the same origin when they share the id before `#dup`. With `use_provenance`, an event that has `provenance_ids` takes its first provenance id as its origin. Set `use_provenance: false` when your scenario uses provenance for derived reports rather than copies.

**Witness.** `[decision id, copy ids...]` for the first duplicated origin. A duplicate that sits in the view but is not in the decision's `known_event_ids` is not a violation.

**UNKNOWN.** `decision` when the trace has no decisions in scope.

```yaml
- id: duplicate.suppressed
  version: "1.0"
  kind: duplicate
  params: {use_provenance: true}
```

This is the mechanism random exploration found on port_alert. Duplicating `sighting-002` to harbour_ops makes the rule see two credible sightings, and the duty officer escalates. Witness: `[decision:duty_officer:escalate_or_monitor, sighting-002, sighting-002#dup1]`.

## `quorum.independent_sources`

**Question.** When a decision chose option X, did its evidence come from at least N independent sources?

**Kind:** `quorum`. **Params:**
- `option`: the option X to check.
- `min_sources`: the required number of distinct sources, N.
- `rule_id`: optional, limits the check to one rule's decisions.
- `window_ns`: optional. Only events received in `[at_ns - window_ns, at_ns]` count.
- `exclude_corrected`: optional. Ignores events that a known `correction` targets.
- `match`: the filter for supporting events. It accepts the absence-predicate keys `kind`, `source_id`, `label`, `quality`, `corrupted` and `payload` (equality), plus `payload_min` and `payload_max` (numeric bounds).

Supporting events are the decision's `known_event_ids` that pass the filter. The check counts distinct `source_id` values among them.

**Witness.** `[decision id, supporting event ids...]`. The explanation names the distinct sources it counted. The witness holds event ids, so it follows the same contract as every other FAIL.

**PASS / UNKNOWN.** If no decision chose `option`, the verdict is PASS, and the explanation says the pass is vacuous. `decision` is UNKNOWN when there are no decisions in scope.

```yaml
- id: quorum.independent_sources
  version: "1.0"
  kind: quorum
  params:
    rule_id: escalate_or_monitor
    option: escalate
    min_sources: 2
    exclude_corrected: true
    match: {kind: sighting, payload_min: {confidence: 0.6}}
```

With the duplicate fault above, both supporting events come from `patrol_boat_a`, so the decision had one source where it needed two. With the delayed correction, this invariant passes: two independent sources supported the decision, but the information was stale. That case is caught by `latency.bound` and the decision invariants instead.

## `decision.fault_attributed`

**Question.** Which missing information did the fault cause?

`decision.consistent_with_truth` reports every truth event the actor had not received at decision time. That list includes things the actor could never see, such as other roles' private traffic or events stamped at the decision instant.

**Kind:** `decision` with `witness: fault_attributed`. **Params:** the same as `decision.consistent_with_truth` (`rule_id`, `actor_roles`).

**Verdict.** Always the same as `decision.consistent_with_truth`.

**Witness.** With `reference=<root trace>`, the witness is the events missing at decision time in this trace minus the events also missing at the same decision point in the reference. If nothing remains, the witness is `[decision id]`, meaning the decision was wrong for a reason other than missing information. Without a reference, the witness is the full missing list, and the explanation says it is not fault-attributed.

**UNKNOWN.** `decision_rule_evaluator` when no evaluator is supplied. `decision` when the trace has no decisions.

```yaml
- id: decision.fault_attributed
  version: "1.0"
  kind: decision
  params: {witness: fault_attributed}
  required_signals: [decision]
```

On convoy_lostlink with `retract-001` delayed to ops_center, the full witness is `[tele-001, tele-002, tele-003, tele-004, retract-001]`. The fault-attributed witness is `[retract-001]`.

## `epistemic.permitted_only`

**Question.** Did any decision rely on information its role is not allowed to see?

This approximates non-interference: a decision should depend only on events visible to its role.

**Kind:** `epistemic` with `rule: permitted_only`. **Params:** `rule_id` (optional), `actor_roles` (optional, an actor-to-role fallback).

Each decision's role comes from the scenario's actors. An event is permitted when the role's `visibility` contains `"*"` or contains every one of the event's `policy_labels`. This is the same rule the runner uses to build views.

**Witness.** `[decision id, offending event ids...]` for the first offending decision.

**UNKNOWN.** `scenario` when no scenario is supplied, because visibility lives in the scenario. `decision` when there are no decisions. `role:<id>` or `event:<id>` when a role or a cited event cannot be resolved.

```yaml
- id: epistemic.permitted_only
  version: "1.0"
  kind: epistemic
  params: {rule: permitted_only}
```

`epistemic.label_isolation` asks whether a role ever received a forbidden label. This invariant asks whether a decision used one. A leak that never reaches a decision fails the first and passes this one.
