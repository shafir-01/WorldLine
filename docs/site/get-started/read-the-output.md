# Read the output

Each step writes plain JSON into the output directory, `experiments/out/` by default. The viewer, the kernel, and the release job all read these same files.

## Verdicts

Every invariant check produces one of three results:

| Result | Meaning | What comes with it |
|---|---|---|
| **PASS** | The guarantee held in this world. | An explanation. |
| **FAIL** | The guarantee was violated. | A **witness**: the event ids that form the counterexample. |
| **UNKNOWN** | The checker could not decide. | `missing_data`, which names the missing signal. |

UNKNOWN is never rounded up to PASS. A release policy that needs a claim treats UNKNOWN as unmet.

## Digests

Commands print `sha256:` digests. The **trace digest** covers truth, every role's view, and every decision. The same scenario, seed, and fault schedule always give the same trace digest, so two people can compare a run by eye. The **bundle root** is the head of the evidence hash chain that the kernel signs.

## The files

| File | Written by | What to look for |
|---|---|---|
| `traces/root.json`, `verdicts/root.json` | `run`, `explore` | The unperturbed world and its verdicts. |
| `exploration.json` | `explore` | `comparison`: failing branches per 100 and the index of the first decision FAIL for each strategy. `trace_files` lists the exported traces of failing branches. |
| `minimal_schedule.json` | `minimize` | `original` versus `minimal`, `one_minimal_verified`, `oracle_calls`, `witness`, and a `causal_diff` against the root. |
| `genome.json` | `genome extract` | `mechanism_sentence`, abstract `mechanism` selectors (never event ids), `preconditions`, and `origin`. |
| `challenge.json` | `genome challenge` | `primary.reproduced`, plus each `controls[]` result. For a control, "preconditions met, not reproduced" is the success case. |
| `index.json`, `agent_evidence.json` | `report` | The manifest and the agents' typed claims. Agents can never emit ATTESTED. |
| `bundle.json` | `verify` (kernel) | Records, hash links, and a signed checkpoint. |
| `authorization.json` | `authorize` (kernel) | `decision`, `reasons`, `satisfied`, `missing`, `stale`, and `downgraded`. |

The full shapes of these files are in [Viewer and data contract](../reference/viewer.md#data-contract).

## Reading a FAIL

Take the minimal `port_alert` counterexample:

- **What broke.** `decision.consistent_with_truth`. The duty officer chose `escalate`, but the same rule evaluated over truth at 1500 s gives `monitor`.
- **Why.** The witness lists truth events from before the decision that the officer's role had not received. One of them is `correction-001`, which the intervention delayed.
- **What is necessary.** The minimal schedule has one intervention, and removing it makes the branch pass. That is what 1-minimal means.
- **Whether it generalizes.** The genome reproduces on `convoy_lostlink` and not on `convoy_lostlink_immune`.

!!! tip "Witnesses report all missing information"
    A decision witness lists every truth event the role had not yet received at decision time, not only the ones the fault caused. The `causal_diff` in `minimal_schedule.json` isolates what the intervention changed.

## Reading an authorization

```json
{"decision": "DENIED", "reasons": ["integrity:record_edited@1: record 1 content does not match its hash"], ...}
```

The `integrity:` reasons come from bundle verification. `missing` lists policy requirements with no acceptable record. `stale` lists evidence older than the policy's `max_age_s`. `downgraded` lists records whose kind is below the required kind. For how the kernel reaches these decisions, see [The kernel](../concepts/kernel.md).
