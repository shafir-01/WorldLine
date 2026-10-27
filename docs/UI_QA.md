> Status: all findings F1 to F6 below were fixed in the viewer after this audit; the suite passes 42 of 42 with no axe violations at any impact level. The findings are kept as the record of what the audit caught.

# UI QA: end-to-end, accessibility and performance checks for the viewer

The static viewer in `ui/` has a Playwright suite in `ui/e2e/`. It runs against `vite preview` serving the **built** bundle, so it tests what ships, not the dev server. The CI job `ui-e2e` (`.gitlab/ci/ui.yml`) runs it on every branch, MR and tag pipeline.

## How to run

Prerequisites: Node 20+ (tested on Node 24.14), and the dependencies from `npm ci`.

```bash
cd ui
npm ci
npx playwright install chromium     # once per machine; CI uses the browsers baked into the image
npm run build                       # copy-data + typecheck + vite build -> dist/
npm run test:e2e                    # serves dist/ on http://localhost:4317 and runs the suite
npx playwright show-report e2e/.output/report
```

Useful variants:

| Command | What it does |
|---|---|
| `npx playwright test e2e/story.spec.ts` | One file |
| `npx playwright test -g "axe"` | Tests whose title matches |
| `npx playwright test --headed --workers=1` | Watch it run |
| `npm run test:e2e:ci` | What CI runs (`--forbid-only`; with `CI=true` it adds a JUnit report and uses 2 workers) |
| `node e2e/build-snapshot.mjs && E2E_DIST=e2e/.output/dist npm run test:e2e` | Build into `e2e/.output/dist` and test that, so a teammate rebuilding `dist/` mid-run cannot break your run |
| `E2E_PORT=5000 npm run test:e2e` | Use another port (default 4317; an existing server on the port is never reused) |

All output lands in `ui/e2e/.output/` (gitignored by `ui/e2e/.gitignore`): `report/` (HTML report), `test-results/` (traces, screenshots and error context of failed tests), `axe/<route>-<scheme>.json` (every axe violation, all impacts), `budget-js.json`, `budget-fcp.json` and, in CI, `junit.xml`.

The tests never hardcode the run's numbers: they read `dist/data/*.json` (the same files the page fetches) and assert that the DOM shows them. The suite therefore passes on either data source `copy-data` picks (`experiments/out` locally when present, `ui/fixtures` in CI).

Network: every test is hermetic. Google Fonts CSS is stubbed with an empty stylesheet, the live verifier's `/api/healthz` is answered "ok", and any other verifier call a test has not mocked gets a 503. Nothing leaves the machine.

## What is covered

42 tests in Chromium (desktop 1280x900 unless noted).

| File | Test | Asserts |
|---|---|---|
| `routes.spec.ts` | renders `#/`, `#/story/2`, `#/worlds`, `#/timeline`, `#/mechanism`, `#/evidence` (6) | Data loads (no skeleton, no load-error alert), a heading is visible, and there are **no console errors, page errors or failed same-origin requests** over 1.5 s after load |
| | `#/story/2` deep link scrolls to beat 2 | `#beat-2` ends up within 300 px of the viewport top |
| `story.spec.ts` | shows the key numbers from the run data | Hero shows total replayed worlds (non-root nodes over all runs in `exploration.json`) and total failing worlds (sum of `n_failing`); the story shows the bundle record count, minimal and original schedule sizes (`minimal_schedule.json`), guided and random failing counts (`comparison`) |
| | AUTHORIZED/DENIED toggle | The banner (section text minus its buttons) says AUTHORIZED and does not name a record; after DENIED it says DENIED and names the record index from `authorization_denied.json` reasons (`...@<n>`); switching back restores it; `aria-selected` follows |
| | "Verify this bundle" ok / tampered (2) | `page.route` mocks `POST /api/verify` with the `service/app.py` response shape. ok: an intact/verified verdict appears and no tampered/rejected one; tampered: a tampered/rejected verdict appears and the finding names record 1. Also checks exactly one POST was sent and its body is the bundle the page serves (`bundle.json`, or `bundle_tampered.json` when the "edited copy" shortcut is used). Matches the button as "Verify this bundle" or "Verify bundle" |
| `keyboard.spec.ts` | Tab reaches the primary controls | From a blurred page, Tab reaches: skip link, the five nav buttons, "See it work", "Verify a release", the DENIED toggle and the Verify button (600 presses max) |
| | focus is visible on the primary nav | A keyboard-focused nav button has an outline or box-shadow |
| | Enter on a nav button switches the view | Enter on each nav button changes the hash and `aria-current="page"` |
| | skip link keeps the current view | On `#/worlds`, Tab then Enter on "Skip to content" must leave the user on Worlds |
| | Escape closes the mobile menu | At 390 px the menu opens with Enter and closes with Escape |
| `layout.spec.ts` | no horizontal overflow at 390 px, per route (6) | `documentElement.scrollWidth` and `body.scrollWidth` <= viewport width; on failure lists the elements that stick out and are not inside a scroll/clip container |
| | renders dark on each route (6) | With `prefers-color-scheme: dark` emulated, the page background changes, is dark (relative luminance < 0.1) and headings are light (> 0.4); screenshots of `#/` and `#/evidence` are attached to the report |
| `a11y.spec.ts` | axe on each route, light and dark (12) | axe-core with tags `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa`. **Fails only on `serious` or `critical`**; all violations are written to `e2e/.output/axe/` and attached to the report |
| `perf.spec.ts` | total JS under 300 kB gzip | Sum of gzip (level 9) sizes of every `.js` in the build |
| | FCP under 2 s | Median `first-contentful-paint` of 3 cold loads of `#/` (fresh browser context each) |

## Measured budgets

Measured on 2026-10-27, Windows 11, Node 24.14, Chromium 156 headless shell (Playwright 1.64.1), `vite preview` on localhost, no CPU or network throttling, fonts stubbed.

| Budget | Limit | Measured |
|---|---|---|
| Total JS, gzip | 300 kB | **95.3 kB** (one chunk, 307.8 kB raw). CSS: 14.9 kB gzip |
| First contentful paint, `#/` | 2000 ms | **64 ms** median (runs: 60 / 64 / 68 ms and 60 / 64 / 64 ms) |
| Data ready (informational) | none | ~705 ms from navigation to skeleton gone and network idle (`networkidle` alone waits 500 ms) |

These are best-case numbers: a local server and a fast machine. They catch regressions (a heavy dependency, a render-blocking script) rather than predict a phone on 4G. The JS budget leaves about 200 kB of headroom.

## Open findings

Found by the suite on the build of 2026-10-27 (ui/src as of about 12:30, while the Story and Inspect views were being reworked). These are left for the owners of `ui/src`; the tests stay red until they are fixed. Selectors come from axe and from the overflow probe.

### F1. axe: color-contrast (serious), 11 of 12 route/scheme scans

Only `#/evidence` in dark mode is clean. WCAG AA needs 4.5:1 for normal text. Grouped by colour pair:

| Theme | Foreground on background | Ratio | Where (selectors) |
|---|---|---|---|
| light | `#c1c0bd` on `#fdfdfc` | 1.78 | Story beat 3, removed interventions: `.iv-removed.iv-card > .iv-op`, `.iv-removed.iv-card > .iv-id` |
| light | `#b3b2af` on `#ffffff` | 2.12 | Mechanism: `.removed > .ix-iv-status` |
| light | `#aeadab` on `#fdfdfc` | 2.2 | `.iv-removed.iv-card > .iv-status` |
| light | `#9b9b99` on `#f8f7f5` | 2.6 | Mechanism: `.op-duplicate`, `.op-reorder` (op tags) |
| light | `#949495` on `#fdfdfc` | 2.97 | `.iv-removed.iv-card > .iv-sentence` |
| light | `#75736d` on `#e8f1fc` / `#eeedfd` / `#f2f1ed` / `#fbf2f0` / `#f6f6f3` | 4.1 to 4.37 | the muted text colour on tinted surfaces: `.beat-num-of`, `.beat-num-label` (every beat header), `.seg-btn[role=tab] > .seg-sub`, `.ix-seg-btn > .ix-seg-sub`, `.sc-meta`, `.ix-banner-body > .small.muted`, `.ix-outcome .muted`, `.ix-verdict > .ix-term`, `.ix-witness > .muted`, `.is-sel .ix-order`, `.is-sel .ix-term` |
| light | `#7c7c7d` on `#ffffff` | 4.16 | `.removed > .ix-iv-text` |
| light | `#d03b3b` on `#fdf3f2` / `#fbf2f0` | 4.36 to 4.41 | `.kept > .ix-iv-status`, `.wit` (fail red on pink tint) |
| dark | `#4d4c4b` on `#131316` | 2.16 | `.iv-removed.iv-card > .iv-op`, `.iv-removed.iv-card > .iv-id` |
| dark | `#595855` on `#17171a` | 2.51 | `.removed > .ix-iv-status` |
| dark | `#646462` on `#131316` | 3.12 | `.iv-removed.iv-card > .iv-status` |
| dark | `#d03b3b` on `#281719` / `#221314` / `#17171a` / `#0e0e10` | 3.56 to 4.01 | `.kept > .ix-iv-status`, `.wit`, `.fail-ink`, `.ix-answer > .bad` (fail red needs a lighter dark-mode value) |
| dark | `#767571` on `#1b1b1f` | 3.72 | `.op-duplicate`, `.op-reorder` |
| dark | `#7a7979` on `#131316` | 4.27 | `.iv-removed.iv-card > .iv-sentence` |

Two fixes cover most nodes: darken the light-theme muted token so it clears 4.5:1 on the tinted surfaces (not just on white), and give `--fail` a lighter dark-mode value. "Removed" interventions are deliberately faded, but they are still text a reader needs; fade them with a strike-through or an icon instead of near-background grey.

### F2. axe: definition-list (serious), `#/` and `#/story/2`

`dl.stat-tiles` (hero, `src/views/Landing.tsx`) has `div.tile` children that contain `p.tile-note` next to `dt`/`dd`. Inside a `dl`, a `div` may only hold `dt` and `dd`. Move the note into the `dd`, or make the tiles a list.

### F3. axe: label-content-name-mismatch (serious), `#/`, `#/story/2`, `#/timeline`

The accessible name must contain the visible label (WCAG 2.5.3), so voice users can say what they see.
- Story progress buttons `ol.progress-steps .ps` (`.ps-active`, `.ps-done`, `li:nth-child(n) > .ps`): visible "Replay", aria-label "Step 1 of 7: One hour, replayed ... ways". Start the label with the visible text, e.g. "Replay, step 1 of 7".
- Timeline decision markers `.dec[role="button"]`, `.dec-differs` (`#/timeline`): same pattern.

### F4. axe: nested-interactive (serious), `#/`, `#/story/2`, `#/timeline`

- `svg.fan` (Story beat 1) has `role="img"` but contains focusable `g.fan-node[role=button]`. An image cannot have interactive children; use `role="group"` with an `aria-label` on the svg.
- `.tl` (`#/timeline`): an interactive container with focusable descendants (axe notes that `tabindex="-1"` inside does not hide them from assistive tech).

### F5. Skip link re-routes to the Story (`keyboard.spec.ts`, "skip link keeps the current view")

`a.skip-link[href="#main"]` (`src/App.tsx`) sets `location.hash` to `#main`. The app routes on the hash, so `readHash()` maps `main` to the Story: on any Inspect view, using the skip link throws the user back to the Story. Handle it in JS (`preventDefault()`, then focus `#main` with `tabIndex={-1}`) instead of following the href.

### F6. Horizontal overflow on `#/worlds` at 390 px (`layout.spec.ts`)

The document is 548 to 555 px wide at a 390 px viewport (the number depends on the data). The only element past the edge that is not inside a scroll or clip container is `footer.footer`. Most likely a min-content child of the Worlds view (the branch tree) widens the app's grid or flex track: the tree itself is clipped by its scroller, but the track, and the footer in it, grow. Fix with `min-width: 0` on the column or track that holds `main`, or `minmax(0, 1fr)`. The other five routes pass.

### Checks that pass

No console errors on any route; the key numbers match the data; the gate toggle names the tampered record; verify ok and tampered both render correctly against the mocked API; Tab reaches every primary control; focus is visible; Enter switches views; Escape closes the mobile menu; no overflow at 390 px on five of six routes; dark mode renders on all six routes; both performance budgets hold with a wide margin.

## Flakiness

Two consecutive runs, each on a fresh build (`node e2e/build-snapshot.mjs`, then the full suite): **29 passed, 13 failed, the same 13 both times** (11 axe scans, F5, F6), 0 flaky, 0 retries configured. The run against `ui/fixtures` data gave the same results for the non-axe tests. The one instability seen during development was external: another process rebuilt `ui/dist` mid-run, which briefly removed the data files and failed tests at random. Use `E2E_DIST` (above) when others may be building in the same checkout. CI is not affected, since each job has its own checkout.

## Maintenance

- **Bump Playwright:** change `@playwright/test` in `ui/package.json` and the image tag `mcr.microsoft.com/playwright:v<version>-noble` in `.gitlab/ci/ui.yml` together. A mismatch makes the job fail with "Executable doesn't exist".
- **UI wording changes:** the tests look for meaning, not exact copy. They read the AUTHORIZED/DENIED words, a record index, verdict words such as intact/verified or tampered/rejected, and the button names listed above. If a label is renamed, update the matching regex in `story.spec.ts` or `keyboard.spec.ts`.
- **Raising the axe bar:** to also fail on `moderate`, add it to `BLOCKING` in `a11y.spec.ts`.
