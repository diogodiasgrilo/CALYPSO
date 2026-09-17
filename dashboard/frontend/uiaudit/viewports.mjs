import { chromium } from '@playwright/test';
const VPS = [['iPhone 14',390,844],['iPad',820,1180],['laptop',1440,1000],['wide',1920,1080]];
const ROUTES = [['dashboard','/'],['history','/history'],['analytics','/analytics'],['group-ic','/comparison/ic_0dte']];
const b = await chromium.launch();
console.log('  viewport      route         ovfX  bodyW/scrollW   clipped  tiny-tap');
for (const [name,w,h] of VPS) {
  for (const [rn,path] of ROUTES) {
    const ctx = await b.newContext({ viewport:{width:w,height:h}, reducedMotion:'reduce' });
    await ctx.addInitScript(()=>{try{localStorage.setItem('calypso-selected-strategy','b')}catch{}});
    const p = await ctx.newPage();
    await p.goto('http://127.0.0.1:4178'+path,{waitUntil:'networkidle',timeout:20000});
    await p.waitForTimeout(900);
    const r = await p.evaluate(()=>{
      let clipped=0, tinyTap=0;
      for (const el of document.querySelectorAll('body *')) {
        if (!el.children.length && el.textContent.trim() && el.scrollWidth>el.clientWidth+2
            && getComputedStyle(el).overflow!=='visible') clipped++;
        if ((el.tagName==='BUTTON'||el.tagName==='A') && el.clientHeight>0 && el.clientHeight<32) tinyTap++;
      }
      return { sw: document.body.scrollWidth, cw: document.body.clientWidth, clipped, tinyTap };
    });
    const ovf = r.sw > r.cw+2;
    console.log(`  ${name.padEnd(12)} ${rn.padEnd(12)} ${(ovf?'YES':'-').padEnd(5)} ${String(r.cw)}/${String(r.sw).padEnd(10)} ${String(r.clipped).padEnd(8)} ${r.tinyTap}${ovf?'   <<< OVERFLOW':''}`);
    await ctx.close();
  }
}
await b.close();
