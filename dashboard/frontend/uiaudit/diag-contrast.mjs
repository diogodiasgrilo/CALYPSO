/* WCAG 1.4.3 contrast, measured on the RENDERED page.
 *
 * Not computed from the palette: a token's ratio depends on which surface it
 * lands on (bg #1a2229, card #222e35, bg-elevated #2d3b43 all differ), on any
 * alpha in the colour, and on ancestors that are transparent. So this walks
 * real text nodes, composites the effective background up the ancestor chain,
 * and reports the actual ratio.
 *
 *   cd dashboard/frontend && npm run build
 *   cd uiaudit && node mock-server.mjs &
 *   node diag-contrast.mjs            # → contrast.json
 */
import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { CONTRAST_PROBE as PROBE } from './lib-contrast.mjs';

const BASE = 'http://127.0.0.1:4178';

const SURFACES = [
  { name: 'login-gate', path: '/', auth: false },
  { name: 'dashboard', path: '/', auth: true },
  { name: 'history', path: '/history', auth: true },
  { name: 'analytics', path: '/analytics', auth: true },
  { name: 'comparison-ic', path: '/comparison/ic_0dte', auth: true },
  { name: 'comparison-cal', path: '/comparison/calendar_multiday', auth: true },
];

const b = await chromium.launch();
const all = {};
let total = 0;

for (const s of SURFACES) {
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  await ctx.addInitScript(() => { try { localStorage.setItem('calypso-selected-strategy', 'b'); } catch { /* private */ } });
  const p = await ctx.newPage();
  if (!s.auth) await p.route('**/api/auth/me', (r) => r.fulfill({ status: 401, body: '{}' }));
  await p.goto(BASE + s.path, { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1600);

  const bad = await p.evaluate(PROBE);
  all[s.name] = bad;
  total += bad.reduce((n, x) => n + x.count, 0);
  console.log(`\n=== ${s.name} — ${bad.length} distinct failing combination(s), ${bad.reduce((n, x) => n + x.count, 0)} node(s) ===`);
  for (const x of bad.slice(0, 12)) {
    console.log(`  ${String(x.ratio).padStart(5)}:1 (need ${x.need})  ${x.px}px/${x.weight}  x${x.count}`);
    console.log(`      ${x.fg} on ${x.bg}   "${x.sample}"`);
    console.log(`      ${x.cls}`);
  }
  await ctx.close();
}

await b.close();
writeFileSync('contrast.json', JSON.stringify(all, null, 2));
console.log(`\n  ${total} failing text node(s) across ${SURFACES.length} surfaces → contrast.json`);
process.exit(total ? 1 : 0);
