import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { OUT, ROUTES, expect, test, waitForApp } from "./helpers";

/**
 * axe-core scan per route, in light and dark. Every violation is written to e2e/.output/axe/<route>-<scheme>.json
 * (and attached to the HTML report); the test fails only on `serious` or `critical` impact.
 */
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const BLOCKING = new Set(["serious", "critical"]);

test.describe("accessibility (axe)", () => {
  for (const scheme of ["light", "dark"] as const) {
    for (const route of ROUTES) {
      test(`${route} (${scheme}) has no serious/critical violations`, async ({ page, errors }, info) => {
        void errors; // fixture installs the network isolation
        await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
        await page.goto(route);
        await waitForApp(page);
        await page.waitForTimeout(800); // entrance animations (reduced) and deep-link scroll settle
        const res = await new AxeBuilder({ page }).withTags(TAGS).analyze();
        const findings = res.violations.map((v) => ({
          id: v.id,
          impact: v.impact,
          help: v.help,
          helpUrl: v.helpUrl,
          nodes: v.nodes.map((n) => ({ target: n.target.join(" "), summary: n.failureSummary?.split("\n").slice(0, 3).join(" ") })),
        }));
        const slug = `${route.replace(/[#/]+/g, "-").replace(/^-|-$/g, "") || "story"}-${scheme}`;
        mkdirSync(join(OUT, "axe"), { recursive: true });
        writeFileSync(join(OUT, "axe", `${slug}.json`), JSON.stringify({ route, scheme, findings }, null, 2));
        await info.attach(`axe-${slug}.json`, { body: JSON.stringify(findings, null, 2), contentType: "application/json" });
        for (const f of findings) info.annotations.push({ type: `axe ${f.impact}`, description: `${f.id}: ${f.nodes.length} node(s), e.g. ${f.nodes[0]?.target}` });

        const blocking = findings.filter((f) => BLOCKING.has(f.impact ?? ""));
        const report = blocking.map((f) => `[${f.impact}] ${f.id} (${f.help})\n${f.nodes.slice(0, 6).map((n) => `    ${n.target}`).join("\n")}${f.nodes.length > 6 ? `\n    ... +${f.nodes.length - 6} more` : ""}`).join("\n");
        expect(blocking.map((f) => `${f.impact} ${f.id} (${f.nodes.length} nodes)`), `serious/critical axe violations on ${route} (${scheme}):\n${report}`).toEqual([]);
      });
    }
  }
});
