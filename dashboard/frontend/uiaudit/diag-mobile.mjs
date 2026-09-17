import { chromium } from '@playwright/test';
const b = await chromium.launch();
for (const [rn, path] of [['dashboard','/'],['history','/history'],['analytics','/analytics'],['group-ic','/comparison/ic_0dte']]) {
  const ctx = await b.newContext({ viewport:{width:390,height:844}, reducedMotion:'reduce' });
  await ctx.addInitScript(()=>{try{localStorage.setItem('calypso-selected-strategy','b')}catch{}});
  const p = await ctx.newPage();
  await p.goto('http://127.0.0.1:390'.replace('390','4178')+path,{waitUntil:'networkidle'});
  await p.waitForTimeout(900);
  const offenders = await p.evaluate(() => {
    const VW = document.documentElement.clientWidth;
    const out = [];
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (r.width <= VW + 2 && r.right <= VW + 2) continue;
      // Skip anything inside a SCROLLABLE ancestor — content wider than a
      // scroll container is the intended behaviour, not page overflow.
      let anc = el.parentElement, inScroller = false;
      while (anc && anc !== document.body) {
        const ov = getComputedStyle(anc).overflowX;
        if ((ov === 'auto' || ov === 'scroll') && anc.scrollWidth > anc.clientWidth) { inScroller = true; break; }
        anc = anc.parentElement;
      }
      if (inScroller) continue;
      // Report the element only if its PARENT fits — i.e. it is the one
      // introducing the overflow, not merely inheriting it.
      const pr = el.parentElement?.getBoundingClientRect();
      if (pr && (pr.width > VW + 2 || pr.right > VW + 2)) continue;
      out.push({
        tag: el.tagName.toLowerCase(),
        w: Math.round(r.width), right: Math.round(r.right),
        cls: (el.className || '').toString().slice(0, 82),
        txt: (el.textContent || '').trim().slice(0, 34).replace(/\s+/g,' '),
      });
    }
    return { VW, out: out.slice(0, 8) };
  });
  console.log(`\n=== ${rn} (viewport ${offenders.VW}px) — root causes ===`);
  for (const o of offenders.out) console.log(`  <${o.tag}> w=${o.w} right=${o.right}\n      cls: ${o.cls}\n      txt: ${o.txt}`);
  await ctx.close();
}
await b.close();
