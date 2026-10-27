import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test as base, expect, type Page } from "@playwright/test";

export const UI_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
/** The build under test; must match what playwright.config.ts serves (E2E_DIST, default dist/). */
export const DIST = resolve(UI_DIR, process.env.E2E_DIST ?? "dist");
export const OUT = join(UI_DIR, "e2e", ".output");

/** Every route the viewer exposes (hash router in src/App.tsx). */
export const ROUTES = ["#/", "#/story/2", "#/worlds", "#/timeline", "#/mechanism", "#/evidence"] as const;

/** Reads a JSON file exactly as the built app serves it (dist/data/<name>), so assertions never hardcode numbers. */
export function distJson<T = any>(name: string): T {
  const p = join(DIST, "data", name);
  if (!existsSync(p)) throw new Error(`${p} is missing: run \`npm run build\` before \`npm run test:e2e\``);
  return JSON.parse(readFileSync(p, "utf8")) as T;
}

/** The numbers the Story leads with, derived from the data the same way src/views/Story.tsx does. */
export function keyNumbers() {
  const ex = distJson("exploration.json");
  const runs = Object.values(ex.runs) as { nodes: { branch_id: string }[]; n_failing: number }[];
  const totalWorlds = runs.reduce((a, r) => a + r.nodes.filter((n) => n.branch_id !== "root").length, 0);
  const totalFailing = runs.reduce((a, r) => a + r.n_failing, 0);
  const minimal = distJson("minimal_schedule.json");
  const bundle = distJson("bundle.json");
  return {
    totalWorlds,
    totalFailing,
    guidedFailing: ex.comparison?.guided?.n_failing as number | undefined,
    randomFailing: ex.comparison?.random?.n_failing as number | undefined,
    minimalChanges: minimal.minimal.length as number,
    originalChanges: minimal.original.length as number,
    records: bundle.records.length as number,
  };
}

/** Same shape as service/app.py healthz(). */
export const HEALTHZ = { status: "ok", public_key: true, data: true, viewer: false };

export const VERIFIER_HOST = /worldline-verifier[^/]*\.run\.app/;

/**
 * Keeps every test hermetic: Google Fonts CSS is answered with an empty stylesheet (font files never load,
 * the system fallback renders). The live verifier is never contacted: /api/healthz is answered "ok" and any
 * other verifier call a test has not mocked is answered 503.
 * Tests that exercise /api/verify register their own route afterwards (later routes take precedence).
 */
export async function isolateNetwork(page: Page) {
  await page.route(/fonts\.googleapis\.com/, (r) => r.fulfill({ status: 200, contentType: "text/css", body: "" }));
  await page.route(/fonts\.gstatic\.com/, (r) => r.fulfill({ status: 204, body: "" }));
  await page.route(VERIFIER_HOST, (r) => {
    const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "*" };
    if (r.request().method() === "OPTIONS") return r.fulfill({ status: 204, headers: cors });
    if (/\/api\/healthz/.test(r.request().url())) return r.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(HEALTHZ) });
    return r.fulfill({ status: 503, contentType: "application/json", headers: cors, body: JSON.stringify({ error: "verifier not mocked in this test" }) });
  });
}

/** Waits until the data has loaded and no skeleton or load error is on screen. */
export async function waitForApp(page: Page) {
  await expect(page.locator("#main")).toBeVisible();
  await expect(page.locator("#main [aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByRole("alert").filter({ hasText: /didn.t load/i })).toHaveCount(0);
  await page.waitForLoadState("networkidle");
}

/** Collects console errors, uncaught page errors and failed same-origin requests for the lifetime of the page. */
export function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console.error: ${m.text()}`);
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("response", (r) => {
    if (r.url().startsWith("http://localhost") && r.status() >= 400) errors.push(`HTTP ${r.status()} ${r.url()}`);
  });
  return errors;
}

export const test = base.extend<{ errors: string[] }>({
  errors: async ({ page }, use) => {
    const errors = watchErrors(page);
    await isolateNetwork(page);
    await use(errors);
  },
});
export { expect };
