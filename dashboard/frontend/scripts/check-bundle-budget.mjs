#!/usr/bin/env node
/**
 * FIRST-PARSE JAVASCRIPT BUDGET.
 *
 * Run after `npm run build`. It reads dist/index.html and sums only the JS the
 * browser fetches and parses EAGERLY — the entry `<script type="module">` plus
 * every `<link rel="modulepreload">` Vite emits for the entry's static import
 * graph. Lazily imported chunks are deliberately not referenced there, so the
 * number this prints is the real cost of the first paint.
 *
 * WHY A GATE AND NOT A COMMENT. On 2026-11-16 this dashboard shipped ONE
 * 1,057,495-byte chunk because every page and both chart libraries were static
 * imports. The split that fixed it is invisible in review: adding
 * `import { SPXChart } from ...` back to a landing-path file silently undoes
 * ~500 kB of it, the app still works, every test still passes, and the only
 * symptom is that a phone takes seconds longer to show anything. A number in a
 * CI step is the only thing that notices.
 *
 * Measured at the time of writing:
 *   before the split   1,057,495 B eager   (313.0 kB gzip)
 *   after the split      425,996 B eager   (132.8 kB gzip)
 *
 * The budget is set a little above the current figure — enough that ordinary
 * feature work does not trip it, tight enough that a chart library or a whole
 * page landing back on the critical path does. When it fires, the fix is
 * almost always `lazy(() => import(...))`, not a bigger budget. Raising it is
 * a decision to make the dashboard slower for everyone, so say why in the
 * commit message.
 */
import { readFileSync, statSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dist = join(here, "..", "dist");

/** Bytes of eager JS allowed before the build fails. */
const BUDGET_BYTES = 480 * 1024;

/** Chunks that must NEVER be on the eager path. These are the heavy
 *  visualisation vendors; the dashboard is readable without them and they are
 *  half the pre-split bundle between them. */
const MUST_BE_LAZY = ["vendor-recharts", "vendor-lightweight-charts"];

let html;
try {
  html = readFileSync(join(dist, "index.html"), "utf8");
} catch {
  console.error("✗ dist/index.html not found — run `npm run build` first.");
  process.exit(2);
}

// ── STALENESS GUARD ───────────────────────────────────────────────────────
// This exists because it actually happened, while writing the mutation
// control for this very script: the mutant did not compile, `npm run build`
// failed, its output had been sent to /dev/null, and this checker then read
// the PREVIOUS dist and printed a confident green — identical byte counts,
// identical content hashes. A size gate that silently measures last build's
// output is worse than no gate, because it reports success about code that
// was never built.
const newestSource = (dir) => {
  let newest = 0;
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, e.name);
    if (e.isDirectory()) newest = Math.max(newest, newestSource(full));
    else if (/\.(ts|tsx|css|html)$/.test(e.name)) newest = Math.max(newest, statSync(full).mtimeMs);
  }
  return newest;
};
const builtAt = statSync(join(dist, "index.html")).mtimeMs;
const srcAt = newestSource(join(here, "..", "src"));
if (srcAt > builtAt) {
  console.error("✗ dist/ is OLDER than src/ — the last build did not run or did not succeed.");
  console.error(`  newest source: ${new Date(srcAt).toISOString()}`);
  console.error(`  dist built:    ${new Date(builtAt).toISOString()}`);
  console.error("  Re-run `npm run build` and read its output before trusting this number.");
  process.exit(2);
}

const eager = [
  ...html.matchAll(/<script[^>]+type="module"[^>]+src="([^"]+\.js)"/g),
  ...html.matchAll(/<link[^>]+rel="modulepreload"[^>]+href="([^"]+\.js)"/g),
].map((m) => m[1]);

if (eager.length === 0) {
  console.error("✗ parsed no eager scripts out of dist/index.html — the parser is wrong, not the bundle.");
  process.exit(2);
}

let total = 0;
const rows = [];
for (const href of eager) {
  const size = statSync(join(dist, href.replace(/^\//, ""))).size;
  total += size;
  rows.push([href, size]);
}

rows.sort((a, b) => b[1] - a[1]);
console.log("eager first-parse JavaScript:");
for (const [href, size] of rows) {
  console.log(`  ${String(size).padStart(9)} B  ${href}`);
}
console.log(`  ${"-".repeat(9)}`);
console.log(`  ${String(total).padStart(9)} B  TOTAL   (budget ${BUDGET_BYTES} B)`);

let failed = false;

for (const name of MUST_BE_LAZY) {
  const hit = eager.find((h) => h.includes(name));
  if (hit) {
    console.error(`\n✗ ${name} is on the EAGER path (${hit}).`);
    console.error(`  It must stay behind a lazy import — see IronCondorDashboard.tsx.`);
    failed = true;
  }
}

if (total > BUDGET_BYTES) {
  const over = total - BUDGET_BYTES;
  console.error(`\n✗ over budget by ${over} B (${(over / 1024).toFixed(1)} kB).`);
  console.error(`  Something large joined the entry's STATIC import graph.`);
  console.error(`  Find it with: npx vite build --mode production && npx vite-bundle-visualizer`);
  console.error(`  The fix is usually lazy(() => import(...)), not a bigger budget.`);
  failed = true;
}

if (failed) process.exit(1);
console.log("\n✓ within budget, and both chart vendors are lazy.");
