# WORLDLINE viewer (`ui/`)

Static Vite + React + TypeScript viewer over the JSON that the WORLDLINE pipeline writes to `experiments/out/`. No backend, no UI kit: hand-written CSS, plain SVG, light/dark via `prefers-color-scheme`. Built with `base: './'` so it deploys unchanged under a GitLab Pages subpath.

> All scenarios are **synthetic**. A persistent footer says so: "All scenarios synthetic. Transfer is within the shared Event v1 substrate, not across heterogeneous codebases."

## Story (default route `#/`)

The landing view walks the result in seven full-width beats, each with a plain-English headline, two sentences and one visual. Every number comes from the data files:

1. **One history, many worlds**: both exploration runs as a half-circle fan (failing worlds emphasised, the rest quiet), plus guided vs random as two large numbers and a small bar pair.
2. **The world that broke**: the minimal counterexample in three lanes (truth, the deciding role, one other role). The delayed correction is drawn as an arrow sliding past the decision marker. A scrubber with a play button shows what the decider knew at each moment.
3. **One cause**: the ddmin result as "3 interventions tried, 1 explains it", with the removed interventions faded.
4. **A mechanism, not a bug**: the genome as a sentence, a kind → role → decision diagram, and the preconditions as checklist chips.
5. **It happens elsewhere**: the challenge primary (reproduced) and the immune control (not reproduced) as two cards.
6. **Nothing ships on untyped evidence**: the bundle as a stepper of records grouped by producer (agent, kernel), with certainty shown by shape and colour, and an AUTHORIZED / DENIED toggle that flags the edited record.
7. **Check it yourself**: **Verify this bundle** POSTs `data/bundle.json` to the live verifier's `/api/verify` and shows the answer. **Try the edited copy** does the same with `bundle_tampered.json`. The beat also links the release. Endpoints are defined once, in `src/config.ts`.

`#/story/<n>` deep-links to beat *n*. Raw sha256 strings never appear in the default view. Identifiers show as 7-character short ids with a copy button, and the full value is in the tooltip and on the clipboard (`src/components/ShortId.tsx`).

## Inspect views

| View | What it shows | Screenshot |
| --- | --- | --- |
| **Worlds** | Both exploration runs of `exploration.json`. A **guided / random** toggle switches the branch tree (FAIL red diamond, PASS blue circle, UNKNOWN amber broken ring; FAIL nodes sort first; a blue dot marks branches with an exported trace). The **Guided vs random** table shows `n_nodes`, `n_failing`, `failing_rate_per_100`, `first_decision_fail_index` and `wall_ms` from `exploration.comparison`; hover a column for its definition. Hover a node for its schedule, click to select, double-click to open its timeline. | `docs/screens/worlds.png` |
| **Timeline** | One lane per role for any exported trace: root, every failing branch (grouped by strategy), the ddmin **minimal** counterexample and the genome **challenge** counterexample. Each event sits at `event_time_ns` with a thin line to `received_time_ns`; decisions are diamonds; FAIL-witness events are ringed red; delivery policy and visibility come from the embedded `scenario`. The `AS_KNOWN_AT(t)` scrubber dims everything not yet received. **Compare with root** overlays root received-times and lists the causal diff (on by default; disabled for the challenge trace, which is a different scenario). | `docs/screens/timeline.png` |
| **Mechanism** | `minimal_schedule.json`: original vs minimal schedule, `one_minimal_verified` / `self_explaining` flags, oracle calls, reduction steps, the recorded `causal_diff`, and (collapsed) the selection rule with every candidate considered. `genome.json`: mechanism sentence, abstract selectors, preconditions, witness shape, origin. `challenge.json`: the **primary** result (REPRODUCED on `convoy_lostlink`) and each **negative control** (`convoy_lostlink_immune`: preconditions met, not reproduced, shown as the control holding). Branch links open the Timeline; the primary's counterexample trace has its own button. | `docs/screens/mechanism.png` |
| **Evidence** | `bundle.json` as a hash chain (`links[i].prev_hash` per record, signed checkpoint root). Each record carries a certainty badge with a distinct shape **and** colour, a producer tag (kernel vs agent) and, when the authorization satisfied it, a SATISFIED flag. The authorization panel toggles between `authorization.json` (**AUTHORIZED**) and `authorization_denied.json` (**DENIED**, evaluated on `bundle_tampered.json`; the edited record is flagged on the chain). Below: a **Benchmark** card from `benchmark.json` with guided-vs-random and ddmin aggregates (mean / median over seeds), definitions on hover and in an expandable section, and the claim ceiling. | `docs/screens/evidence.png` |

## Run

```bash
cd ui
npm install
npm run dev          # http://localhost:5173 — serves ./fixtures as ./data via a Vite middleware
npm run build        # copy-data + typecheck + vite build -> ui/dist
npm run preview      # serve ui/dist locally
```

Other scripts:

```bash
npm run copy-data    # copies ../experiments/out -> public/data if it has JSON, else ./fixtures
npm run typecheck
```

Screenshots in `docs/screens/*.png` (story, timeline = beat 2, mechanism = beat 4, evidence = beat 6, worlds = Inspect/Worlds) were taken from `npm run preview` with headless Edge at 1440x900:

```powershell
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new --disable-gpu --hide-scrollbars --window-size=1440,900 --virtual-time-budget=10000 --screenshot=..\docs\screens\worlds.png "http://localhost:4173/#/worlds"
```

## How data is loaded

The app fetches **relative** URLs: `new URL("data/<file>", document.baseURI)`. File names are fixed by the pipeline (see the contract below), so `src/data.ts` hardcodes them: it reads `index.json`, then `exploration.json`, `minimal_schedule.json`, `genome.json`, `challenge.json`, `bundle.json`, `authorization.json` in parallel, plus `authorization_denied.json` and `benchmark.json` when present. Traces and verdict files are fetched lazily when a trace is opened in Timeline, resolved through the registry built from `exploration.trace_files`, `minimal_schedule.trace_files` and `challenge.primary.trace_file`.

- **Dev**: `vite.config.ts` registers a small middleware that serves `ui/fixtures/**` at `./data/**`. No copying needed.
- **Build**: `npm run build` runs `copy-data` first, which fills `public/data/` (gitignored). Vite copies `public/` verbatim, so `dist/data/**` ships next to the bundle.
- **CI / Pages**: run the pipeline so `experiments/out/*.json` exists, then `npm run build`; `copy-data` prefers the real output over the fixtures. Publish `ui/dist`.

## Data contract

The real files under `experiments/out/` are authoritative; `src/types.ts` mirrors them and the UI adapts, never the pipeline. Written by `worldline explore | minimize | genome extract | genome challenge | bundle | authorize | benchmark | report`.

| File | Shape (top level) |
| --- | --- |
| `index.json` | Manifest from `worldline report`: `generated_at`, `code_commit`, `environment`, `claim_ceiling`, `claims[] {id, claim, kind}`, `digests {root_trace_digest, minimal_trace_digest, minimal_schedule_hash, genome_id}`, `files {"<path>": {bytes, sha256}}`. It is **not** a logical file map. |
| `exploration.json` | `scenario_id`, `seed`, `budget`, `schedule_size`, `strategies`, `root_digest`, `runs {guided: ExplorationRun, random: ExplorationRun}`, `comparison {strategy: {n_nodes, n_failing, failing_rate_per_100, first_decision_fail_index (null if none), wall_ms}}`, `trace_files {branch_id: {trace, verdicts, strategy?, failing_invariants?}}`. An `ExplorationRun` has `nodes[] {branch_id, parent, schedule, trace_digest, verdicts[], failing_invariants[], cost_ns}`, `failing[]`, `n_nodes`, `n_failing`, `n_rejected`, `rejected[]`, `wall_ns`. Both runs share `root`; traces are exported for root and failing branches only. |
| `minimal_schedule.json` | `target_invariant`, `strategy`, `branch_id` (the failing branch ddmin started from), `original[]`, `minimal[]`, `minimal_branch_id`, `minimal_schedule_hash`, `trace_digest`, `oracle_calls`, `cache_hits`, `one_minimal_verified`, `self_explaining`, `steps[] {size, fails}`, `witness[]`, `explanation`, `selection_rule`, `candidates_considered[]`, `causal_diff {a, b, differs, truth_identical, decisions[], views{role: {received_time_moved[], ...}}}`, `trace_files {minimal, minimal_verdicts}`. |
| `genome.json` | `id`, `violated_invariant`, `mechanism[] {op, selector{kind, role, position, relation}, params}`, `mechanism_sentence`, `preconditions[]`, `witness_shape`, `origin {scenario_id, seed, branch_id, trace_digest, minimal_schedule_hash, invariant_version, code_commit, witness[]}`, `evidence_kind`, `claim_ceiling`. Selectors are abstract: no concrete event ids. |
| `challenge.json` | `genome_id`, `success`, `seed_note`, `claim_ceiling`, `primary: ChallengeResult`, `controls[]: ChallengeResult`. A `ChallengeResult` has `scenario_id`, `target_rule`, `preconditions_met`, `preconditions_report {name: bool}`, `instantiated_schedule[]`, `verdicts[]`, `reproduced`, `counterexample_digest`, `explanation`, and `trace_file` when a counterexample trace was written (`traces/challenge_<scenario>.json`). For a control, "preconditions met, not reproduced" is the success case. |
| `bundle.json` | `format: "worldline.bundle.v1"`, `records[] EvidenceRecord {id, kind, claim, subject, procedure, inputs_hash, result, assumptions[], derived_at, producer}`, `links[] {hash, prev_hash}` (one per record, same index), `checkpoint {root, first_index, last_index, sealed_at, signer_key_id, signature}`. `bundle_tampered.json` is the same bundle with one record edited. |
| `authorization.json` / `authorization_denied.json` | `decision: AUTHORIZED \| DENIED`, `policy` (name), `artifact_digest`, `bundle_file`, `evaluated_at`, `reasons[]`, `satisfied[] {claim_pattern, record_id, kind, producer, ...}`, `missing[]`, `stale[]`, `downgraded[]`. The denied file is the same policy evaluated on `bundle_tampered.json`; its reason names the edited record index (`integrity:record_edited@<i>`). |
| `benchmark.json` | `scenario_id`, `seeds[]`, `budget`, `schedule_size`, `target_invariant`, `claim_ceiling`, `definitions {metric: text}`, `environment`, `exploration {rows[], aggregate {strategy: {metric: {mean, median, n, n_null}}}}`, `minimization {cases[], skipped[], aggregate {metric: {...}, one_minimal_verified_all}}`. |
| `traces/*.json` | `Trace`: `scenario_id`, `seed`, `branch_id`, `fault_schedule_hash`, `truth {events[], digest}`, `views {role: {events[], digest}}`, `decisions[]`, `clock_end`, `scenario` (embedded spec: `roles[] {id, delivery, visibility}`, `actors[] {id, role, decision_rules[]}`, `invariants[]`, `clock`), `digest`. The fault schedule itself is **not** in the trace; the viewer takes it from the branch node, `minimal_schedule.minimal` or `challenge.*.instantiated_schedule`. File names: `traces/root.json`, `traces/root__sha256-<hash>.json` (per `trace_files`), `traces/minimal.json`, `traces/challenge_<scenario>.json`. |
| `verdicts/*.json` | `VerdictRecord[]` for the trace of the same name: `invariant_id`, `invariant_version`, `result`, `witness[]`, `explanation`, `missing_data[]`, `derived_at_ns`, `trace_digest`. |

Routing is by hash: `#/<view>/<trace key>` where the key is a `branch_id` or `challenge:<scenario_id>` for a challenge counterexample.

## Fixtures

`ui/fixtures/` is a snapshot of a real `experiments/out/` run (`port_alert`, seed 42; marked `"synthetic": true` in its `index.json`), kept so `npm run dev` and `npm run build` work before the pipeline has run. To keep it small it ships only the traces the views link to (root, minimal, challenge counterexample and the ddmin candidates); opening any other branch in Timeline shows a "file missing" notice. Refresh it by copying `experiments/out/` over it.

## Project structure

```
ui/
├─ fixtures/              snapshot of experiments/out (index.json, exploration.json, traces/, verdicts/, ...)
├─ scripts/
│  └─ copy-data.mjs       experiments/out or fixtures -> public/data
├─ src/
│  ├─ App.tsx             top nav (Story · Inspect), hash routing (#/view/key), persistent footer
│  ├─ config.ts           verifier URL, release link, footer text (single source)
│  ├─ data.ts             relative fetch, useData, trace registry, useTrace
│  ├─ diff.ts             causal_diff computed client-side (root vs branch)
│  ├─ format.ts           time/hash/intervention formatting
│  ├─ types.ts            shapes of the real pipeline output
│  ├─ components/         Badges (KindGlyph, VerdictPill, ProducerTag), Schedule
│  └─ views/              Story (default), Worlds, Timeline, Mechanism, Evidence (+ Benchmark card)
├─ index.html · vite.config.ts · tsconfig.json · package.json
```
