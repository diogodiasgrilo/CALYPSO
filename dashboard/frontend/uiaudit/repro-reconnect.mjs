/**
 * Does the dashboard actually RECONNECT after the WebSocket drops?
 *
 * useWebSocket has a circular useCallback dependency — `connect` closes over
 * `scheduleReconnect`, which depends on `connect` — which eslint flags
 * ("accessed before it is declared"). Reading the code it appears
 * self-consistent, but "appears" is not good enough for the live trading view,
 * and restructuring reconnect logic to satisfy a linter would be the riskier
 * change. So: measure it.
 */
import { chromium } from '@playwright/test';
import { createServer } from 'node:http';
import { WebSocketServer } from 'ws';
import { handler, fx } from './mock-server.mjs';

const PORT = 4181;
const server = createServer(handler);
const wss = new WebSocketServer({ server, path: '/ws/dashboard' });

let connections = 0;
const sockets = [];
wss.on('connection', (sock) => {
  connections++;
  sockets.push(sock);
  console.log(`  [server] connection #${connections}`);
  const snap = fx('ws_snapshot');
  if (snap) sock.send(snap);
});
await new Promise(r => server.listen(PORT, r));

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
await ctx.addInitScript(() => { try { localStorage.setItem('calypso-selected-strategy','b'); } catch {} });
const page = await ctx.newPage();
await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: 'networkidle' });
await page.waitForTimeout(1500);

const status = async () => (await page.innerText('body')).match(/Connected|Disconnected|Reconnecting|error/i)?.[0] ?? '?';
console.log(`  initial status: ${await status()}  (connections=${connections})`);

console.log('  [test] dropping the socket from the server side...');
sockets.forEach(s => s.close());
await page.waitForTimeout(1200);
console.log(`  after drop:     ${await status()}`);

// Reconnect backoff starts ~1s; give it room.
await page.waitForTimeout(6000);
const finalStatus = await status();
console.log(`  after wait:     ${finalStatus}  (connections=${connections})`);

await browser.close();
await new Promise(r => { wss.close(); server.close(r); });

console.log('\n=== VERDICT ===');
if (connections >= 2 && /Connected/i.test(finalStatus)) {
  console.log('  RECONNECT WORKS — the lint warning is cosmetic; leave the code alone.');
} else if (connections >= 2) {
  console.log(`  reconnected (${connections} connections) but status reads "${finalStatus}"`);
} else {
  console.log('  DID NOT RECONNECT — the circular dependency is a REAL bug. Fix it.');
}
