import { chromium } from '@playwright/test';
const b = await chromium.launch();
const ctx = await b.newContext({ viewport:{width:390,height:844}, reducedMotion:'reduce' });
await ctx.addInitScript(()=>{try{localStorage.setItem('calypso-selected-strategy','b')}catch{}});
const p = await ctx.newPage();
await p.goto('http://127.0.0.1:4178/',{waitUntil:'networkidle'});
await p.waitForTimeout(800);
const info = await p.evaluate(()=>[...document.querySelectorAll('a')]
  .map(e=>{const r=e.getBoundingClientRect();return {w:Math.round(r.width),h:Math.round(r.height),
    href:e.getAttribute('href'), aria:e.getAttribute('aria-label'), title:e.getAttribute('title'),
    html:e.outerHTML.slice(0,150)};})
  .filter(x=>x.h>0 && !(x.aria||'').trim() && !(e=>false)() && x.w<120));
for (const i of info) console.log(`  <a ${i.w}x${i.h}> href=${i.href} aria=${i.aria} title=${i.title}\n     ${i.html}`);
await b.close();
