// Builds the viewer into e2e/.output/dist instead of dist/, so a test run is not disturbed by
// someone else rebuilding dist/ at the same time. Usage (from ui/):
//   node e2e/build-snapshot.mjs && E2E_DIST=e2e/.output/dist npm run test:e2e
import { execSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ui = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const run = (cmd) => execSync(cmd, { cwd: ui, stdio: "inherit" });
run("npm run copy-data");
run("npx tsc -p tsconfig.json --noEmit");
run("npx vite build --outDir e2e/.output/dist --emptyOutDir");
