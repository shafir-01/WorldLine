# Scenario schema

A scenario is one YAML document, which `worldline.worlds.load_scenario` loads into a `Scenario`. Unknown top-level keys, such as `specimen:` and `provenance:`, are ignored. A complete example is `scenarios/port_alert.yaml`. For a guided walkthrough, see [Author a scenario](../guides/authoring.md).

**Durations.** Every field ending in `_ns` accepts an integer number of nanoseconds or a string with a unit: `ns`, `us`, `ms`, `s`, `m`, or `h` (for example `90s`, `1.5m`, `1h`). Fractional nanoseconds are rejected.

## Top level

| Key | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | The scenario id. It appears in every trace and digest. |
| `seed` | integer | yes | The seed for delivery jitter and drops. |
| `clock` | mapping | yes | `epoch_ns`, `tick_ns`, `horizon_ns`. Decision times must not exceed the horizon. |
| `roles` | list | no | Observers. See below. Ids must be unique. |
| `actors` | list | no | Decision makers. Each must refer to an existing role. |
| `injects` | list | no | Truth events. Ids must be unique. |
| `transitions` | list | no | State changes of the world (outages). |
| `invariants` | list of ids | no | The invariants checked by default. See [Invariants](invariants.md). |

## `roles[]`

| Key | Type | Default | Description |
|---|---|---|---|
| `id` | string | required | The role id. |
| `visibility` | list of labels | `["*"]` | The policy labels this role may receive. `"*"` means all labels. An event is visible only if *all* of its labels are allowed. |
| `delivery.base_latency_ns` | duration | `0` | The fixed delay from event time to receipt. |
| `delivery.jitter_ns` | duration | `0` | Extra delay drawn uniformly from `[0, jitter_ns]` by a seeded per-event stream. |
| `delivery.drop_probability` | float in [0, 1] | `0.0` | The chance that a delivery is lost. |
| `delivery.ordering` | `fifo` or `network` | `fifo` | `fifo` keeps receipt order equal to truth order. `network` lets jitter reorder deliveries. |

## `actors[]`

| Key | Type | Description |
|---|---|---|
| `id` | string | The actor id. |
| `role` | role id | The role whose view the actor decides from. |
| `decision_rules[]` | list | See below. |

### `decision_rules[]`

| Key | Type | Description |
|---|---|---|
| `id` | string | The rule id. It appears in the decision event id `decision:<actor>:<rule>`. |
| `at_ns` | duration | The decision time. |
| `window_ns` | duration, at least 0 | Only events received within `[at_ns - window_ns, at_ns]` count. |
| `when` | predicate | The condition, in the [predicate language](#predicate-language). It is parsed at load time. |
| `then` | string | The option chosen if `when` is true. |
| `otherwise` | string | The option chosen otherwise. |

## `injects[]`

| Key | Type | Default | Description |
|---|---|---|---|
| `id` | string | `<kind>-<nnn>` | The event id. Corrections reference it. |
| `at_ns` | duration | required | The event time in truth. |
| `source_id` | string | required | Who emitted it. Used by outages and `clock_skew`. |
| `kind` | string | required | The event kind. Rules and invariants select on it. |
| `payload` | mapping | `{}` | Free-form data. Rules read it as `payload.<path>`. |
| `policy_labels` | list | `[]` | Visibility labels. |
| `geometry` | mapping | none | Optional spatial data. |
| `priority` | integer | `0` | A tie-breaker at equal event time (higher first). |

A **correction** is an event of kind `correction` whose `payload.corrects` names the id it supersedes. The `not_corrected` and `corrected` flags in rules use this field.

## `transitions[]`

| Key | Type | Description |
|---|---|---|
| `id` | string | The transition id. |
| `kind` | `outage` | The only kind so far. |
| `from_ns`, `to_ns` | duration | The inclusive window, with `to_ns >= from_ns`. |
| `source_id` | string, optional | The silenced source. If omitted, every source is silenced. |
| `roles` | list, optional | The affected roles. If omitted, every role is affected. |

During an outage, matching events still exist in truth but are never delivered.

## Predicate language

```text
expr      := and_expr ('or' and_expr)*
and_expr  := not_expr ('and' not_expr)*
not_expr  := 'not' not_expr | primary
primary   := '(' expr ')' | call (CMP NUMBER)?
call      := NAME '(' [arg (',' arg)*] ')'
arg       := FIELD ('=' | CMP) LITERAL  |  FLAG
CMP       := '>=' | '<=' | '==' | '!=' | '>' | '<'
LITERAL   := 'string' | number | duration | true | false | null
```

| Function | Value |
|---|---|
| `count(filters...)` | The number of known events in the window that match every filter. |
| `exists(filters...)` | `count(...) > 0`. |
| `acknowledged(kind='alert', within_ns=N, ack_kind='ack')` | True if at least one matching event is known and each has a known `ack_kind` event with `payload.acknowledges` equal to its id, at most `N` later. |

Fields: `id`, `kind`, `source_id`, `event_time_ns`, `received_time_ns`, `priority`, `quality`, `unit`, and `payload.a.b`. Flags: `not_corrected`, `corrected`. A comparison against a missing field or a mismatched type is false.
