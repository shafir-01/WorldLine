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
// The viewer cannot render without the manifest and the sealed bundle, so partial output falls back too.
const complete = (dir) => hasJson(dir) && ["index.json", "bundle.json"].every((f) => existsSync(join(dir, f)));
const src = complete(real) ? real : fixtures;

rmSync(dest, { recursive: true, force: true });
mkdirSync(dest, { recursive: true });
cpSync(src, dest, { recursive: true });
console.log(`[copy-data] ${src === real ? "experiments/out" : "fixtures (synthetic)"} -> ${dest}`);
