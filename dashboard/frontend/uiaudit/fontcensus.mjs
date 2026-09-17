/**
 * Census every rendered font-size across every surface.
 *
 * The right instrument for "did the type-scale refactor change any size".
 * Pixel-diffing could not answer it: chart canvases render non-deterministically,
 * so 6 of 42 screenshots differ between two runs of the SAME build.
 */
import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';

const BASE = 'http://127.0.0.1:4178';
const ROUTES = [['dashboard','/'],['history','/history'],['analytics','/analytics'],
  ['group-ic','/comparison/ic_0dte'],['group-cal','/comparison/calendar_multiday'],
  ['group-naked','/comparison/undefined_risk_0dte']];
const STRATS = ['a','b','c','d','e','f','g'];

const browser = await chromium.launch();
const census = {};
for (const sid of STRATS) {
  for (const [rname, path] of ROUTES) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await ctx.addInitScript(id => { try { localStorage.setItem('calypso-selected-strategy', id); } catch {} }, sid);
    const page = await ctx.newPage();
    await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 20000 });
    await page.waitForTimeout(1200);
    // Count TEXT-BEARING leaf elements by computed font-size.
    const sizes = await page.evaluate(() => {
      const out = {};
      for (const el of document.querySelectorAll('body *')) {
        if (el.children.length) continue;
        if (!el.textContent || !el.textContent.trim()) continue;
        const fs = getComputedStyle(el).fontSize;
        out[fs] = (out[fs] || 0) + 1;
      }
      return out;
    });
    for (const [px, n] of Object.entries(sizes)) census[px] = (census[px] || 0) + n;
    await ctx.close();
  }
}
await browser.close();
const sorted = Object.fromEntries(
  Object.entries(census).sort((a, b) => parseFloat(a[0]) - parseFloat(b[0])));
writeFileSync(process.argv[2] || 'fontcensus.json', JSON.stringify(sorted, null, 2));
console.log('  font-size : elements');
for (const [px, n] of Object.entries(sorted)) console.log(`  ${px.padStart(9)} : ${n}`);
