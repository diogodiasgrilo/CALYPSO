/**
 * Serves the built dashboard + REAL API payloads captured from the VM.
 * Lets the UI be rendered and screenshotted without login credentials.
 */
import { createServer } from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocketServer } from 'ws';

const PORT = Number(process.env.PORT || 4178);
const DIST = fileURLToPath(new URL('../dist/', import.meta.url));
const FIX  = fileURLToPath(new URL(process.env.FIXTURES || './fixtures/', import.meta.url));

const MIME = { '.html':'text/html', '.js':'text/javascript', '.css':'text/css',
  '.json':'application/json', '.svg':'image/svg+xml', '.png':'image/png',
  '.ico':'image/x-icon', '.webmanifest':'application/manifest+json' };

export const fx = (n) => { const p = join(FIX, n + '.json'); return existsSync(p) ? readFileSync(p,'utf8') : null; };

/** Map a request to a captured fixture; `sid` is the ?strategy_id= param. */
export function resolveFixture(pathname, sid) {
  const s = sid || 'b';
  const direct = {
    '/api/strategies/meta': 'strategies_meta',
    '/api/agents/status': 'agents_status',
    '/api/market/status': 'market_status',
    '/api/market/ohlc': 'market_ohlc',
    '/api/variants/health': 'variants_health',
  }[pathname];
  if (direct) return fx(direct);

  let m;
  if ((m = pathname.match(/^\/api\/strategies\/([a-z])\/snapshot$/))) return fx(`snapshot_${m[1]}`);
  if (pathname === '/api/hydra/state')      return fx(`hydra_state_${s}`);
  if (pathname === '/api/hydra/summary')    return fx(`hydra_summary_${s}`);
  if (pathname === '/api/hydra/bot-config') return fx(`hydra_botconfig_${s}`);
  if (pathname === '/api/metrics/cumulative') return fx(`metrics_cumulative_${s}`);
  if (pathname === '/api/metrics/daily')      return fx(`metrics_daily_${s}`);
  if (pathname === '/api/metrics/range')      return fx(`metrics_range_${s}`);
  if (pathname === '/api/metrics/entries')    return fx(`metrics_entries_${s}`);
  if (pathname === '/api/metrics/stops')      return fx(`metrics_stops_${s}`);
  if (pathname === '/api/metrics/performance')return fx(`metrics_performance_${s}`);
  if (pathname === '/api/metrics/comparisons')return fx(`metrics_comparisons_${s}`);
  if (pathname === '/api/dc/status')          return fx(`dc_status_${s === 'e' ? 'e' : 'd'}`);
  if (pathname === '/api/auth/me')            return JSON.stringify({ username: 'uiaudit' });
  if (pathname === '/api/health')             return JSON.stringify({ status: 'ok' });
  if (pathname.startsWith('/api/hydra/entries')) return fx(`hydra_entries_${s}`);
  if (pathname === '/api/market/ticks')      return fx('market_ticks');
  if (pathname === '/api/market/replay_pnl') return fx(`replay_pnl_${s}`);
  if ((m = pathname.match(/^\/api\/strategies\/groups\/([a-z_0-9]+)\/comparison$/))) return fx(`group_comparison_${m[1]}`);
  if ((m = pathname.match(/^\/api\/strategies\/groups\/([a-z_0-9]+)\/aggregate$/)))  return fx(`group_aggregate_${m[1]}`);
  return null;
}

export const handler = (req, res) => {
  const url = new URL(req.url, 'http://x');
  const sid = url.searchParams.get('strategy_id') || '';
  if (url.pathname.startsWith('/api/')) {
    const body = resolveFixture(url.pathname, sid);
    res.writeHead(body ? 200 : 404, { 'Content-Type': 'application/json' });
    return res.end(body ?? JSON.stringify({ error: 'no fixture', path: url.pathname }));
  }
  // static, SPA-fallback to index.html
  let p = join(DIST, url.pathname === '/' ? 'index.html' : url.pathname);
  if (!existsSync(p) || !extname(p)) p = join(DIST, 'index.html');
  res.writeHead(200, { 'Content-Type': MIME[extname(p)] || 'application/octet-stream' });
  res.end(readFileSync(p));
};

// The PRIMARY variant renders from the live WebSocket, not from /snapshot
// (StrategyDashboard.tsx:58). Without a WS the live seat renders an empty
// shell — so auditing it at all requires replaying the real broadcast payload.
if (process.argv[1] && process.argv[1].endsWith('mock-server.mjs')) {
  const server = createServer(handler);
  const wss = new WebSocketServer({ server, path: '/ws/dashboard' });
  wss.on('connection', (sock) => {
    const snap = fx('ws_snapshot');
    if (snap) sock.send(snap);
  });
  server.listen(PORT, () => console.log(`ui-audit mock (http+ws) on http://127.0.0.1:${PORT}`));
}
