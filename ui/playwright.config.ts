import { defineConfig, devices } from "@playwright/test";

/**
 * E2E suite for the WORLDLINE viewer. Runs against `vite preview` serving the built `dist/`,
 * so build first: `npm run build && npm run test:e2e`. See docs/UI_QA.md.
 * Paths in E2E_DIST are relative to ui/.
 */
const PORT = Number(process.env.E2E_PORT ?? 4317);
const CI = !!process.env.CI;
const OUT = "e2e/.output";
/** Directory to serve; defaults to dist/. Set E2E_DIST to test an isolated build (see docs/UI_QA.md). */
const DIST = process.env.E2E_DIST ?? "dist";

export default defineConfig({
  testDir: "e2e",
  outputDir: `${OUT}/test-results`,
  fullyParallel: true,
  forbidOnly: CI,
  retries: 0,
  workers: CI ? 2 : undefined,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: CI
    ? [["list"], ["html", { outputFolder: `${OUT}/report`, open: "never" }], ["junit", { outputFile: `${OUT}/junit.xml` }]]
    : [["list"], ["html", { outputFolder: `${OUT}/report`, open: "never" }]],
  use: {
    baseURL: `http://localhost:${PORT}/`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    colorScheme: "light",
    reducedMotion: "reduce",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 } } }],
  webServer: {
    command: `npm run preview -- --port ${PORT} --strictPort --outDir "${DIST}"`,
    url: `http://localhost:${PORT}/`,
    // Never reuse: another preview on the port could be serving a different build.
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
