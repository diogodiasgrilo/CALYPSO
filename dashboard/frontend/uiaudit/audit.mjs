/**
 * Render EVERY surface of the dashboard for EVERY strategy, screenshot it, and
 * collect console errors / failed requests.
 *
 * The console-error capture is the real bug-finding mechanism: it surfaces
 * runtime failures (undefined reads, NaN renders, thrown effects) that reading
 * code cannot, and that were invisible because nobody had ever rendered a
 * non-live variant.
 */
import { chromium } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';

const BASE = process.env.BASE || 'http://127.0.0.1:4178';
const OUT = new URL('./shots/', import.meta.url).pathname.replace(/%20/g, ' ');
mkdirSync(OUT, { recursive: true });

const ROUTES = [
  ['dashboard', '/'],
  ['history', '/history'],
  ['analytics', '/analytics'],
  ['group-ic', '/comparison/ic_0dte'],
  ['group-cal', '/comparison/calendar_multiday'],
  ['group-naked', '/comparison/undefined_risk_0dte'],
];
const STRATS = ['a', 'b', 'c', 'd', 'e', 'f', 'g'];

const browser = await chromium.launch();
const findings = [];

for (const sid of STRATS) {
  for (const [rname, path] of ROUTES) {
    // Deterministic rendering. Without this the count-up animation
    // (useAnimatedNumber, 300ms rAF) and chart transitions make two runs of the
    // SAME build differ, so a pixel diff measures timing noise instead of
    // change — which is exactly what a first attempt at one did.
    const ctx = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      reducedMotion: 'reduce',
    });
    const page = await ctx.newPage();
    const errors = [], failed = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
    // The mock now serves a real WS, so any remaining WS error IS a finding.
    page.on('pageerror', e => errors.push('PAGEERROR: ' + String(e).slice(0, 200)));
    page.on('requestfailed', r => failed.push(r.url().replace(BASE, '').slice(0, 120)));

    // Seed the picked strategy the way the app persists it, before first paint.
    await ctx.addInitScript(id => {
      // Exact key used by store/hydraStore.ts — an approximation here makes the
      // whole variant dimension silently untested (every page renders the default).
      try { localStorage.setItem('calypso-selected-strategy', id); } catch {}
    }, sid);

    try {
      await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 20000 });
      await page.addStyleTag({ content: `*,*::before,*::after{
        animation: none !important; transition: none !important;
        animation-duration: 0s !important; transition-duration: 0s !important; }` });
      await page.waitForTimeout(2500);   // let rAF counters settle on their final value
      const name = `${sid}-${rname}`;
      await page.screenshot({ path: `${OUT}${name}.png`, fullPage: true });
      const text = (await page.innerText('body').catch(() => '')) || '';
      findings.push({
        strategy: sid, route: rname, ok: true,
        errors, failed,
        textLen: text.length,
        blank: text.trim().length < 120,
        hasNaN: /\bNaN\b/.test(text),
        // NOT a bare \bundefined\b: a real strategy label contains
        // "undefined-risk", and matching inside a hyphenated compound makes
        // this fail forever on correct text.
        hasUndefined: /(^|[^-\w])undefined([^-\w]|$)/.test(text),
        hasInfinity: /Infinity/.test(text),
      });
    } catch (e) {
      findings.push({ strategy: sid, route: rname, ok: false, error: String(e).slice(0, 180), errors, failed });
    }
    await ctx.close();
  }
}
await browser.close();
writeFileSync(new URL('./findings.json', import.meta.url), JSON.stringify(findings, null, 2));

const bad = findings.filter(f => !f.ok || f.errors.length || f.blank || f.hasNaN || f.hasUndefined || f.hasInfinity);
console.log(`\n${findings.length} surfaces rendered, ${bad.length} with problems\n`);
for (const f of bad) {
  const flags = [ !f.ok && 'FAILED', f.blank && 'BLANK', f.hasNaN && 'NaN', f.hasUndefined && 'undefined',
                  f.hasInfinity && 'Infinity', f.errors.length && `${f.errors.length} console err` ].filter(Boolean);
  console.log(`  ${f.strategy}/${f.route}: ${flags.join(', ')}`);
  if (f.error) console.log(`      ${f.error}`);
  for (const e of (f.errors || []).slice(0, 2)) console.log(`      ${e}`);
}
