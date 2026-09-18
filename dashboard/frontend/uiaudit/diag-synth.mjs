import { chromium } from '@playwright/test';
const b = await chromium.launch();
for (const [sid,path,label] of [['b','/comparison/undefined_risk_0dte','group-naked'],['c','/','c-dashboard']]) {
  const ctx = await b.newContext({ viewport:{width:1440,height:1000}, reducedMotion:'reduce' });
  await ctx.addInitScript(id=>{try{localStorage.setItem('calypso-selected-strategy',id)}catch{}}, sid);
  const p = await ctx.newPage();
  await p.goto('http://127.0.0.1:4178'+path,{waitUntil:'networkidle'});
  await p.waitForTimeout(1200);
  const hits = await p.evaluate(()=>{
    const out=[];
    for (const el of document.querySelectorAll('body *')) {
      if (el.children.length) continue;
      const t=(el.textContent||'').trim();
      if (/\bundefined\b|\bNaN\b/.test(t))
        out.push({txt:t.slice(0,50), cls:(el.className||'').toString().slice(0,44),
                  parent:(el.parentElement?.textContent||'').trim().slice(0,60)});
    }
    return out;
  });
  console.log(`\n  ${label}: ${hits.length} hit(s)`);
  for (const h of hits.slice(0,5)) console.log(`     "${h.txt}"   ctx: ${h.parent}`);
  await ctx.close();
}
await b.close();
