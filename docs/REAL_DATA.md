# Real data: the USGS scenario

WORLDLINE's question is "what did the operator know when the decision was made?". Every other scenario in this repository is synthetic. This one is the first whose **truth stream is real recorded public data**: the earthquakes that happened in a fixed window, the magnitudes the USGS first published for them, and the times at which those magnitudes were revised. The operator, how fast information reaches them, and the decision rule are still **declared models**. The scenario file labels which part is which.

**Claim ceiling:** the events, magnitudes and publication/revision times are real USGS records, while the roles, latencies, rule and decision time are models, so a FAIL here shows that *this modelled operator* would have decided on a stale *real* magnitude under a *modelled* delay. It does not show that any real person or agency did.

## Files

| Path | What it is |
|---|---|
| `worldline/adapters/usgs.py` | `fetch_window`, `snapshot`, `to_scenario` (plus `verify_snapshot`, `build_timeline`) |
| `data/real/usgs/20250729T2320Z_20250730T0140Z_m5.5.json` | Pinned snapshot with a provenance block (64 KB) |
| `scenarios/real/usgs_20250729T2320Z_20250730T0140Z_m5.5.yaml` | Generated scenario. Its header and its `provenance:` key list what is REAL and what is MODEL |
| `tests/test_usgs_adapter.py` | Offline tests against the committed snapshot |
| `experiments/out_real/` | Output of the chain in "Results" |

## The window and why

**2025-07-29T23:20:00Z .. 2025-07-30T01:40:00Z, `minmagnitude=5.5`** (2 h 20 min). This covers the M8.8 Kamchatka Peninsula earthquake (`us6000qw60`, origin 2025-07-29T23:24:52.483Z) and its first aftershocks. I chose it through the FDSN API:

- It is a notable sequence: 20 catalogue events at M5.5+ in the window (103 at M4.5+, 67 at M5.0+).
- The mainshock has a real, timestamped magnitude history. ComCat retains these origin-product versions (from the `includesuperseded=true` detail document):

  | Published (UTC) | Contributor | Magnitude |
  |---|---|---|
  | 23:34:08.161 | `pt` | 8.0 Mi |
  | 23:36:06.206 | `at` | 8 Mi |
  | 23:43:56.040 | `us` | 8.0 mww |
  | 00:22:51.040 | `us` | **8.7** mww |
  | 01:32:38.040 | `us` | **8.8** mww |

  Revisions after the window (2025-07-31 onwards) stay at 8.8 and fall outside the horizon.
- At M5.5 the snapshot stays small. At M4.5 the window would have needed 103 detail documents (the mainshock's detail document alone is 1.8 MB) without adding any revision that could be known inside the window.

## What is real, what is modelled

**REAL** (copied from USGS, never edited):
- Event ids, origin times, and first-published locations and depths.
- Place labels. These are today's catalogue text; the mainshock's label "2025 Kamchatka Peninsula, Russia Earthquake" was assigned after the fact.
- The first-published magnitude of each event, as a `quake_report` at its publication time.
- Each later change of the shown magnitude, as a `correction` at its publication time.
- `at_ns` is the publication time minus the window start, in nanoseconds. USGS timestamps have millisecond resolution.

**MODEL** (declared parameters, chosen by us):
- `field_ops`: fifo, 5 s + up to 2 s jitter.
- `regional_hq`: network ordering, 60 s + up to 30 s jitter.
- `evaluator`: 0 s.
- Actor `duty_seismologist` (role `regional_hq`), rule `advisory_or_hold`:
  `count(kind='quake_report', payload.mag>=8.5, not_corrected) >= 1 or count(kind='correction', payload.mag>=8.5, not_corrected) >= 1` → `issue_advisory`, else `hold`.
  The threshold M8.5 and the option names are labels of the model, not a real USGS, PTWC or other agency procedure.
- Decision time: **3892 s** after window start (2025-07-30T00:24:52Z). It is computed as the publication of the first correction that flips the rule over truth (`corr-us6000qw60-1`, 3771.040 s) + 60 s base + 30 s jitter + 30 s margin, rounded up. This places the decision shortly after a real correction, so the latent defect exists in the real timeline.
- The seed only drives the modelled jitter.

**Modelling choices about the real data** (stated in the provenance):
- **Shown magnitude.** At any instant, the shown magnitude is that of the current origin product with the highest `preferredWeight` (ties go to the latest update). This approximates ComCat's preferred-origin selection. A correction is emitted only when this shown value changes. That is why the `at` and first `us` versions, also 8.0, produce no correction.
- **Correction chain.** Each correction's `payload.corrects` points to the estimate it supersedes: the report for the first revision, the previous correction after that. `payload.report` always names the report. As a result, `not_corrected` selects the latest estimate. A flat `corrects = report id` would let a superseded 8.7 still count after a later revision.
- **Excluded events.** 14 of the 20 catalogue events are excluded: each one's first origin product was published after the window end (later catalogue additions and reviews). Nobody could have known them inside the window. They are listed with reasons under `provenance.excluded`.
- **Fallback.** For an event without an origin-product history, the adapter would use the summary `time` with the final magnitude, plus one correction at `updated` if that falls inside the window, and label it. In this window every included event has a history (`events_without_history: 0`).
- **Limits of the history.** Superseded versions are what ComCat retains today. A version deleted before our fetch is invisible to us.

The result is 6 real reports and 2 real corrections (8.0 → 8.7, then 8.7 → 8.8).

## Provenance

The snapshot's `provenance` block holds:
- `source_url`, the exact FDSN query.
- `fetched_at_utc` (2026-10-27T11:17:26Z).
- `sha256`: `e2315774e081e531087af74901450134fd563d108aed8405a5b41bc485785d98`, computed over the canonical JSON (sorted keys, compact, UTF-8) of `payload`.
- `license: "USGS, public domain"`.
- `raw_responses`: the SHA-256 and size of every raw HTTP body. The detail documents were trimmed to origin products, and these hashes keep the trimming auditable.
- `notes`.

`to_scenario` refuses a snapshot whose hash does not match. The scenario repeats the snapshot hash under `provenance.snapshot_sha256`. The loader ignores unknown top-level keys, so `Scenario` is unchanged; the same applies to the existing `specimen:` key.

## Regenerate

```bash
# offline, from the committed snapshot (byte-identical YAML; checked by a test)
uv run worldline real usgs --start 2025-07-29T23:20:00Z --end 2025-07-30T01:40:00Z --minmag 5.5 --offline

# re-fetch from USGS (overwrites the snapshot; fetched_at_utc and possibly the hash change if ComCat changed)
uv run worldline real usgs --start 2025-07-29T23:20:00Z --end 2025-07-30T01:40:00Z --minmag 5.5

# model knobs: --threshold, --decision-at <s>, --out, --snapshot-dir
```

## Results (as printed by the CLI, 2026-10-27)

Baseline (`worldline run`): the correction reaches `regional_hq` at 3850.111 s, which is 42 s before the decision.

```
decision duty_seismologist/advisory_or_hold at 3892s -> issue_advisory (knew 2 events)
PASS    absence.no_corrupted_in_decision
PASS    decision.consistent_with_truth  all 1 decision(s) match the rule evaluated over truth
PASS    epistemic.no_future_knowledge
```

`uv run worldline explore scenarios/real/usgs_20250729T2320Z_20250730T0140Z_m5.5.yaml --budget 150 --seed 42 --out experiments/out_real`

```
scenario usgs_20250729T2320Z_20250730T0140Z_m5_5  root trace digest sha256:4f46c3762c747425cea6be2005c1957a54664eedc979ff8a3ea58b27028dc1d7
| strategy | nodes | failing | fail/100 | first decision FAIL idx | wall ms |
| guided   | 88    | 43      | 49.4     | 1                       | 119     |
| random   | 63    | 33      | 53.2     | 3                       | 81      |
```

Fewer than 150 nodes were run because some draws were rejected or duplicated. `exploration.json` records `n_rejected` = 62 (guided) and 50 (random): these schedules violate the time rule, for example a clock skew that would deliver an event before it happened. The rest of the shortfall is duplicate schedules, which are skipped. The scenario has only 8 truth events, so the candidate pool is small. The high failure rate reflects how few events there are, not how hard the problem is. Here guided finds the first decision FAIL sooner (index 1 vs 3) but does not have a higher failure rate than random.

`uv run worldline minimize experiments/out_real/exploration.json --out experiments/out_real`

```
schedule size 3 -> 1  oracle calls 3 (cache hits 0)  one-minimal verified: True
  drop {'event_id': 'corr-us6000qw60-1'} {}
witness ['corr-us6000qw60-1']
explanation: decision decision:duty_seismologist:advisory_or_hold (advisory_or_hold) chose 'hold' but truth as of 3892000000000 gives 'issue_advisory'; actor had not received ['corr-us6000qw60-1']
causal diff vs root: 1 decision(s) changed, truth identical: True
```

`uv run worldline genome extract experiments/out_real/minimal_schedule.json --out experiments/out_real/genome.json`

```
genome genome:186fd72dd8c8145c  (EMPIRICALLY_VALIDATED; ...)
mechanism: drop the last before decision 'correction' event (corrects dependency) seen by the decision role, so that decision.consistent_with_truth fails.
```

Port genome on the real scenario: `uv run worldline genome challenge experiments/out/genome.json scenarios/real/usgs_...yaml --seed 42 --out experiments/out_real/challenge_port_genome_on_real.json`

```
genome genome:94f0ad69dd5483d0 on usgs_20250729T2320Z_20250730T0140Z_m5_5: reproduced yes
  reproduced on usgs_...: decision ... chose 'hold' but truth as of 3892000000000 gives 'issue_advisory'; actor had not received ['corr-us6000qw60-1']
control convoy_lostlink_immune: reproduced no
challenge success (reproduced on primary, not on controls): True
```

The instantiated schedule delays `corr-us6000qw60-1` to `regional_hq` by 42.89 s, just past the decision.

Real genome on port_alert: `uv run worldline genome challenge experiments/out_real/genome.json scenarios/port_alert.yaml --seed 42 --out experiments/out_real/challenge_real_genome_on_port.json`

```
genome genome:186fd72dd8c8145c on port_alert: reproduced yes
  reproduced on port_alert: decision decision:duty_officer:escalate_or_monitor (escalate_or_monitor) chose 'escalate' but truth as of 1500000000000 gives 'monitor'; actor had not received ['correction-001', 'ais-006', 'patrol-001']
control convoy_lostlink_immune: reproduced no
challenge success (reproduced on primary, not on controls): True
```

**Reading:**
- The port_alert mechanism (delay the last decision-relevant correction past the decision) reproduces on the real timeline. It needs a delay of about 42 s on the modelled `regional_hq` path.
- The real-data genome (drop that correction) reproduces on port_alert, and the immune control stays clean in both directions.
- The direction of the error differs, and the mechanism does not care. In port_alert a downgrade is missed, so the operator over-reacts. In the USGS case an upgrade (8.0 → 8.7) is missed, so the operator under-reacts.
- Both challenges run in the shared Event v1 substrate. The real part is only the truth stream; the delay that exposes the defect is modelled.

## Known limitations

- There is one window, one rule-flipping correction, and one deciding actor.
- Preferred-magnitude selection is approximated from `preferredWeight`. Real-time display and feed latency are not modelled from data.
- Publication time in ComCat is not delivery time to any real person. That gap is exactly what the declared roles model.
