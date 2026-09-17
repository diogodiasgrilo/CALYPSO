import { chromium } from '@playwright/test';
const b = await chromium.launch();
for (const w of [390, 820]) {
  const ctx = await b.newContext({ viewport:{width:w,height:1000}, reducedMotion:'reduce' });
  await ctx.addInitScript(()=>{try{localStorage.setItem('calypso-selected-strategy','b')}catch{}});
  const p = await ctx.newPage();
  await p.goto('http://127.0.0.1:4178/comparison/ic_0dte',{waitUntil:'networkidle'});
  await p.waitForTimeout(900);
  const c = await p.evaluate(()=>{
    const out=[];
    for (const el of document.querySelectorAll('body *')) {
      if (el.children.length || !el.textContent.trim()) continue;
      if (el.scrollWidth > el.clientWidth+2 && getComputedStyle(el).overflow!=='visible')
        out.push({ sw: el.scrollWidth, cw: el.clientWidth, txt: el.textContent.trim().slice(0,40), cls:(el.className||'').toString().slice(0,60) });
    }
    return out;
  });
  console.log(`  ${w}px — ${c.length} clipped`);
  for (const x of c) console.log(`      "${x.txt}"  ${x.cw}px box / ${x.sw}px content   ${x.cls}`);
  await ctx.close();
}
await b.close();
