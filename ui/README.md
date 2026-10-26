# WORLDLINE viewer (`ui/`)

Static Vite + React + TypeScript viewer over the JSON that the WORLDLINE pipeline writes to `experiments/out/`. No backend, no UI kit: hand-written CSS, plain SVG, light/dark via `prefers-color-scheme`. Built with `base: './'` so it deploys unchanged under a GitLab Pages subpath.

> All bundled data is **synthetic** (`"synthetic": true` on every fixture). Transfer claims are within the shared Event v1 substrate, not across heterogeneous codebases.

## Views

| View | What it shows |
| --- | --- |
| **Worlds** | Branch tree (`ExplorationResult`): root at the left, children by fault schedule. FAIL red, PASS green, UNKNOWN grey. Hover shows the schedule, click selects a branch, double-click opens its timeline. |
| **Timeline** | For the selected branch (`Trace`): one lane per role (`harbour_ops`, `coast_guard`, `evaluator`). Each event sits at `event_time_ns` with a thin line to `received_time_ns` (the information lag); decisions are diamonds; FAIL-witness events are ringed red. The `AS_KNOWN_AT(t)` scrubber dims everything not yet received. **Compare with root** overlays root received-times and lists the causal diff (moved received times, presence, decisions that differ). |
| **Mechanism** | The ddmin card ("1 intervention out of N explains the failure"), the `FailureGenome` card (abstract selectors, preconditions, witness shape, origin), and the `ChallengeResult` on the second scenario `convoy_lostlink` with a counterexample mini-timeline. |
| **Evidence** | The `Bundle` as a hash chain. Each record carries a certainty badge with a distinct shape **and** colour (square PROVED_WITHIN_MODEL, circle EMPIRICALLY_VALIDATED, shield ATTESTED, diamond-X FAILED, dashed-? UNKNOWN), a producer tag (kernel vs agent), and the `Authorization` verdict with reasons. A toggle switches to the DENIED case (`stale` + `agent_report_mismatch`). |

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
npm run gen-fixtures # regenerates ./fixtures deterministically (node scripts/gen-fixtures.mjs)
npm run typecheck
```

## How data is loaded

The app fetches **relative** URLs: `new URL("data/<file>", document.baseURI)`. It first reads `./data/index.json`, which lists every other file, then loads `exploration.json`, `minimal.json`, `genome.json`, `challenge.json`, `bundle.json` and `authorization.json` in parallel. Traces (`traces/<branch>.json`) are fetched lazily when a branch is selected in Timeline; only branches listed in `index.files.traces` (root and failing branches) have a trace file.

- **Dev**: `vite.config.ts` registers a small middleware that serves `ui/fixtures/**` at `./data/**`. No copying needed.
- **Build**: `npm run build` runs `copy-data` first, which fills `public/data/` (gitignored). Vite copies `public/` verbatim, so `dist/data/**` ships next to the bundle.
- **CI / Pages**: run the pipeline so `experiments/out/*.json` exists, then `npm run build`; `copy-data` prefers the real output over the fixtures. Publish `ui/dist`.

## Data contract

Types in `src/types.ts` mirror `docs/ARCHITECTURE.md` (Trace, VerdictRecord, BranchNode/ExplorationResult, Intervention/FaultSchedule, FailureGenome, ChallengeResult, EvidenceRecord/Bundle, Authorization). `index.json` has the shape:

```json
{
  "synthetic": true,
  "files": {
    "exploration": "exploration.json",
    "minimal": "minimal.json",
    "genome": "genome.json",
    "challenge": "challenge.json",
    "bundle": "bundle.json",
    "authorization": "authorization.json",
    "traces":   { "root": "traces/root.json", "root/<hash>": "traces/root__<hash>.json" },
    "verdicts": { "root": "verdicts/root.json", "root/<hash>": "verdicts/root__<hash>.json" }
  }
}
```

`authorization.json` holds two cases: `{ "cases": { "authorized": Authorization, "denied": Authorization } }`.

## Fixtures

`scripts/gen-fixtures.mjs` is a tiny deterministic re-implementation of the pipeline for one scenario (`port_alert`, seed 42): 14 truth events, 3 roles with different delivery latencies, 3 decision rules, 5 invariants, 25 branches (3 FAIL, 1 UNKNOWN). The failure is the one ARCHITECTURE.md singles out: a `correction` delayed past the `dispatch_cutter` decision point makes `coast_guard` dispatch on an alert truth had already retracted (`decision_matches_truth` FAIL, witness `e-0010`). ddmin reduces a 3-intervention schedule to that single delay; the genome abstracts it; the challenge reproduces it on `convoy_lostlink`.

## Project structure

```
ui/
├─ fixtures/              synthetic data (index.json, exploration.json, traces/, verdicts/, ...)
├─ scripts/
│  ├─ copy-data.mjs       experiments/out or fixtures -> public/data
│  └─ gen-fixtures.mjs    deterministic fixture generator
├─ src/
│  ├─ App.tsx             left nav, hash routing (#/view/branch), persistent footer
│  ├─ data.ts             relative fetch, useData / useTrace hooks
│  ├─ diff.ts             causal_diff (root vs branch)
│  ├─ format.ts           time/hash/intervention formatting
│  ├─ types.ts            ARCHITECTURE.md shapes
│  ├─ components/         Badges (KindGlyph, VerdictPill, ProducerTag), Schedule
│  └─ views/              Worlds, Timeline, Mechanism, Evidence
├─ index.html · vite.config.ts · tsconfig.json · package.json
```
