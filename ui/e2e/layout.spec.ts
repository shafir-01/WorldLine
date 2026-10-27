import { ROUTES, expect, test, waitForApp } from "./helpers";

test.describe("390 px (phone) layout", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  for (const route of ROUTES) {
    test(`no horizontal overflow on ${route}`, async ({ page, errors }) => {
      await page.goto(route);
      await waitForApp(page);
      await page.waitForTimeout(500); // ResizeObserver-driven SVG widths settle
      const m = await page.evaluate(() => {
        const vw = document.documentElement.clientWidth;
        const offenders: string[] = [];
        const clipped = (el: Element) => {
          for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
            const ox = getComputedStyle(p).overflowX;
            if (ox === "auto" || ox === "scroll" || ox === "hidden" || ox === "clip") return true;
          }
          return false;
        };
        for (const el of document.querySelectorAll("body *")) {
          const r = el.getBoundingClientRect();
          if (r.width === 0 || r.right <= vw + 1 || clipped(el)) continue;
          const sel = el.tagName.toLowerCase() + (el.id ? `#${el.id}` : "") + (typeof el.className === "string" && el.className ? "." + el.className.trim().split(/\s+/).join(".") : "");
          offenders.push(`${sel} (right=${Math.round(r.right)})`);
        }
        return { vw, docW: document.documentElement.scrollWidth, bodyW: document.body.scrollWidth, offenders: offenders.slice(0, 10) };
      });
      expect(m.docW, `document is ${m.docW}px wide at ${m.vw}px; widest offenders:\n${m.offenders.join("\n")}`).toBeLessThanOrEqual(m.vw);
      expect(m.bodyW, `body scrolls horizontally; offenders:\n${m.offenders.join("\n")}`).toBeLessThanOrEqual(m.vw);
      expect(errors).toEqual([]);
    });
  }
});

/** Relative luminance (WCAG) of a computed `rgb(...)`/`rgba(...)` colour. */
function luminance(css: string): number {
  const [r, g, b] = (css.match(/[\d.]+/g) ?? ["0", "0", "0"]).slice(0, 3).map((v) => {
    const c = Number(v) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

test.describe("dark mode (prefers-color-scheme: dark)", () => {
  for (const route of ROUTES) {
    test(`renders dark on ${route}`, async ({ page, errors }, info) => {
      await page.emulateMedia({ colorScheme: "light" });
      await page.goto(route);
      await waitForApp(page);
      const colours = () =>
        page.evaluate(() => {
          // The page background may sit on <html>, <body> or the app shell; take the first opaque one.
          const bg = [document.querySelector(".app"), document.body, document.documentElement]
            .map((el) => (el ? getComputedStyle(el).backgroundColor : ""))
            .find((c) => c && !/rgba\(0, 0, 0, 0\)|transparent/.test(c)) ?? "rgb(255, 255, 255)";
          const h = document.querySelector("#main h1, #main h2, #main h3");
          return { bg, fg: getComputedStyle(h ?? document.body).color };
        });
      const light = await colours();

      await page.emulateMedia({ colorScheme: "dark" });
      await page.waitForTimeout(200);
      const dark = await colours();
      info.annotations.push({ type: "colours", description: `light bg ${light.bg} fg ${light.fg}; dark bg ${dark.bg} fg ${dark.fg}` });

      expect(dark.bg, "background changes with the colour scheme").not.toBe(light.bg);
      expect(luminance(dark.bg), `dark background ${dark.bg} is dark`).toBeLessThan(0.1);
      expect(luminance(dark.fg), `heading text ${dark.fg} is light on dark`).toBeGreaterThan(0.4);
      expect(luminance(light.bg), `light background ${light.bg} is light`).toBeGreaterThan(0.6);
      if (route === "#/" || route === "#/evidence") await info.attach(`dark-${route.replace(/\W+/g, "") || "story"}.png`, { body: await page.screenshot(), contentType: "image/png" });
      expect(errors).toEqual([]);
    });
  }
});
