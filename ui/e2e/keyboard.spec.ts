import type { Page } from "@playwright/test";
import { expect, test, waitForApp } from "./helpers";

/** Accessible-ish name of the focused element: aria-label, else visible text, else title. */
async function focusedName(page: Page): Promise<string> {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) return "";
    return (el.getAttribute("aria-label") || el.innerText || el.getAttribute("title") || el.tagName).replace(/\s+/g, " ").trim();
  });
}

/** Presses Tab until every pattern has been focused at least once (or `max` presses). Returns what was missed. */
async function tabUntil(page: Page, targets: Record<string, RegExp>, max: number) {
  const pending = new Map(Object.entries(targets));
  const seen: string[] = [];
  for (let i = 0; i < max && pending.size; i++) {
    await page.keyboard.press("Tab");
    const name = await focusedName(page);
    seen.push(name);
    for (const [k, re] of pending) if (re.test(name)) pending.delete(k);
  }
  return { missing: [...pending.keys()], seen };
}

test.describe("keyboard navigation", () => {
  test("Tab reaches the primary controls on the Story", async ({ page, errors }) => {
    await page.goto("#/");
    await waitForApp(page);
    await page.locator("body").click({ position: { x: 1, y: 1 } });
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    const targets = {
      "skip link": /skip to content/i,
      "nav: Story": /^story$/i,
      "nav: Worlds": /^worlds$/i,
      "nav: Timeline": /^timeline$/i,
      "nav: Mechanism": /^mechanism$/i,
      "nav: Evidence": /^evidence$/i,
      "hero: See it work": /see it work/i,
      "hero: Verify a release": /verify a release/i,
      "gate: DENIED toggle": /^DENIED/,
      "verify: Verify (this) bundle": /^verify (this )?bundle$/i,
    };
    const { missing, seen } = await tabUntil(page, targets, 600);
    expect(missing, `not reachable with Tab after ${seen.length} presses`).toEqual([]);
    expect(errors).toEqual([]);
  });

  test("focus is visible on the primary nav", async ({ page }) => {
    await page.goto("#/");
    await waitForApp(page);
    const nav = page.getByRole("navigation", { name: "Views" }).first();
    const btn = nav.getByRole("button", { name: "Worlds" });
    await btn.focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    await expect(btn).toBeFocused();
    const ring = await btn.evaluate((el) => {
      const s = getComputedStyle(el);
      return { outline: s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0, shadow: s.boxShadow !== "none" };
    });
    expect(ring.outline || ring.shadow, "focused nav button shows an outline or focus ring").toBe(true);
  });

  test("Enter on a nav button switches the view", async ({ page, errors }) => {
    await page.goto("#/");
    await waitForApp(page);
    const nav = page.getByRole("navigation", { name: "Views" }).first();
    for (const [name, hash] of [
      ["Worlds", "#/worlds"],
      ["Timeline", "#/timeline"],
      ["Mechanism", "#/mechanism"],
      ["Evidence", "#/evidence"],
      ["Story", "#/story"],
    ] as const) {
      await nav.getByRole("button", { name }).focus();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(new RegExp(`${hash.replace("/", "\\/")}`));
      await expect(nav.getByRole("button", { name })).toHaveAttribute("aria-current", "page");
      await waitForApp(page);
    }
    expect(errors).toEqual([]);
  });

  test("skip link moves focus to main without leaving the current view", async ({ page }) => {
    await page.goto("#/worlds");
    await waitForApp(page);
    await page.keyboard.press("Tab");
    expect(await focusedName(page)).toMatch(/skip to content/i);
    await page.keyboard.press("Enter");
    const nav = page.getByRole("navigation", { name: "Views" }).first();
    // The app routes on location.hash, so a plain href="#main" would re-route to the Story.
    await expect(nav.getByRole("button", { name: "Worlds" }), "still on Worlds after using the skip link").toHaveAttribute("aria-current", "page");
    await expect(page.locator("#main")).toBeInViewport();
  });

  test("Escape closes the mobile menu", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("#/");
    await waitForApp(page);
    const menu = page.getByRole("button", { name: /open menu/i });
    await menu.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#mobile-menu")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("#mobile-menu")).toHaveCount(0);
  });
});
