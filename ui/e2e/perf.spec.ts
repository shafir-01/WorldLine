import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { gzipSync } from "node:zlib";
import { DIST, OUT, expect, isolateNetwork, test, waitForApp } from "./helpers";

/** Budgets (docs/UI_QA.md). kB = 1000 bytes. */
const JS_GZIP_BUDGET = 300_000;
const FCP_BUDGET_MS = 2_000;
const FCP_RUNS = 3;

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const kb = (n: number) => Math.round(n / 100) / 10;

function record(key: string, value: unknown) {
  mkdirSync(OUT, { recursive: true });
  const file = join(OUT, `budget-${key}.json`);
  writeFileSync(file, JSON.stringify(value, null, 2));
}

test.describe("performance budget", () => {
  test("total JS shipped in dist/ is under 300 kB gzip", async ({}, info) => {
    const files = walk(DIST).filter((f) => /\.(m?js)$/.test(f));
    expect(files.length, "dist/ contains JS (did you run `npm run build`?)").toBeGreaterThan(0);
    const rows = files.map((f) => {
      const raw = readFileSync(f);
      return { file: relative(DIST, f).replace(/\\/g, "/"), raw: raw.length, gzip: gzipSync(raw, { level: 9 }).length };
    });
    const total = rows.reduce((a, r) => ({ raw: a.raw + r.raw, gzip: a.gzip + r.gzip }), { raw: 0, gzip: 0 });
    const css = walk(DIST).filter((f) => f.endsWith(".css")).reduce((a, f) => a + gzipSync(readFileSync(f), { level: 9 }).length, 0);
    const summary = { files: rows, totalRawBytes: total.raw, totalGzipBytes: total.gzip, cssGzipBytes: css, budgetGzipBytes: JS_GZIP_BUDGET };
    record("js", summary);
    info.annotations.push({ type: "js", description: `JS ${kb(total.raw)} kB raw, ${kb(total.gzip)} kB gzip (budget ${kb(JS_GZIP_BUDGET)} kB); CSS ${kb(css)} kB gzip` });
    console.log(`[budget] JS ${kb(total.raw)} kB raw / ${kb(total.gzip)} kB gzip over ${rows.length} file(s); CSS ${kb(css)} kB gzip`);
    expect(total.gzip).toBeLessThan(JS_GZIP_BUDGET);
  });

  test("first contentful paint on the preview server is under 2 s", async ({ browser }, info) => {
    const runs: { fcp: number; domContentLoaded: number; load: number; dataReady: number; jsTransferred: number }[] = [];
    for (let i = 0; i < FCP_RUNS; i++) {
      // Fresh context each run: cold HTTP cache, like a first visit.
      const ctx = await browser.newContext({ baseURL: info.project.use.baseURL, viewport: { width: 1280, height: 900 }, reducedMotion: "reduce" });
      const page = await ctx.newPage();
      await isolateNetwork(page);
      let jsTransferred = 0;
      page.on("response", async (r) => {
        if (/\.m?js(\?|$)/.test(r.url()) && r.url().startsWith("http://localhost")) jsTransferred += (await r.body().catch(() => Buffer.alloc(0))).length;
      });
      const t0 = Date.now();
      await page.goto("#/", { waitUntil: "load" });
      await waitForApp(page);
      const dataReady = Date.now() - t0;
      const m = await page.evaluate(
        () =>
          new Promise<{ fcp: number; domContentLoaded: number; load: number }>((resolve) => {
            const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming;
            const read = () => {
              const fcp = performance.getEntriesByName("first-contentful-paint")[0]?.startTime ?? -1;
              return { fcp, domContentLoaded: nav.domContentLoadedEventEnd, load: nav.loadEventEnd };
            };
            const now = read();
            if (now.fcp >= 0) return resolve(now);
            new PerformanceObserver(() => resolve(read())).observe({ type: "paint", buffered: true });
          }),
      );
      runs.push({ ...m, dataReady, jsTransferred });
      await ctx.close();
    }
    const fcps = runs.map((r) => r.fcp).sort((a, b) => a - b);
    const median = fcps[Math.floor(fcps.length / 2)];
    record("fcp", { runs, medianFcpMs: median, budgetMs: FCP_BUDGET_MS, note: "vite preview on localhost, Google Fonts stubbed, cold cache per run, no CPU/network throttling" });
    info.annotations.push({ type: "fcp", description: `FCP median ${Math.round(median)} ms over ${FCP_RUNS} cold runs (${fcps.map(Math.round).join(", ")} ms); budget ${FCP_BUDGET_MS} ms` });
    console.log(`[budget] FCP ${fcps.map(Math.round).join(" / ")} ms (median ${Math.round(median)}); data-ready ${runs.map((r) => r.dataReady).join(" / ")} ms`);
    expect(fcps.every((f) => f >= 0), "a first-contentful-paint entry was recorded").toBe(true);
    expect(median).toBeLessThan(FCP_BUDGET_MS);
  });
});
