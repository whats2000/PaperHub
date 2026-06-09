// Vendor the MathJax 3 browser build into public/ so nginx (deploy) and Vite
// (dev) serve it SAME-ORIGIN — the Citation Canvas iframe then needs no external
// CDN, and dev ≡ deploy. We copy only what the combined `tex-chtml-full.js`
// build loads at runtime: the loader itself + its CHTML web fonts (it resolves
// fonts at `<loader-dir>/output/chtml/fonts/...`). Copying the whole `es5/` tree
// would bloat the image ~10x for output formats/font variants we never use.
//
// Runs on predev/prebuild/pretest (see package.json), so a fresh `npm install`
// followed by any of those has the vendored copy in place. The destination is
// gitignored — it's a build artefact reproduced from the pinned npm dependency.
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const srcRoot = resolve(root, "node_modules/mathjax/es5");
const destRoot = resolve(root, "public/vendor/mathjax");

if (!existsSync(srcRoot)) {
  console.error(
    `[vendor-mathjax] ${srcRoot} not found — run \`npm install\` first (mathjax dependency missing).`,
  );
  process.exit(1);
}

// The loader + the CHTML font directory it pulls glyphs from at render time.
const items = ["tex-chtml-full.js", "output/chtml/fonts/woff-v2"];

rmSync(destRoot, { recursive: true, force: true });
for (const item of items) {
  const src = resolve(srcRoot, item);
  if (!existsSync(src)) {
    console.error(
      `[vendor-mathjax] expected ${item} in the mathjax package but it's missing — check the pinned mathjax version.`,
    );
    process.exit(1);
  }
  const dest = resolve(destRoot, item);
  mkdirSync(dirname(dest), { recursive: true });
  cpSync(src, dest, { recursive: true });
}
console.log(`[vendor-mathjax] vendored ${items.length} item(s) → ${destRoot}`);
