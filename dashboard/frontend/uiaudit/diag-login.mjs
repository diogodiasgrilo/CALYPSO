/* The login gate — the one surface the whole visual audit skipped.
 *
 * Every screen audited so far was reached through mock-server.mjs with
 * authentication switched off, so the real entry path to the dashboard had
 * never once been rendered. This drives each step of LoginGate.tsx by
 * intercepting /api/auth/* with page.route(), at desktop AND phone width, and
 * runs the same overflow detector used on every other surface.
 *
 *   cd dashboard/frontend && npm run build
 *   cd uiaudit && node mock-server.mjs &
 *   node diag-login.mjs            # → shots_login/*.png
 */
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const BASE = 'http://127.0.0.1:4178';
const OUT = 'shots_login';
mkdirSync(OUT, { recursive: true });

// A real 160x160 SVG so the QR <img> has genuine intrinsic size — a 1x1
// placeholder would collapse the layout and hide any overflow it causes.
const QR = 'data:image/svg+xml;base64,' + Buffer.from(
  `<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160"><rect width="160" height="160" fill="#fff"/>` +
  Array.from({ length: 144 }, (_, i) =>
    (i * 7 % 11 < 5)
      ? `<rect x="${(i % 12) * 13 + 2}" y="${Math.floor(i / 12) * 13 + 2}" width="11" height="11" fill="#000"/>`
      : '').join('') + `</svg>`
).toString('base64');

const CODES = Array.from({ length: 10 }, (_, i) =>
  `${(i + 10).toString(16).repeat(2)}f${i}-9c${i}a${i}`);

/** Each scenario: what /api/auth/* returns, and how far to drive the form. */
const SCENARIOS = [
  { name: 'step1-login', routes: { me: 401 }, drive: null },
  { name: 'step1-login-bad-password',
    routes: { me: 401, login: [401, { detail: 'Invalid username or password.' }] },
    drive: 'submitLogin' },
  { name: 'step1-login-rate-limited',
    routes: { me: 401, login: [429, { detail: 'Too many attempts from this address — try again in a few minutes.' }] },
    drive: 'submitLogin' },
  { name: 'step1-login-locked',
    routes: { me: 401, login: [423, { detail: 'Account temporarily locked after repeated failed attempts. Try again later.' }] },
    drive: 'submitLogin' },
  { name: 'step2-change-password',
    routes: { me: 401, login: [200, { step: 'change_password', pending_token: 'pt' }] },
    drive: 'submitLogin' },
  { name: 'step3-totp-enrolled',
    routes: { me: 401, login: [200, { step: 'totp', pending_token: 'pt', totp_enabled: true }] },
    drive: 'submitLogin' },
  { name: 'step3-totp-first-enrollment',
    routes: { me: 401, login: [200, { step: 'totp', pending_token: 'pt', totp_enabled: false }],
              'setup-totp': [200, { pending_token: 'pt', secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP', qr_data_uri: QR }] },
    drive: 'submitLogin' },
  { name: 'step4-recovery-codes-issued',
    routes: { me: 401, login: [200, { step: 'totp', pending_token: 'pt', totp_enabled: false }],
              'setup-totp': [200, { pending_token: 'pt', secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP', qr_data_uri: QR }],
              'verify-totp': [200, { ok: true, username: 'diogo', recovery_codes: CODES }] },
    drive: 'submitTotp' },
  // The two screens added today.
  { name: 'step5-recovery-used-3-left',
    routes: { me: 401, login: [200, { step: 'totp', pending_token: 'pt', totp_enabled: true }],
              'verify-totp': [200, { ok: true, username: 'diogo', recovery_code_used: true, recovery_codes_remaining: 3 }] },
    drive: 'submitTotp' },
  { name: 'step5-recovery-used-1-left',
    routes: { me: 401, login: [200, { step: 'totp', pending_token: 'pt', totp_enabled: true }],
              'verify-totp': [200, { ok: true, username: 'diogo', recovery_code_used: true, recovery_codes_remaining: 1 }] },
    drive: 'submitTotp' },
  { name: 'step5-recovery-used-EXHAUSTED',
    routes: { me: 401, login: [200, { step: 'totp', pending_token: 'pt', totp_enabled: true }],
              'verify-totp': [200, { ok: true, username: 'diogo', recovery_code_used: true, recovery_codes_remaining: 0 }] },
    drive: 'submitTotp' },
  { name: 'step3-totp-bad-code',
    routes: { me: 401, login: [200, { step: 'totp', pending_token: 'pt', totp_enabled: true }],
              'verify-totp': [401, { detail: 'Invalid code.' }] },
    drive: 'submitTotp' },
];

const VIEWPORTS = [
  { tag: 'desktop', width: 1440, height: 900 },
  { tag: 'phone', width: 390, height: 844 },
];

/** Same root-cause overflow detector the other surfaces use. */
const OVERFLOW = () => {
  const VW = document.documentElement.clientWidth;
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width <= VW + 2 && r.right <= VW + 2) continue;
    let anc = el.parentElement, inScroller = false;
    while (anc && anc !== document.body) {
      const ov = getComputedStyle(anc).overflowX;
      if ((ov === 'auto' || ov === 'scroll') && anc.scrollWidth > anc.clientWidth) { inScroller = true; break; }
      anc = anc.parentElement;
    }
    if (inScroller) continue;
    const pr = el.parentElement?.getBoundingClientRect();
    if (pr && (pr.width > VW + 2 || pr.right > VW + 2)) continue;
    out.push({ tag: el.tagName.toLowerCase(), w: Math.round(r.width),
               cls: (el.className || '').toString().slice(0, 60) });
  }
  return { VW, scrollW: document.documentElement.scrollWidth, out: out.slice(0, 5) };
};

/** Anything a person is meant to tap must clear ~44px (Apple HIG). */
const TAPTARGETS = () => [...document.querySelectorAll('button, input, a[href]')]
  .map((el) => {
    const r = el.getBoundingClientRect();
    return { tag: el.tagName.toLowerCase(), h: Math.round(r.height),
             label: (el.getAttribute('placeholder') || el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 28) };
  })
  .filter((t) => t.h > 0 && t.h < 44);

const b = await chromium.launch();
const findings = [];

for (const sc of SCENARIOS) {
  for (const vp of VIEWPORTS) {
    const ctx = await b.newContext({
      viewport: { width: vp.width, height: vp.height }, reducedMotion: 'reduce',
    });
    const p = await ctx.newPage();
    const consoleErrors = [];
    // Every scenario here starts logged OUT, so /api/auth/me deliberately
    // returns 401 and the browser logs a failed-resource error for it. That is
    // exactly what a real signed-out visitor sees; counting it as a finding
    // marks all 24 renders red and buries the genuine ones.
    const EXPECTED = /Failed to load resource.*(401|403|429|423)/;
    p.on('console', (m) => {
      const t = m.text();
      if (m.type() === 'error' && !EXPECTED.test(t)) consoleErrors.push(t.slice(0, 120));
    });
    p.on('pageerror', (e) => consoleErrors.push('PAGEERROR ' + String(e).slice(0, 120)));

    await p.route('**/api/auth/**', async (route) => {
      const path = new URL(route.request().url()).pathname.split('/').pop();
      const spec = sc.routes[path];
      if (spec === undefined) return route.fulfill({ status: 404, body: '{}' });
      if (typeof spec === 'number')
        return route.fulfill({ status: spec, contentType: 'application/json', body: '{}' });
      return route.fulfill({ status: spec[0], contentType: 'application/json',
                             body: JSON.stringify(spec[1]) });
    });

    await p.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    await p.waitForSelector('input[autocomplete="username"]', { timeout: 8000 }).catch(() => {});

    if (sc.drive) {
      await p.fill('input[autocomplete="username"]', 'diogo').catch(() => {});
      await p.fill('input[autocomplete="current-password"]', 'a-real-password').catch(() => {});
      await p.click('button[type=submit]').catch(() => {});
      await p.waitForTimeout(400);
      if (sc.drive === 'submitTotp') {
        await p.fill('input[autocomplete="one-time-code"]', '123456').catch(() => {});
        await p.click('button[type=submit]').catch(() => {});
        await p.waitForTimeout(400);
      }
    }
    await p.waitForTimeout(250);

    const ov = await p.evaluate(OVERFLOW);
    const taps = vp.tag === 'phone' ? await p.evaluate(TAPTARGETS) : [];
    const visible = (await p.evaluate(() => document.body.innerText)).replace(/\s+/g, ' ').trim();

    await p.screenshot({ path: `${OUT}/${sc.name}.${vp.tag}.png`, fullPage: false });

    const bad = [];
    if (ov.scrollW > ov.VW + 2) bad.push(`H-OVERFLOW scrollW=${ov.scrollW} vw=${ov.VW}`);
    if (consoleErrors.length) bad.push(`CONSOLE ${consoleErrors[0]}`);
    if (taps.length) bad.push(`SMALL-TAP ${taps.map((t) => `${t.label}:${t.h}px`).join(', ')}`);
    if (!visible || visible.length < 20) bad.push(`BLANK len=${visible.length}`);
    if (/\bundefined\b|NaN|\[object Object\]/.test(visible)) bad.push(`PLACEHOLDER in text`);

    findings.push({ scenario: sc.name, vp: vp.tag, bad, len: visible.length });
    const mark = bad.length ? '  ✗' : '  ·';
    console.log(`${mark} ${sc.name.padEnd(32)} ${vp.tag.padEnd(8)} len=${String(visible.length).padStart(4)}` +
                (bad.length ? `\n        ${bad.join('\n        ')}` : ''));
    await ctx.close();
  }
}

await b.close();
const failed = findings.filter((f) => f.bad.length);
console.log(`\n  ${findings.length} renders, ${failed.length} with findings → ${OUT}/`);
process.exit(failed.length ? 1 : 0);
