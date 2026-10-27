import type { Locator, Page } from "@playwright/test";
import { HEALTHZ, VERIFIER_HOST, distJson, expect, keyNumbers, test, waitForApp } from "./helpers";

/** Matches `n` as a whole number (not part of 1234 or 12.5). */
/** The Story's verify action (labelled "Verify this bundle" or "Verify bundle" across UI revisions). */
export const VERIFY_BUTTON = /^\s*verify (this )?bundle\s*$/i;

const num = (n: number) => new RegExp(`(^|[^\\d.,])${n}([^\\d.,]|$)`);

/** The <section> that contains `inner`, resolved to a stable id-based locator (button labels change while busy). */
async function sectionOf(page: Page, inner: Locator): Promise<Locator> {
  const id = await inner.evaluate((el) => el.closest("section[id]")?.id ?? "");
  return id ? page.locator(`#${id}`) : page.locator("#main");
}

test.describe("Story", () => {
  test("shows the key numbers from the run data", async ({ page, errors }) => {
    const k = keyNumbers();
    await page.goto("#/");
    await waitForApp(page);
    const hero = page.locator("section").filter({ has: page.locator("h1") }).first();
    await expect(hero).toBeVisible();
    const heroText = await hero.innerText();
    expect(heroText, `hero shows total replayed worlds = ${k.totalWorlds} (exploration.json: non-root nodes over all runs)`).toMatch(num(k.totalWorlds));
    expect(heroText, `hero shows total failing worlds = ${k.totalFailing} (exploration.json: sum of runs[].n_failing)`).toMatch(num(k.totalFailing));
    const story = await page.locator("#main").innerText();
    expect(story, `story shows the bundle record count = ${k.records} (bundle.json records)`).toMatch(num(k.records));
    expect(story, `story shows the minimal schedule size = ${k.minimalChanges} (minimal_schedule.json minimal)`).toMatch(num(k.minimalChanges));
    expect(story, `story shows the original schedule size = ${k.originalChanges} (minimal_schedule.json original)`).toMatch(num(k.originalChanges));
    if (k.guidedFailing != null) expect(story, `story shows guided failing worlds = ${k.guidedFailing} (comparison.guided.n_failing)`).toMatch(num(k.guidedFailing));
    if (k.randomFailing != null) expect(story, `story shows random failing worlds = ${k.randomFailing} (comparison.random.n_failing)`).toMatch(num(k.randomFailing));
    expect(errors).toEqual([]);
  });

  test("AUTHORIZED/DENIED toggle changes the banner and names the tampered record", async ({ page, errors }) => {
    const denied = distJson("authorization_denied.json");
    const authorized = distJson("authorization.json");
    expect(authorized.decision).toBe("AUTHORIZED");
    expect(denied.decision).toBe("DENIED");
    const tamperedIdx = Number(String(denied.reasons.join(" ")).match(/@(\d+)/)?.[1]);
    expect(Number.isInteger(tamperedIdx), "authorization_denied.json names a record index (...@<n>)").toBe(true);

    await page.goto("#/");
    await waitForApp(page);
    const controls = page.locator("button, [role=tab], [role=radio]");
    const deniedBtn = controls.filter({ hasText: /^\s*DENIED/ }).first();
    const authBtn = controls.filter({ hasText: /^\s*AUTHORIZED/ }).first();
    await deniedBtn.scrollIntoViewIfNeeded();
    const section = await sectionOf(page, deniedBtn);

    // Banner text = the section's text minus the toggle and any other buttons.
    const banner = () =>
      section.evaluate((el) => {
        const c = el.cloneNode(true) as HTMLElement;
        c.querySelectorAll("[role=tablist], [role=radiogroup], button").forEach((n) => n.remove());
        return (c.textContent ?? "").replace(/\s+/g, " ");
      });
    const namesRecord = new RegExp(`record\\s*#?\\s*${tamperedIdx}\\b`, "i");

    const before = await banner();
    expect(before).toContain("AUTHORIZED");
    expect(before).not.toMatch(namesRecord);

    await deniedBtn.click();
    await expect.poll(banner).toContain("DENIED");
    const after = await banner();
    expect(after, `DENIED banner names the tampered record #${tamperedIdx}`).toMatch(namesRecord);
    if ((await deniedBtn.getAttribute("aria-selected")) != null) await expect(deniedBtn).toHaveAttribute("aria-selected", "true");
    if ((await deniedBtn.getAttribute("aria-checked")) != null) await expect(deniedBtn).toHaveAttribute("aria-checked", "true");

    await authBtn.click();
    await expect.poll(banner).not.toMatch(namesRecord);
    expect(await banner()).toContain("AUTHORIZED");
    expect(errors).toEqual([]);
  });

  for (const scenario of ["ok", "tampered"] as const) {
    test(`"Verify this bundle" shows the verifier's ${scenario} answer (network mocked)`, async ({ page, errors }) => {
      const bundle = distJson("bundle.json");
      const tamperedBundle = distJson("bundle_tampered.json");
      const cp = bundle.checkpoint ?? {};
      const keyId = cp.signer_key_id ?? "ed25519:test";
      const tamperedIdx = 1;
      // Same shape as service/app.py verify().
      const response =
        scenario === "ok"
          ? { ok: true, findings: [], root: cp.root ?? "sha256:" + "a".repeat(64), signer_key_id: keyId, verifier_key_id: keyId, n_records: bundle.records.length, records: [] }
          : { ok: false, findings: [{ code: "record_edited", index: tamperedIdx, detail: `record ${tamperedIdx} content does not match its hash` }], root: null, signer_key_id: keyId, verifier_key_id: keyId, n_records: tamperedBundle.records.length, records: [] };
      const posted: string[] = [];
      await page.route(VERIFIER_HOST, async (route) => {
        const req = route.request();
        const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "GET, POST, OPTIONS" };
        if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
        if (/\/api\/healthz/.test(req.url())) return route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(HEALTHZ) });
        if (/\/api\/verify/.test(req.url()) && req.method() === "POST") {
          posted.push(req.postData() ?? "");
          return route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(response) });
        }
        return route.fulfill({ status: 404, contentType: "application/json", headers: cors, body: JSON.stringify({ error: "unexpected verifier call in test" }) });
      });

      await page.goto("#/");
      await waitForApp(page);
      const btn = page.getByRole("button", { name: VERIFY_BUTTON });
      await btn.scrollIntoViewIfNeeded();
      await expect(btn).toBeEnabled();
      const section = await sectionOf(page, btn);

      // For the tampered case, pick the page's own edited copy when the UI offers one, so the POST body is checked too.
      const editedCopy = section.getByRole("button", { name: /edited copy/i });
      const usesEditedCopy = scenario === "tampered" && (await editedCopy.count()) > 0;
      if (usesEditedCopy) await editedCopy.first().click();

      // Wording-agnostic: the verdict words must appear more often after the answer than before it.
      const okWords = /\b(intact|verified|signature (is )?valid|chain intact)\b/gi;
      const badWords = /\b(tampered|rejected|not verified|failed verification|invalid)\b/gi;
      const recordWords = new RegExp(`record_edited|record\\s*#?\\s*${tamperedIdx}\\b|#${tamperedIdx}\\b`, "gi");
      const count = async (re: RegExp) => ((await section.innerText()).match(re) ?? []).length;
      const before = { ok: await count(okWords), bad: await count(badWords), rec: await count(recordWords) };

      const req = page.waitForRequest((r) => /\/api\/verify/.test(r.url()) && r.method() === "POST");
      await btn.click();
      await req;

      if (scenario === "ok") {
        await expect.poll(() => count(okWords), { message: "an 'intact/verified' verdict appears" }).toBeGreaterThan(before.ok);
        expect(await count(badWords), "no 'tampered/rejected' verdict appears").toBe(before.bad);
        await expect(section).not.toContainText(/could not reach|verifier is offline/i);
      } else {
        await expect.poll(() => count(badWords), { message: "a 'tampered/rejected' verdict appears" }).toBeGreaterThan(before.bad);
        await expect.poll(() => count(recordWords), { message: `the finding names record ${tamperedIdx}` }).toBeGreaterThan(before.rec);
      }
      expect(posted.length, "exactly one POST to /api/verify").toBe(1);
      const body = JSON.parse(posted[0]);
      const expected = usesEditedCopy ? tamperedBundle : bundle;
      expect(body.records?.length, "the posted body is the bundle the page serves").toBe(expected.records.length);
      expect(JSON.stringify(body.records), usesEditedCopy ? "posted the edited copy" : "posted the loaded bundle").toBe(JSON.stringify(expected.records));
      expect(errors).toEqual([]);
    });
  }
});
