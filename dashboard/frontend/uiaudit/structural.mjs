/**
 * Structural/visual defect sweep across every surface.
 * Catches the things that read as "unpolished" and that code-reading misses:
 * empty-data density, horizontal overflow, clipped text, off-token colours,
 * and duplicated/blank cards.
 */
import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';

const BASE = 'http://127.0.0.1:4178';
const ROUTES = [['dashboard','/'],['history','/history'],['analytics','/analytics'],
  ['group-ic','/comparison/ic_0dte'],['group-cal','/comparison/calendar_multiday'],
  ['group-naked','/comparison/undefined_risk_0dte']];
const STRATS = ['a','b','c','d','e','f','g'];

const browser = await chromium.launch();
const rows = [];
for (const sid of STRATS) {
  for (const [rname, path] of ROUTES) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await ctx.addInitScript(id => { try { localStorage.setItem('calypso-selected-strategy', id); } catch {} }, sid);
    const page = await ctx.newPage();
    await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 20000 });
    await page.waitForTimeout(1000);
    const r = await page.evaluate(() => {
      const txt = document.body.innerText || '';
      const dash = (txt.match(/—/g) || []).length;
      const words = txt.split(/\s+/).filter(Boolean).length;
      const out = { dash, words, emptyRatio: words ? +(dash / words).toFixed(3) : 0,
                    overflowX: document.body.scrollWidth > document.body.clientWidth,
                    clipped: [], offToken: new Set(), tinyText: 0, zeroBox: 0 };
      for (const el of document.querySelectorAll('body *')) {
        const cs = getComputedStyle(el);
        // text clipped by its own box
        if (el.children.length === 0 && el.textContent.trim() &&
            el.scrollWidth > el.clientWidth + 2 && cs.overflow !== 'visible') out.clipped.push(el.textContent.trim().slice(0,30));
        // element with content but no box
        if (el.textContent.trim() && el.clientWidth === 0 && el.clientHeight === 0 && cs.position !== 'absolute') out.zeroBox++;
        const fs = parseFloat(cs.fontSize);
        if (el.children.length === 0 && el.textContent.trim() && fs && fs < 10) out.tinyText++;
        // colours written as literal hex/rgb rather than a token variable
        for (const prop of ['color','backgroundColor','borderColor']) {
          const v = cs[prop];
          if (/^rgb/.test(v) && v !== 'rgba(0, 0, 0, 0)') out.offToken.add(prop + ':' + v);
        }
      }
      out.offToken = out.offToken.size;
      out.clipped = out.clipped.slice(0, 4);
      return out;
    });
    rows.push({ sid, rname, ...r });
    await ctx.close();
  }
}
await browser.close();
writeFileSync('structural.json', JSON.stringify(rows, null, 2));
console.log('surface        ' + ['dashes','words','empty%','ovfX','clip','zeroBox','tiny'].map(h=>h.padStart(7)).join(''));
for (const r of rows) {
  const flag = (r.emptyRatio > 0.15 || r.overflowX || r.clipped.length || r.zeroBox > 3) ? '  <<<' : '';
  console.log(`${(r.sid+'/'+r.rname).padEnd(15)}` +
    [r.dash, r.words, (r.emptyRatio*100).toFixed(0)+'%', r.overflowX?'YES':'-', r.clipped.length, r.zeroBox, r.tinyText]
      .map(v=>String(v).padStart(7)).join('') + flag);
}
