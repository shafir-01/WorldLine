import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { createReadStream, existsSync, statSync } from "node:fs";
import { join, normalize, resolve } from "node:path";

/**
 * Dev-only: serve `./data/*` from ui/fixtures (or from public/data if `npm run copy-data` populated it,
 * since Vite serves public/ first). In production `npm run copy-data` copies the data into public/data,
 * which Vite ships verbatim into dist/data.
 */
function serveFixturesAsData(): Plugin {
  const fixtures = resolve(__dirname, "fixtures");
  return {
    name: "worldline-serve-fixtures-as-data",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? "").split("?")[0];
        const idx = url.indexOf("/data/");
        if (idx === -1) return next();
        const rel = normalize(decodeURIComponent(url.slice(idx + "/data/".length)));
        if (rel.startsWith("..")) return next();
        const file = join(fixtures, rel);
        if (!existsSync(file) || !statSync(file).isFile()) return next();
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        createReadStream(file).pipe(res);
      });
    },
  };
}

export default defineConfig({
  // Relative base so the bundle works under any subpath (e.g. GitLab Pages /<group>/<project>/).
  base: "./",
  plugins: [react(), serveFixturesAsData()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
  },
  server: { port: 5173, open: false },
});
