// Copies the data the viewer reads at runtime into public/data (shipped verbatim to dist/data).
// Prefers real pipeline output in ../experiments/out; falls back to the synthetic fixtures.
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ui = resolve(here, "..");
const real = resolve(ui, "..", "experiments", "out");
const fixtures = join(ui, "fixtures");
const dest = join(ui, "public", "data");

const hasJson = (dir) => existsSync(dir) && statSync(dir).isDirectory() && readdirSync(dir).some((f) => f.endsWith(".json"));
const src = hasJson(real) ? real : fixtures;

rmSync(dest, { recursive: true, force: true });
mkdirSync(dest, { recursive: true });
cpSync(src, dest, { recursive: true });
console.log(`[copy-data] ${src === real ? "experiments/out" : "fixtures (synthetic)"} -> ${dest}`);
