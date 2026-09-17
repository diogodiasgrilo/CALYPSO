import { chromium } from '@playwright/test';
const b = await chromium.launch();
const ctx = await b.newContext({ viewport:{width:390,height:844}, reducedMotion:'reduce' });
await ctx.addInitScript(()=>{try{localStorage.setItem('calypso-selected-strategy','b')}catch{}});
const p = await ctx.newPage();
await p.goto('http://127.0.0.1:4178/comparison/ic_0dte',{waitUntil:'networkidle'});
await p.waitForTimeout(800);
const trunc = await p.evaluate(()=>[...document.querySelectorAll('.truncate')]
  .filter(e=>e.scrollWidth>e.clientWidth+2)
  .map(e=>({txt:e.textContent.trim().slice(0,32), title:e.getAttribute('title')||null})));
console.log('  truncated labels — do they carry a title?');
for (const t of trunc) console.log(`    "${t.txt}"  title=${t.title===null?'MISSING':'"'+t.title.slice(0,28)+'"'}`);
await p.goto('http://127.0.0.1:4178/',{waitUntil:'networkidle'});
await p.waitForTimeout(800);
const taps = await p.evaluate(()=>[...document.querySelectorAll('button,a,select,[role=button]')]
  .map(e=>{const r=e.getBoundingClientRect();return {t:e.tagName.toLowerCase(),h:Math.round(r.height),w:Math.round(r.width),
    label:(e.getAttribute('aria-label')||e.textContent||'').trim().slice(0,24)};})
  .filter(x=>x.h>0 && x.h<44));
console.log(`\n  tap targets under 44px (Apple HIG minimum): ${taps.length}`);
for (const t of taps.slice(0,10)) console.log(`    <${t.t}> ${t.w}x${t.h}  "${t.label}"`);
await b.close();
