/**
 * Copies the orb's static assets next to its compiled renderer.
 *
 * `tsc` only emits JavaScript, so `index.html` and `orb.css` need an explicit
 * copy into `dist/orb` where the static server mounts them.
 *
 * `thinking-orbs/engine` is the same framework-free renderer the in-app orb
 * uses (the React component is only a thin canvas host). Its package exports a
 * browser ESM entry that imports a sibling chunk, so the whole `dist` folder is
 * copied and `index.html` maps the bare specifier onto `vendor/`.
 */
import { access, copyFile, cp, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const target = resolve(here, "..", "dist", "orb");
const engineSource = resolve(here, "..", "node_modules", "thinking-orbs", "dist");

await mkdir(target, { recursive: true });
for (const file of ["index.html", "orb.css"]) {
  await copyFile(resolve(here, file), resolve(target, file));
}

// The engine and its shared chunk must ship together: `engine.es.js` imports
// `./index-<hash>.js` relatively, and the import map only resolves the bare
// specifier. Copying the package's dist keeps both in lockstep on upgrades.
await cp(engineSource, resolve(target, "vendor", "thinking-orbs"), { recursive: true });
await access(resolve(target, "vendor", "thinking-orbs", "engine.es.js"));

console.log(`[orb] static assets copied to ${target}`);
