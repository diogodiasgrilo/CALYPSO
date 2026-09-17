import { chromium } from '@playwright/test';
const b = await chromium.launch();
const ctx = await b.newContext({ viewport:{width:390,height:844}, reducedMotion:'reduce' });
await ctx.addInitScript(()=>{try{localStorage.setItem('calypso-selected-strategy','b')}catch{}});
const p = await ctx.newPage();
await p.goto('http://127.0.0.1:4178/',{waitUntil:'networkidle'});
await p.waitForTimeout(900);
const r = await p.evaluate(() => {
  const h = document.querySelector('header');
  const out = { headerW: Math.round(h.getBoundingClientRect().width), groups: [], items: [] };
  for (const g of h.children) {
    const gr = g.getBoundingClientRect();
    out.groups.push({ w: Math.round(gr.width), right: Math.round(gr.right), txt: (g.textContent||'').trim().slice(0,30) });
    for (const it of g.children) {
      const ir = it.getBoundingClientRect();
      if (ir.width > 0) out.items.push({ tag: it.tagName.toLowerCase(), w: Math.round(ir.width), txt: (it.textContent||'').trim().slice(0,26) });
    }
  }
  return out;
});
console.log(`  header width: ${r.headerW}  (viewport 390)`);
console.log('  --- groups ---');
for (const g of r.groups) console.log(`    w=${String(g.w).padStart(4)} right=${String(g.right).padStart(4)}  ${g.txt}`);
console.log('  --- items ---');
for (const i of r.items) console.log(`    <${i.tag}> w=${String(i.w).padStart(4)}  ${i.txt}`);
await b.close();
