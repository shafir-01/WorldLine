# Authoring scenarios

A WORLDLINE scenario is a small YAML file that describes a world: who observes what, how late, and which decisions they make from what they know. WORLDLINE runs it deterministically, then branches it to find the delivery faults that make a decision go wrong. You write data, not code. Decision rules use a small, closed predicate language with no `eval`.

This guide covers:

1. [A scenario from scratch in ten steps](#a-scenario-from-scratch-in-ten-steps), using a new domain (a cold-chain warehouse)
2. [Grammar reference](#grammar-reference)
3. [Predicate DSL reference](#predicate-dsl-reference): every function, filter, flag and literal
4. [Validation](#validation): what the validator checks and how to read each error
5. [Declaring an invariant](#declaring-an-invariant)
6. [Specimens and product scenarios](#specimens-and-product-scenarios)

Three complete scenarios ship with the repo. Read one beside this guide:

| Scenario | Domain | Seeded defect |
|---|---|---|
| `scenarios/port_alert.yaml` | harbour security | a late radar-ghost correction makes the duty officer escalate |
| `scenarios/convoy_lostlink.yaml` | logistics convoy | a late threat retraction makes the watch officer reroute |
| `scenarios/triage_board.yaml` | hospital emergency department | a late acuity downgrade makes the bed manager open the resus bay |

---

## A scenario from scratch in ten steps

The example is a vaccine cold store. Temperature probes report to the dock immediately and to a quality office over a slow link. The quality lead decides whether to quarantine a lot. One alarming reading is later corrected as a calibration fault.

### Step 1: Name the decision

Start with the decision, not the events. Write it as one sentence: *at 600s the quality lead quarantines the lot if any uncorrected excursion above 8 °C is known; otherwise the lot is released.* Everything else in the file exists to feed that decision or to make it interesting.

Good scenarios contain at least one decision that depends on information that can arrive late, arrive twice, or be corrected. Those are the decisions WORLDLINE can find faults in.

### Step 2: Scaffold the file

```bash
uv run worldline init scenarios/cold_chain.yaml
```

`init` writes a fully commented template that already validates. The scenario id defaults to the file name (`--id` overrides it), and an existing file is never overwritten unless you pass `--force`. To see the smallest possible valid scenario instead:

```bash
uv run worldline validate --example
```

### Step 3: Set id, seed and clock

```yaml
id: cold_chain
seed: 11
clock:
  epoch_ns: 0
  tick_ns: 1s
  horizon_ns: 20m
```

- `id` appears in every trace, verdict and genome. Letters, digits, `_`, `.`, `-`.
- `seed` drives every random draw (delivery jitter and drops) through a deterministic RNG. Same scenario plus same seed gives a byte-identical trace digest.
- Every `*_ns` field takes integer nanoseconds or a string with a unit: `ns`, `us`, `ms`, `s`, `m`, `h` (`1500s`, `25m`, `1h`, `0.5s`).
- Decisions must happen at or before `horizon_ns`.

### Step 4: Declare roles

A role is a point of observation: it has a delivery policy (how late truth reaches it) and a visibility list (which policy labels it may see).

```yaml
roles:
  - id: dock
    visibility: ["*"]
    delivery: {base_latency_ns: 1s, jitter_ns: 1s, drop_probability: 0.0, ordering: fifo}
  - id: quality
    visibility: [telemetry]
    delivery: {base_latency_ns: 30s, jitter_ns: 20s, drop_probability: 0.0, ordering: network}
  - id: evaluator
    visibility: ["*"]
    delivery: {base_latency_ns: 0, jitter_ns: 0, drop_probability: 0.0, ordering: fifo}
```

- Receipt time = event time + `base_latency_ns` + a uniform draw in `[0, jitter_ns]`.
- `ordering: fifo` keeps receipt order equal to truth order. `ordering: network` lets jitter reorder events.
- `drop_probability` in `[0, 1]` loses events silently.
- `visibility: ["*"]` sees everything. Otherwise an event is visible only if **all** of its `policy_labels` are listed. An event with no labels is visible to every role.
- The `evaluator` role is a convention: an omniscient, zero-latency observer that is useful when you read traces.

### Step 5: Declare actors and their decision rules

```yaml
actors:
  - id: quality_lead
    role: quality
    decision_rules:
      - id: quarantine_or_release
        at_ns: 600s
        window_ns: 600s
        when: "count(kind='temp_excursion', payload.celsius>8, not_corrected) >= 1"
        then: quarantine_lot
        otherwise: release_lot
  - id: dock_supervisor
    role: dock
    decision_rules:
      - id: call_engineer_or_wait
        at_ns: 900s
        window_ns: 900s
        when: "exists(kind='door_alarm') and not exists(kind='door_closed')"
        then: call_engineer
        otherwise: wait
```

An actor decides only from what its role has **received** by `at_ns`, never from truth. `window_ns` limits which received events are counted: those received in `[at_ns - window_ns, at_ns]`. Corrections and acknowledgements are looked up in everything the role knows, because knowledge does not expire. Rule ids must be unique across the whole scenario. `then` and `otherwise` are free-form option names that show up in decisions and verdicts.

### Step 6: Write the truth

Injects are what actually happens, at their true time.

```yaml
injects:
  - {id: temp-001, at_ns: 120s, source_id: probe_3, kind: temp_reading, payload: {celsius: 4.8}, policy_labels: [telemetry]}
  - {id: exc-001,  at_ns: 300s, source_id: probe_7, kind: temp_excursion, payload: {celsius: 11.2, lot: L-88}, policy_labels: [telemetry]}
  - {id: door-001, at_ns: 700s, source_id: door_sensor, kind: door_alarm, payload: {door: D2}, policy_labels: [facility]}
  - {id: door-002, at_ns: 760s, source_id: door_sensor, kind: door_closed, payload: {door: D2}, policy_labels: [facility]}
```

- Required: `at_ns`, `source_id`, `kind`. Optional: `id` (defaults to `<kind>-<NNN>`), `payload`, `policy_labels`, `geometry`, `priority` (higher first within the same instant).
- `kind` is your vocabulary. Rules filter on it, so keep it consistent.
- Give important events explicit ids. Witnesses, interventions and genomes refer to them.

### Step 7: Add the correction

A correction is an ordinary inject of kind `correction` whose `payload.corrects` names the event it corrects. The `not_corrected` flag then drops the corrected event from a count.

```yaml
  - {id: corr-001, at_ns: 520s, source_id: probe_7, kind: correction,
     payload: {corrects: exc-001, celsius: 5.1, reason: "probe_7 calibration fault"}, policy_labels: [telemetry]}
```

Acknowledgements work the same way: an inject (kind `ack` by default) with `payload.acknowledges: <id>` satisfies `acknowledged(...)`. Placing the correction 80s before the decision, with a 30s + up to 20s link to `quality`, is what makes this world fragile: in this seed the correction arrives at about 566.6s, so a delivery delay of roughly 34s or more lands the correction after the decision.

### Step 8: Add transitions

Transitions change the world itself. The only kind today is `outage`: events from `source_id` with event time in `[from_ns, to_ns]` are never delivered to `roles` (all roles when empty). They still exist in truth.

```yaml
transitions:
  - {id: wifi_drop, kind: outage, from_ns: 100s, to_ns: 130s, source_id: probe_3}
```

### Step 9: Choose invariants and validate

```yaml
invariants:
  - epistemic.no_future_knowledge
  - decision.consistent_with_truth
```

The ids refer to declarations in `invariants/*.yaml` (see [Declaring an invariant](#declaring-an-invariant)). Then validate:

```bash
uv run worldline validate scenarios/cold_chain.yaml
# OK   scenarios/cold_chain.yaml: valid (3 roles, 2 actors, 2 decision rules, 5 injects, 1 transitions); 0 warning(s)
```

Validate after every edit. It takes milliseconds and points at the exact YAML path and line.

### Step 10: Run, perturb, explore

```bash
uv run worldline run scenarios/cold_chain.yaml
```

In the root world the correction reaches `quality` at about 566.6s. The rule sees no uncorrected excursion, chooses `release_lot`, and every invariant passes. Delay the correction to the quality office by 120s and the same rule chooses `quarantine_lot` on stale information. `decision.consistent_with_truth` FAILs with witness `[temp-001, corr-001]`: the truth events the quality office had not received at 600s (`temp-001` was silenced by the outage).

You don't have to find that delay yourself:

```bash
uv run worldline explore scenarios/cold_chain.yaml --budget 200 --seed 42 --out experiments/out
uv run worldline minimize experiments/out/exploration.json --out experiments/out
```

The explorer branches the world with delays, drops, duplicates, reorders, corruptions, revocations and clock skews, and `minimize` shrinks a failing schedule to a 1-minimal counterexample. If you intend the scenario as a laboratory specimen, mark it as one (see [Specimens](#specimens-and-product-scenarios)).

---

## Grammar reference

The authoritative shape is the JSON Schema at `scenarios/schema/scenario.schema.json` (draft 2020-12). Point your editor at it for completion and inline errors. With the YAML language server (VS Code YAML extension), add this first line:

```yaml
# yaml-language-server: $schema=./schema/scenario.schema.json
```

The path is relative to the scenario file. This example is for a file directly under `scenarios/`.

| Key | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | `[A-Za-z0-9_.-]+` |
| `seed` | integer | yes | root of every random stream |
| `clock` | mapping | yes | `horizon_ns` required; `epoch_ns` (default 0), `tick_ns` (default 1s) |
| `roles[]` | list | no | `id`, `visibility` (default `["*"]`), `delivery` |
| `roles[].delivery` | mapping | no | `base_latency_ns`, `jitter_ns`, `drop_probability` (0..1), `ordering` (`fifo` or `network`) |
| `actors[]` | list | no | `id`, `role`, `decision_rules[]` |
| `actors[].decision_rules[]` | list | no | all of `id`, `at_ns`, `window_ns`, `when`, `then`, `otherwise` |
| `injects[]` | list | no | `at_ns`, `source_id`, `kind`; optional `id`, `payload`, `policy_labels`, `geometry`, `priority` |
| `transitions[]` | list | no | `id`, `kind: outage`, `from_ns`, `to_ns`; optional `source_id`, `roles`, `params` |
| `invariants[]` | list of strings | no | ids from `invariants/*.yaml`; empty means all declared invariants |
| `specimen` | boolean | no | marks a frozen, deliberately defective specimen |
| `provenance` | mapping | no | free-form; used by scenarios built from recorded data (see `scenarios/real/`) |
| `x-*` | any | no | extension keys, ignored by the loader |

Any other key is an error. This catches typos such as `injcts` or `prority`, which the loader would otherwise ignore without a word.

Reserved payload fields: `corrects` (on `correction` injects) and `acknowledges` (on acknowledgement injects). Both must name an existing inject id.

---

## Predicate DSL reference

`when` is a boolean expression over the events the actor's role knows at `at_ns`.

### Grammar

```
expr      := and_expr ('or' and_expr)*
and_expr  := not_expr ('and' not_expr)*
not_expr  := 'not' not_expr | primary
primary   := '(' expr ')' | call (CMP NUMBER)?
call      := NAME '(' [arg (',' arg)*] ')'
arg       := FIELD ('=' | CMP) LITERAL      a filter
           | NAME                           a flag
CMP       := '>=' | '<=' | '==' | '!=' | '>' | '<'
```

Keywords (`and`, `or`, `not`, `true`, `false`, `null`) are case-insensitive. Precedence from loosest to tightest: `or`, `and`, `not`. Use parentheses to be explicit.

### Functions

| Function | Returns | Meaning |
|---|---|---|
| `count(filters..., flags...)` | integer | number of **windowed** known events matching every filter and flag |
| `exists(filters..., flags...)` | boolean | `count(...) > 0` |
| `acknowledged(filters..., within_ns=N, ack_kind='ack')` | boolean | true if at least one windowed event matches the filters, and **every** such event `A` has a known event of `ack_kind` with `payload.acknowledges == A.id` and `0 <= ack.event_time - A.event_time <= N`. `within_ns` is optional (no bound); `ack_kind` defaults to `'ack'`. Both take `=` only. |

A call can be compared with a number (`count(...) >= 2`). A bare call is truthy when it is non-zero or true. Don't compare `exists(...)` or `acknowledged(...)` with a number. A boolean never equals or orders against a number, so the comparison is always false, and the validator warns about it.

### Filters

A filter is `FIELD OP LITERAL`. `=` and `==` are the same.

| Field | Meaning |
|---|---|
| `id` | event id |
| `kind` | event kind |
| `source_id` | producing source |
| `event_time_ns` | truth time |
| `received_time_ns` | when this role received it |
| `priority` | inject priority |
| `quality` | unset on ordinary events; 0.0 marks an event corrupted by an intervention |
| `unit` | unit field of the event, if any |
| `payload.a.b` | nested payload field |

Comparing a missing field, or values of mismatched types, is false and never an error. A misspelled `payload.` field therefore fails quietly at runtime. The validator closes that gap by checking every `payload.*` filter against the payload fields your injects actually carry.

### Flags

| Flag | Keeps an event when |
|---|---|
| `not_corrected` | no known `correction` has `payload.corrects == event.id` |
| `corrected` | some known `correction` has `payload.corrects == event.id` |

### Literals

| Literal | Examples |
|---|---|
| string | `'sighting'`, `"amber"` |
| number | `2`, `0.6`, `-1` |
| duration | `600s`, `1.5m`, `250ms`, `2h`, `10us`, `100ns`: converted to integer nanoseconds |
| boolean / null | `true`, `false`, `null` |

### Examples

```text
count(kind='sighting', payload.confidence>=0.6, not_corrected) >= 2
exists(kind='lost_link')
exists(kind='vitals_alert', payload.spo2<90) or exists(kind='sepsis_screen', payload.positive=true)
acknowledged(kind='alert', within_ns=600s)
not exists(kind='door_closed', source_id='door_sensor')
count(kind='report', received_time_ns>=300s) >= 1 and not exists(kind='stand_down')
```

### How kinds feed exploration

The guided explorer and the genome read the kinds each rule depends on from the predicate: every `kind='...'` equality filter, plus `correction` when a rule uses `not_corrected`/`corrected`, plus the `ack_kind` of `acknowledged`. A precise rule gives exploration a better target.

---

## Validation

```bash
uv run worldline validate scenarios/triage_board.yaml              # one file
uv run worldline validate scenarios/*.yaml --invariants invariants # several, explicit invariants
uv run worldline validate my.yaml --strict                         # warnings fail too (CI)
uv run worldline validate my.yaml --json                           # machine-readable report
uv run worldline validate --example                                # print a minimal valid scenario
```

Exit code `0` means valid (warnings allowed unless `--strict`), `1` means errors, and `2` is a usage error. Without `--invariants`, the command uses `invariants/` when that directory exists and otherwise skips the invariant-id check, with a note.

From Python:

```python
from worldline.worlds.schema import validate_file
report = validate_file("scenarios/cold_chain.yaml", invariants="invariants")
report.ok, report.messages("error"), report.messages("warning")
```

### What is checked

1. **YAML**: syntax errors (with line) and duplicate keys. Plain YAML silently keeps only the last duplicate.
2. **Structure**: the JSON Schema: types, required keys, unknown keys (with suggestions), enums (`ordering`, transition `kind`), ranges (`drop_probability`), duration syntax, no negative durations.
3. **Predicates**: every `when` is parsed with the same parser the runner uses (`parse_predicate`). Unknown functions, flags and filters come back with a "did you mean". `payload.*` filters are checked against the payload fields of injects of the filtered kind.
4. **References**: `actors[].role` names a role; `payload.corrects` and `payload.acknowledges` name an inject id; `transitions[].roles` name roles; `invariants[]` name declared invariants. Ids are unique: roles, actors, decision rules (scenario-wide), injects (including auto-generated `<kind>-NNN` ids), and transitions.
5. **Loader agreement**: if nothing above failed, the document is loaded with the real loader as a final safety net.

### Errors, explained

Real output for a deliberately broken file:

```text
broken.yaml:7: error: roles[0].delivery.ordering: invalid value 'fifoo' (did you mean 'fifo'?); allowed: 'fifo', 'network'
broken.yaml:10: error: actors[0].role: unknown role 'operater' (did you mean 'operator'?); roles: operator
broken.yaml:19: error: actors[0].decision_rules[1].at_ns: decision time 1200s is outside the horizon (600s); the rule would never fire
broken.yaml:21: error: actors[0].decision_rules[1].when: unknown filter 'confidnce' (did you mean 'payload.confidence'?); filters are event fields (event_time_ns, id, kind, priority, quality, received_time_ns, source_id, unit) or payload.<field>
broken.yaml:27: error: actors[0].decision_rules[2].when: unknown function 'exsts' (did you mean 'exists'?); functions: acknowledged, count, exists
broken.yaml:33: error: actors[0].decision_rules[3].when: unknown filter 'payload.confidnce' (did you mean 'payload.confidence'?); no inject of kind 'report' carries that payload field
broken.yaml:38: error: injects[1].payload.corrects: corrects 'report-01' is not an inject id (did you mean 'report-001'?)
broken.yaml:39: error: injects[2].id: duplicate inject id 'report-001' (also at injects[0].id)
broken.yaml:39: error: injects[2].prority: unknown key 'prority' (did you mean 'priority'?); allowed here: id, at_ns, source_id, kind, payload, policy_labels, geometry, priority
broken.yaml:40: error: invariants[1]: unknown invariant 'decision.consistant_with_truth' (did you mean 'decision.consistent_with_truth'?)
broken.yaml:33: warning: actors[0].decision_rules[3].when: exists(...) is a boolean; comparing it with a number ('>= 1') is always false. Use it bare, or use count(...)
broken.yaml:33: warning: actors[0].decision_rules[3].when: kind 'reprot' never occurs in injects (did you mean 'report'?); this filter matches nothing
broken.yaml:39: warning: injects[2].at_ns: inject 'report-001' at 1200s is after the horizon (600s) and never happens
FAIL broken.yaml: 10 error(s), 3 warning(s)
```

Each line reads `file:line: severity: yaml.path: message`. The path is exact: `actors[0].decision_rules[1].when` is the `when` of the second rule of the first actor.

| Message | Cause | Fix |
|---|---|---|
| `unknown key 'x' (did you mean 'y'?)` | a key the grammar does not have | rename it; for private notes use an `x-` key or a YAML comment |
| `missing required key 'x'` | e.g. a rule without `otherwise` | add it; rules need all six keys |
| `invalid value '60 sec': expected a duration...` | duration syntax | `60s`, `1m`, or integer nanoseconds |
| `invalid value 'fifoo' (did you mean 'fifo'?)` | enum | `fifo` or `network`; transition kind is `outage` |
| `unknown filter 'x' (did you mean 'payload.x'?)` | payload fields need the `payload.` prefix, or a typo | use the suggestion |
| `unknown filter 'payload.x' ... no inject of kind 'k' carries that payload field` | the field is misspelled relative to your injects | fix the rule or the payload |
| `unknown function 'f'` / `unknown flag 'g'` | outside the closed DSL | functions: `count`, `exists`, `acknowledged`; flags: `not_corrected`, `corrected` |
| `within_ns and ack_kind are only valid inside acknowledged()` | used in `count`/`exists` | move it into `acknowledged(...)`, or filter on `received_time_ns` |
| `predicate syntax error: ...` | unbalanced parentheses, a missing literal, trailing tokens | the parser reports the first problem only; fix it and re-run |
| `unknown role 'x'` | actor or transition names a role that is not declared | declare it or fix the name |
| `corrects 'x' is not an inject id` | a correction points nowhere, so `not_corrected` would silently ignore it | point it at an existing inject id |
| `duplicate inject id 'x'` | explicit ids collide, or an explicit id collides with an auto id like `report-001` | give every inject a unique id |
| `decision time ... is outside the horizon` | `at_ns > horizon_ns`; the loader rejects it | move the decision or extend the horizon |
| `to_ns ... is before from_ns` | an inverted outage window | swap them |
| `unknown invariant 'x'` | the id is not declared in `invariants/` | fix the id, or declare the invariant |
| `duplicate key 'x' (first defined on line N)` | YAML keeps only the last value | remove one |

### Warnings, explained

A warning means the file loads, but something probably does not do what you intended.

| Warning | Why it matters |
|---|---|
| `kind 'x' never occurs in injects` | the filter matches nothing, so the rule always takes the same branch |
| `exists(...) is a boolean; comparing it with a number ... is always false` | use `exists(...)` bare, or `count(...) >= N` |
| `filter 'payload.x': no inject carries that payload field` | the comparison is false for every event |
| `role 'x' never receives anything` | its visibility excludes every inject, or `drop_probability` is 1 |
| `label 'x' is never carried by any inject` | likely a typo in `visibility` |
| `actor 'a' counts kind 'k' but its role 'r' cannot see any 'k' inject` | the rule can never see its own evidence |
| `no inject of kind k happens inside the decision window` | `window_ns` is too small or the decision is too early |
| `then and otherwise are the same option` | the decision can never differ, so nothing can go wrong |
| `inject 'x' ... is after the horizon and never happens` | extend the horizon or drop the inject |
| `no inject comes from source 'x'` / `no inject from 'x' falls inside [...]` | the outage silences nothing |
| `correction 'x' has no payload.corrects` | the flags cannot use it |
| `flag not_corrected has no effect: no inject of kind 'correction' exists` | either add the correction or drop the flag |
| `decision time equals the horizon` / `is before the clock epoch` | edge timing |
| `no decision rules` | `decision.*` invariants will be UNKNOWN |
| `specimen: true without a '# SPECIMEN: ...' header comment` | specimens must explain their seeded defect |

Use `--strict` in CI to keep the shipped scenarios warning-free. Every scenario under `scenarios/` passes `--strict` today, and a test enforces it.

---

## Declaring an invariant

Invariants are the specification of correctness. They live in `invariants/*.yaml`, which is a protected path: only maintainers edit it (see `CODEOWNERS` and `AGENTS.md`). An agent or contributor who needs a new invariant proposes the declaration in an MR description, and a maintainer adds it.

Shape:

```yaml
invariants:
  - id: always.cold_store_in_range           # <kind>.<name>; referenced from scenario `invariants:`
    version: "1.0"                           # bump on any semantic change; verdicts record it
    kind: always
    description: >
      No truth temp_reading reports payload.celsius above 8.
      FAIL witness: the first offending truth event.
    params:
      kind: temp_reading
      field: payload.celsius
      max: 8
    required_signals: [temp_reading]         # event kinds that must occur; otherwise UNKNOWN names them
```

The kinds, and the parameters the checker (`worldline/invariants/checker.py`) reads for each:

| `kind` | Checks | `params` |
|---|---|---|
| `always` | a state predicate over every truth event that carries `field` | `field` (required), `kind` (optional), any of `min`, `max`, `equals`, `not_equals` |
| `response` | `G(trigger -> F<=within response)` in one role's view | `role`, `trigger_kind`, `response_kind`, `within_ns`, `link_field` (payload field that names the trigger; default `acks`) |
| `absence` | no event matching `match` in role views (`scope: views`, optional `role`) or in the events a decision relied on (`scope: decisions`) | `scope`, `role`, `match: {kind, source_id, label, quality, corrupted, payload: {...}}` |
| `epistemic` | information-state separation | `rule: no_future_knowledge`, or `rule: label_isolation` with `role` and `label` |
| `decision` | each decision equals what its rule yields over truth as of `at_ns` | none required |

The checker also implements `latency`, `ordering`, `duplicate` and `quorum`. Read their parameters in `checker.py` before you declare one.

Rules of the road:

- Every FAIL must carry a witness of event ids, and every UNKNOWN must name the missing signal. Use `required_signals` so a scenario without the relevant kind reports UNKNOWN instead of a vacuous PASS.
- Never weaken an existing invariant. Add a new id or bump `version` with a stricter check.
- After adding one, reference it from a scenario's `invariants:` list and run `worldline validate` to confirm the id resolves.

---

## Specimens and product scenarios

Scenarios directly under `scenarios/` are laboratory **specimens**: deliberately defective and frozen, so that exploration, minimisation and the genome pipeline always have something to find. A specimen states its defect in a header comment, then carries the marker line and the flag:

```yaml
# Seeded latent defect (do not "fix" it here, a later module must discover it):
#   <who decides what, when; which event arrives when; what delay flips it>
# SPECIMEN: deliberately defective laboratory scenario. Frozen. Repairs land on scenarios/product/ (see AGENTS.md).
specimen: true
```

Repairs never edit a specimen. They land on `scenarios/product/<same file name>`, following the repair contract in `AGENTS.md`.

The three shipped specimens share one defect class, *a decision taken on stale information when a correction arrives after the decision point*, in three unrelated vocabularies. That is why the failure genome extracted from `port_alert` can be challenged against the others:

```bash
uv run worldline genome challenge experiments/out/genome.json scenarios/triage_board.yaml --out /tmp/challenge_triage.json
# genome genome:94f0ad69dd5483d0 on triage_board: reproduced yes
```

On `triage_board` it reproduces. All preconditions are met, it instantiates as "delay `correction-001` to `bed_manager` by ~39.6s", and `decision.consistent_with_truth` FAILs. Transfer is within the shared Event v1 substrate across scenarios; it is not transfer across heterogeneous codebases.
