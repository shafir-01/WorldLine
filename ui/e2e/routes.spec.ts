import { ROUTES, expect, test, waitForApp } from "./helpers";

test.describe("every route renders without console errors", () => {
  for (const route of ROUTES) {
    test(`renders ${route}`, async ({ page, errors }) => {
      await page.goto(route);
      await waitForApp(page);
      // Something real is on screen: at least one heading inside <main>.
      await expect(page.locator("#main").getByRole("heading").first()).toBeVisible();
      // Give deferred effects (deep-link scroll at 250/1200 ms, lazy trace fetches) time to run and fail loudly.
      await page.waitForTimeout(1500);
      expect(errors, `errors on ${route}:\n${errors.join("\n")}`).toEqual([]);
    });
  }

  test("#/story/2 deep link scrolls to beat 2", async ({ page, errors }) => {
    await page.goto("#/story/2");
    await waitForApp(page);
    const beat = page.locator("#beat-2");
    await expect(beat).toBeAttached();
    await expect
      .poll(async () => beat.evaluate((el) => Math.round(el.getBoundingClientRect().top)), { timeout: 5_000, message: "beat 2 should be scrolled near the top of the viewport" })
      .toBeLessThan(300);
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(100);
    expect(errors).toEqual([]);
  });
});
