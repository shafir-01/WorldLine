# Invariants

Invariants are declared in YAML under `invariants/`, a path protected by CODEOWNERS. `load_invariants("invariants")` loads every `*.yaml` and `*.yml` file in the directory and rejects duplicate ids. Each invariant has an `id`, a `version`, a `kind`, `params`, and `required_signals`, which are event kinds that must be present for a verdict. When a required signal is absent, the result is UNKNOWN. For the model behind them, see [Concepts: Invariants](../concepts/invariants.md).

A scenario names the invariants it checks by default under `invariants:`. Adding a file doesn't change what an existing scenario evaluates. To change what an invariant means, bump its `version`. The kernel records `invariant_version` with every verdict it recomputes.

{{INVARIANTS_TABLE}}

## File shape

```yaml
invariants:
  - id: response.alert_ack
    version: "1.0"
    kind: response
    description: >
      G(alert -> F<=within_ns acknowledged) inside the harbour_ops view ...
    params:
      role: harbour_ops
      trigger_kind: alert
      response_kind: ack
      within_ns: 300000000000
      link_field: acknowledges
    required_signals: [alert]
```

## Verdict record

Each check returns a `VerdictRecord` with these fields: `invariant_id`, `invariant_version`, `result` (PASS, FAIL, or UNKNOWN), `witness[]`, `explanation`, `missing_data[]`, `derived_at_ns` (evaluator time, separate from event time), and `trace_digest`. The CLI writes them to `verdicts/<trace>.json`.
