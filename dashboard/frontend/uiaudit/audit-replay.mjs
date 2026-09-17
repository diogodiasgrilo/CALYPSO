import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';
mkdirSync('shots_replay', { recursive: true });
const b = await chromium.launch();
for (const [w,h,tag] of [[1440,1000,'desktop'],[390,844,'mobile']]) {
  const ctx = await b.newContext({ viewport:{width:w,height:h}, reducedMotion:'reduce' });
  await ctx.addInitScript(()=>{try{localStorage.setItem('calypso-selected-strategy','b')}catch{}});
  const p = await ctx.newPage();
  const errs=[]; p.on('pageerror',e=>errs.push(String(e).slice(0,110)));
  p.on('console',m=>{if(m.type()==='error'&&!/WebSocket/.test(m.text()))errs.push(m.text().slice(0,110));});
  await p.goto('http://127.0.0.1:4178/',{waitUntil:'networkidle'});
  await p.addStyleTag({content:'*,*::before,*::after{animation:none!important;transition:none!important}'});
  await p.waitForTimeout(2200);
  const t = await p.innerText('body');
  const r = await p.evaluate(()=>({sw:document.body.scrollWidth,cw:document.body.clientWidth}));
  console.log(`  ${tag}: errors=${errs.length} overflow=${r.sw>r.cw+2?'YES':'-'} `
    + `NaN=${/\bNaN\b/.test(t)} undefined=${/\bundefined\b/.test(t)} Infinity=${/Infinity/.test(t)}`);
  for (const e of errs.slice(0,3)) console.log('      ' + e);
  // Spot-check what the entry cards actually say.
  if (tag === 'desktop') {
    const idx = t.indexOf('ENTRIES');
    console.log('   --- entry area ---');
    console.log(t.slice(idx, idx+330).split('\n').filter(Boolean).slice(0,16).map(s=>'      '+s).join('\n'));
  }
  await p.screenshot({path:`shots_replay/${tag}.png`, fullPage: tag==='desktop'});
  await ctx.close();
}
await b.close();
