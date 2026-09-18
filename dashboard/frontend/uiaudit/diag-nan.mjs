import { chromium } from '@playwright/test';
const b = await chromium.launch();
const ctx = await b.newContext({ viewport:{width:1440,height:1400}, reducedMotion:'reduce' });
await ctx.addInitScript(()=>{try{localStorage.setItem('calypso-selected-strategy','c')}catch{}});
const p = await ctx.newPage();
await p.goto('http://127.0.0.1:4178/',{waitUntil:'networkidle'});
await p.waitForTimeout(1200);
const t = await p.innerText('body');
const i = t.indexOf('ENTRIES');
console.log('  entry area:');
console.log(t.slice(i, i+300).split('\n').filter(Boolean).slice(0,14).map(s=>'     '+s).join('\n'));
const snap = await p.evaluate(async()=>{
  const r = await fetch('/api/strategies/c/snapshot'); const d = await r.json();
  const e = (d.body.entries||[])[0] || {};
  return Object.fromEntries(Object.entries(e).filter(([k])=>/credit|value|stop|strike|commission/.test(k)));
});
console.log('\n  first synthetic entry, numeric fields:');
for (const [k,v] of Object.entries(snap)) console.log(`     ${k}: ${JSON.stringify(v)}`);
await b.close();
