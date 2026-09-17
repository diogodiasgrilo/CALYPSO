/**
 * Does an EntryCard status transition that crosses the early-return actually
 * crash React?
 *
 * EntryCard early-returns for status "skipped"/"failed" BEFORE calling
 * useAnimatedNumber twice (EntryCard.tsx:259-260). EntryGrid keys cards by
 * entry_number, which is STABLE across the transition, so React reuses the
 * instance and the hook count changes mid-life.
 *
 * ARM A:  active -> fully-skipped  — NOT reachable: EntryGrid partitions fully-
 *          skipped entries into a separate plain-<div> list, so EntryCard
 *          unmounts instead of re-rendering with fewer hooks.
 * ARM B (control):  active -> active'  — same shape, no early return crossed.
 * ARM C (the real one): active -> execution_failed. A failed entry is NOT
 *          fully-skipped, so it stays in the `positions` list and IS re-rendered
 *          by the SAME EntryCard instance (key = entry_number) — now taking the
 *          line-184 early return with ZERO hooks after a render that had two.
 *          This is the live case: B's entry #3 silently failed on 2026-08-03.
 * If ARM B also errors, the harness is wrong, not the app.
 */
import { chromium } from '@playwright/test';
import { createServer } from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocketServer } from 'ws';
import { handler } from './mock-server.mjs';

const DIST = fileURLToPath(new URL('../dist/', import.meta.url));
const FIX  = fileURLToPath(new URL('./fixtures/', import.meta.url));
const fx = n => { const p = join(FIX, n + '.json'); return existsSync(p) ? readFileSync(p,'utf8') : null; };
const base = JSON.parse(fx('ws_snapshot'));

// Make entry #1 ACTIVE: it has an entry_time and neither side skipped.
const activeSnap = JSON.parse(JSON.stringify(base));
const e1 = activeSnap.state.entries[0];
e1.call_side_skipped = false; e1.put_side_skipped = false; e1.skip_reason = null;
e1.call_spread_credit = 250; e1.put_spread_credit = 300;
e1.call_spread_value = 100; e1.put_spread_value = 120;

async function run(arm) {
  const server = createServer(handler);
  const wss = new WebSocketServer({ server, path: '/ws/dashboard' });
  wss.on('connection', sock => {
    sock.send(JSON.stringify(activeSnap));            // 1. entry #1 ACTIVE
    setTimeout(() => {
      const next = JSON.parse(JSON.stringify(activeSnap.state));
      if (arm === 'A') {                               // diverted by EntryGrid
        next.entries[0].call_side_skipped = true;
        next.entries[0].put_side_skipped = true;
        next.entries[0].skip_reason = 'credit gate';
      } else if (arm === 'C') {                        // stays in EntryCard
        next.entries[0].execution_failed = true;
        next.entries[0].skip_reason = 'order rejected by broker';
      } else {                                         // control: stay active
        next.entries[0].call_spread_value = 140;
      }
      sock.send(JSON.stringify({ type: 'state_update', data: next }));
    }, 1600);
  });
  await new Promise(r => server.listen(4179, r));

  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await ctx.addInitScript(() => { try { localStorage.setItem('calypso-selected-strategy','b'); } catch {} });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e).split('\n')[0].slice(0, 160)));
  page.on('console', m => { if (m.type()==='error') errs.push(m.text().slice(0,160)); });
  await page.goto('http://127.0.0.1:4179/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(3500);
  const txt = await page.innerText('body').catch(()=>'');
  const bodyLen = txt.length;
  // PROVE the transition landed. Without this, "no crash" may just mean
  // "nothing changed", which is the same false pass as a page that never rendered.
  const marker = arm === 'C' ? /order rejected by broker/i : /credit gate/i;
  const sawSkip = marker.test(txt);
  console.log(`  transition applied (entry #1 shows the new skip reason): ${sawSkip}`);
  if (arm !== 'B' && !sawSkip) throw new Error(`ARM ${arm}: the state_update never reached the DOM — run invalid.`);
  await page.screenshot({ path: `shots/repro-hooks-${arm}.png`, fullPage: true });
  await browser.close();
  await new Promise(r => { wss.close(); server.close(r); });
  if (bodyLen < 200) throw new Error(`ARM ${arm}: page did not render (bodyLen=${bodyLen}) — the run is invalid, not clean.`);
  const hookErr = errs.filter(e => /hook|Hook|Rendered (more|fewer)/.test(e));
  console.log(`\nARM ${arm} (${arm==='A'?'active -> skipped, crosses early return':'active -> active, control'})`);
  console.log(`  page text length after transition: ${bodyLen}`);
  console.log(`  hook errors: ${hookErr.length}`);
  for (const e of hookErr.slice(0,3)) console.log(`    ${e}`);
  const other = errs.filter(e => !/hook|Hook|Rendered (more|fewer)/.test(e) && !/WebSocket/.test(e));
  for (const e of other.slice(0,3)) console.log(`    [other] ${e}`);
  return { arm, hookErr: hookErr.length, bodyLen };
}

const a = await run('A');
const b = await run('B');
const c = await run('C');
console.log('\n=== VERDICT ===');
console.log(`  A (active->skipped, diverted by EntryGrid): ${a.hookErr} hook errors`);
console.log(`  B (control, no early return crossed):      ${b.hookErr} hook errors`);
console.log(`  C (active->execution_failed, REACHABLE):   ${c.hookErr} hook errors`);
if (c.hookErr > 0 && b.hookErr === 0) console.log('\n  CONFIRMED: the reachable transition crashes React; the control is clean.');
else if (c.hookErr === 0 && b.hookErr === 0) console.log('\n  NOT reproduced even on the reachable path — the lint error is latent, not live.');
else console.log('\n  INCONCLUSIVE: control also errored — harness suspect.');
