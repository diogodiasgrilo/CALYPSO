/* What does the dashboard do when the data is BAD?
 *
 * Every fixture in this harness is happy-path: a populated, well-formed
 * response captured from a working backend. So across 42 audited surfaces the
 * app has never once been rendered against the things that actually happen in
 * production — a 500, an empty result, a field that is null because the bot
 * hasn't computed it yet, or a number that arrived as a string.
 *
 * Each mode below rewrites every /api/ response before it reaches the app, then
 * checks the rendered page for the symptoms that matter:
 *
 *   CRASH        an uncaught error, or the React root emptied
 *   PLACEHOLDER  "undefined" / "NaN" / "[object Object]" / "Infinity" on screen
 *   SILENT       a totally blank surface with no explanation to the user
 *
 * An empty dataset SHOULD render an empty state. A 500 SHOULD say something.
 * Neither should print NaN or a white page.
 *
 *   cd dashboard/frontend && npm run build
 *   cd uiaudit && FIXTURES=./fixtures_synthetic/ node mock-server.mjs &
 *   node diag-degraded.mjs
 */
import { chromium } from '@playwright/test';

const BASE = 'http://127.0.0.1:4178';

const SURFACES = [
  ['dashboard', '/'],
  ['history', '/history'],
  ['analytics', '/analytics'],
  ['comparison-ic', '/comparison/ic_0dte'],
  ['comparison-cal', '/comparison/calendar_multiday'],
  ['dc', '/dc'],
];

/** How to corrupt an upstream JSON body. Each returns a NEW body string. */
const MODES = {
  // The backend is down or throwing.
  'http-500': () => ({ status: 500, body: '{"detail":"internal error"}' }),

  // Valid shape, no rows. The commonest real case: a brand-new variant, a
  // pre-market load, a date with no trading.
  empty: (json) => ({
    status: 200,
    body: JSON.stringify(emptied(json)),
  }),

  // Every leaf null. The bot writes rows before it has computed every field,
  // so partial nulls reach the UI routinely.
  'null-fields': (json) => ({
    status: 200,
    body: JSON.stringify(nulled(json)),
  }),

  // Numbers as strings — what an un-coerced API or a JSON round-trip through
  // a text column produces. `"12" * 2` is 24 but `"12" + 2` is "122", and
  // .toFixed() on a string throws.
  'numbers-as-strings': (json) => ({
    status: 200,
    body: JSON.stringify(stringified(json)),
  }),
};

function emptied(v) {
  if (Array.isArray(v)) return [];
  if (v && typeof v === 'object') {
    const out = {};
    for (const [k, val] of Object.entries(v)) out[k] = emptied(val);
    return out;
  }
  return v;
}

function nulled(v) {
  if (Array.isArray(v)) return v.map(nulled);
  if (v && typeof v === 'object') {
    const out = {};
    for (const [k, val] of Object.entries(v)) out[k] = nulled(val);
    return out;
  }
  return null;
}

function stringified(v) {
  if (Array.isArray(v)) return v.map(stringified);
  if (v && typeof v === 'object') {
    const out = {};
    for (const [k, val] of Object.entries(v)) out[k] = stringified(val);
    return out;
  }
  return typeof v === 'number' ? String(v) : v;
}

const b = await chromium.launch();
const findings = [];

for (const [mode, transform] of Object.entries(MODES)) {
  for (const [name, path] of SURFACES) {
    const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
    await ctx.addInitScript(() => { try { localStorage.setItem('calypso-selected-strategy', 'b'); } catch { /* private */ } });
    const p = await ctx.newPage();

    const crashes = [];
    p.on('pageerror', (e) => crashes.push(String(e).slice(0, 140)));

    await p.route('**/api/**', async (route) => {
      // /api/auth/me must keep working or we only ever test the login gate.
      if (route.request().url().includes('/api/auth/')) return route.continue();
      let original = {};
      try {
        const res = await route.fetch();
        original = await res.json();
      } catch { /* upstream gave non-JSON; transform the empty object */ }
      const { status, body } = transform(original);
      return route.fulfill({ status, contentType: 'application/json', body });
    });

    await p.goto(BASE + path, { waitUntil: 'domcontentloaded' }).catch(() => {});
    await p.waitForTimeout(2200);

    // Text OUTSIDE any error boundary. The boundary deliberately prints the
    // raw exception message so an operator can act on it, and those messages
    // routinely contain the word "undefined" ("Cannot read properties of
    // undefined") — scanning them flags the very mechanism that FIXED the
    // white page. The boundary is identified by role="alert".
    // Hide the boundaries, read innerText from the LIVE document, restore.
    // A detached cloneNode has no layout, so its innerText silently degrades to
    // textContent semantics and returns different text — which made a real
    // `$NaN` finding disappear when this was first written as a clone.
    const text = await p.evaluate(() => {
      const alerts = [...document.querySelectorAll('[role="alert"]')];
      const prev = alerts.map((el) => el.style.display);
      alerts.forEach((el) => { el.style.display = 'none'; });
      const t = document.body.innerText || '';
      alerts.forEach((el, i) => { el.style.display = prev[i]; });
      return t;
    }).catch(() => '');
    const boundaries = await p.evaluate(
      () => document.querySelectorAll('[role="alert"]').length).catch(() => 0);
    const rootChildren = await p.evaluate(
      () => document.getElementById('root')?.childElementCount ?? 0).catch(() => 0);

    const bad = [];
    if (crashes.length) bad.push(`CRASH ${crashes[0]}`);
    if (rootChildren === 0) bad.push('CRASH react root is empty');
    const placeholder = text.match(/(^|[^-\w])(undefined|NaN|\[object Object\]|Infinity)([^-\w]|$)/);
    if (placeholder) {
      const i = Math.max(0, placeholder.index - 40);
      bad.push(`PLACEHOLDER …${text.slice(i, placeholder.index + 50).replace(/\s+/g, ' ')}…`);
    }
    // A boundary showing its message is a HANDLED failure, not a silent one.
    if (text.trim().length < 30 && !crashes.length && !boundaries)
      bad.push(`SILENT len=${text.trim().length}`);

    if (bad.length) findings.push({ mode, name, bad });
    console.log(`  ${bad.length ? '✗' : '·'} ${mode.padEnd(20)} ${name.padEnd(16)} len=${String(text.trim().length).padStart(5)}`
                + (boundaries ? ` [${boundaries} boundary caught it]` : '')
                + (bad.length ? `\n      ${bad.join('\n      ')}` : ''));
    await ctx.close();
  }
}

await b.close();
console.log(`\n  ${findings.length} of ${Object.keys(MODES).length * SURFACES.length} surface/mode combinations with findings`);
process.exit(findings.length ? 1 : 0);
