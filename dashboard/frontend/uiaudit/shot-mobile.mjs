import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';
mkdirSync('shots_mobile', { recursive: true });
const b = await chromium.launch();
for (const [rn,path] of [['dashboard','/'],['history','/history'],['group-ic','/comparison/ic_0dte']]) {
  const ctx = await b.newContext({ viewport:{width:390,height:844}, reducedMotion:'reduce', deviceScaleFactor:2 });
  await ctx.addInitScript(()=>{try{localStorage.setItem('calypso-selected-strategy','b')}catch{}});
  const p = await ctx.newPage();
  await p.goto('http://127.0.0.1:4178'+path,{waitUntil:'networkidle'});
  await p.addStyleTag({content:'*,*::before,*::after{animation:none!important;transition:none!important}'});
  await p.waitForTimeout(1800);
  await p.screenshot({ path:`shots_mobile/${rn}.png` });   // viewport only, not fullPage
  await ctx.close();
}
await b.close();
console.log('  mobile screenshots captured');
