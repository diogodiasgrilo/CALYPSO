/* Can a keyboard user SEE where they are? (WCAG 2.4.7 Focus Visible)
 *
 * Empirical, not a grep for `outline-none`: focus each control in turn and
 * diff its computed style against its unfocused state. An element is a finding
 * only if NOTHING observable changes — outline, box-shadow, border colour,
 * background and ring are all identical focused vs not.
 *
 *   cd dashboard/frontend && npm run build
 *   cd uiaudit && node mock-server.mjs &
 *   node diag-focus.mjs
 */
import { chromium } from '@playwright/test';

const BASE = 'http://127.0.0.1:4178';

/** The login gate, then the authenticated surfaces. */
const SURFACES = [
  { name: 'login-gate', path: '/', auth: false },
  { name: 'dashboard', path: '/', auth: true },
  { name: 'history', path: '/history', auth: true },
  { name: 'analytics', path: '/analytics', auth: true },
  { name: 'comparison', path: '/comparison/ic_0dte', auth: true },
];

const PROBE = async () => {
  // Only VISUALLY MEANINGFUL properties, which is the whole subtlety here.
  // A first version compared raw computed values and under-reported: the
  // password field's outline-width changed 3px -> 1px on focus while
  // outline-style stayed `none`, so the detector scored it "has a focus state"
  // for a ring the browser never draws. Width and colour of an undrawn outline
  // say nothing; the same goes for a border whose style is none.
  const props = (el) => {
    const s = getComputedStyle(el);
    const outline = s.outlineStyle === 'none'
      ? 'none' : `${s.outlineStyle} ${s.outlineWidth} ${s.outlineColor}`;
    const border = s.borderStyle === 'none'
      ? 'none' : `${s.borderStyle} ${s.borderWidth} ${s.borderColor}`;
    return [outline, border, s.boxShadow, s.backgroundColor, s.color,
            s.textDecorationLine].join('|');
  };
  // Most focus styles here ride on `transition-colors`, so the change is not
  // present in the computed style until the transition has advanced. Reading
  // synchronously after .focus() reports the START colour and marks a working
  // control as broken — that produced a false positive on History's year
  // <select>, which in fact animates to a visible border over ~200ms. Wait for
  // the declared duration before sampling; skip the wait where there is none.
  const settle = (el) => {
    const d = getComputedStyle(el).transitionDuration || '0s';
    const ms = Math.max(...d.split(',').map((x) => parseFloat(x) * (x.includes('ms') ? 1 : 1000) || 0));
    return new Promise((r) => setTimeout(r, ms > 0 ? ms + 60 : 0));
  };

  const sel = 'button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])';
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;         // not actually reachable
    if (el.hasAttribute('disabled')) continue;
    // An autoFocus'd field (the login form's username) is ALREADY focused when
    // its "before" sample is taken, so before and after match trivially and it
    // reports as having no focus style however good its focus style is. Blur
    // first and let any transition unwind.
    if (document.activeElement === el) { el.blur(); await settle(el); }
    const before = props(el);
    el.focus();
    await settle(el);
    const after = props(el);
    el.blur();
    if (before !== after) continue;                         // something changed — fine
    const label = (el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
                   el.textContent || el.tagName).trim().replace(/\s+/g, ' ').slice(0, 34);
    const key = el.tagName + '::' + label;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ tag: el.tagName.toLowerCase(), label,
               cls: (el.className || '').toString().slice(0, 70) });
  }
  return out;
};

const b = await chromium.launch();
let total = 0;

for (const s of SURFACES) {
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  await ctx.addInitScript(() => { try { localStorage.setItem('calypso-selected-strategy', 'b'); } catch { /* private mode */ } });
  const p = await ctx.newPage();
  if (!s.auth) await p.route('**/api/auth/me', (r) => r.fulfill({ status: 401, body: '{}' }));
  await p.goto(BASE + s.path, { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1400);

  const bad = await p.evaluate(PROBE);
  total += bad.length;
  console.log(`\n=== ${s.name} — ${bad.length} control(s) with NO visible focus state ===`);
  for (const x of bad) console.log(`  <${x.tag}> ${x.label}\n      ${x.cls}`);
  await ctx.close();
}

await b.close();
console.log(`\n  ${total} total`);
process.exit(total ? 1 : 0);
