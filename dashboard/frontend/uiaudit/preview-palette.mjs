/* Preview an accessible palette WITHOUT changing any source file.
 *
 * Contrast is a design decision about the product's identity, so this renders
 * the candidate side by side with the current look instead of committing it.
 * Everything is injected at runtime: CSS variables for the token ramp, and a
 * JS repaint for the month-calendar cells (whose colours are inline rgba, so a
 * stylesheet cannot reach them).
 *
 *   cd uiaudit && node preview-palette.mjs     # → shots_palette/*.png
 *
 * Re-runs the contrast probe after injection so the claim "this passes" is
 * measured on the previewed pixels, not asserted from the swatches.
 */
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { CONTRAST_PROBE } from './lib-contrast.mjs';

const BASE = 'http://127.0.0.1:4178';
const OUT = 'shots_palette';
mkdirSync(OUT, { recursive: true });

/* ── The candidate ───────────────────────────────────────────────────────────
 * text-primary is untouched: it already passes everywhere (9.81:1 on the
 * lightest surface) and it carries most of the brand's character.
 * secondary and dim both rise — secondary FAILS too (4.08:1 on bg-elevated),
 * a latent failure the sampled surfaces happened not to exercise. Raising only
 * dim would have collapsed it into secondary (1.10:1 apart).           */
const TOKENS = {
  '--color-text-secondary': '#c2cad5',   // 4.08 -> 7.00 on bg-elevated
  '--color-text-dim':       '#98a5b5',   // 2.22 -> 4.60 on bg-elevated
  '--color-loss':           '#f95f58',   // 4.15 -> 4.50 on card
  '--color-profit-muted':   '#5ca18d',   // 4.43 -> 4.59 on card
};

/* The heat-map ramp is the interesting one. Today it fades a bright mint/red
 * toward the surface with rising alpha, so the STRONGEST P&L days render the
 * LIGHTEST cells — and their day numbers drop to 2.03:1, i.e. legibility is
 * inversely correlated with importance. At the crossover luminance neither
 * white nor near-black reaches 4.5:1 (max 3.80:1), so no choice of text colour
 * can rescue it: the fill has to get DARKER as it saturates, not lighter. */
const HEAT = {
  profit: ['#1a3630', '#1c4a3d', '#1a5e4a', '#147055', '#0b8360'],
  loss:   ['#3b1f1f', '#4e2421', '#642823', '#7c2a24', '#962c24'],
};

// Playwright's page.evaluate passes exactly ONE argument, so the two halves
// travel together in a single object.
/* The palette is declared TWICE — as CSS custom properties in index.css and as
 * JS constants in lib/tradingColors.ts — and P&L figures are coloured from the
 * JS copy via inline styles, which a CSS-variable override cannot reach. The
 * first run of this preview under-reported the fix for exactly that reason
 * (loss red stayed at 4.15:1). So remap computed colours directly as well. */
const REMAP = {
  'rgb(248, 81, 73)':   '#f95f58',   // loss
  'rgb(90, 158, 138)':  '#5ca18d',   // profit-muted
  'rgb(163, 113, 247)': '#a97af7',   // strategy E purple
  'rgb(88, 166, 255)':  '#5da8ff',   // info — only marginal on the blue "AH" chip
};

const INJECT = ({ tokens, heat, remap }) => {
  const st = document.createElement('style');
  st.textContent = ':root{' +
    Object.entries(tokens).map(([k, v]) => `${k}:${v} !important`).join(';') + '}';
  document.head.appendChild(st);

  // Month-calendar cells carry inline rgba(), which a stylesheet cannot
  // override, so repaint them directly. Recover each cell's intensity from the
  // alpha it was given (alpha = 0.15 + intensity*0.6) and map it onto the ramp.
  for (const el of document.querySelectorAll('*')) {
    const c = getComputedStyle(el).color;
    if (remap[c] && (el.textContent || '').trim()) el.style.color = remap[c];
  }

  for (const el of document.querySelectorAll('div[style*="rgba(126, 232, 199"], div[style*="rgba(248, 81, 73"]')) {
    const m = el.style.backgroundColor.match(/rgba?\(([^)]+)\)/);
    if (!m) continue;
    const p = m[1].split(',').map(parseFloat);
    const isProfit = p[0] < 200;                       // 126 = mint, 248 = red
    const alpha = p.length > 3 ? p[3] : 1;
    const intensity = Math.max(0, Math.min(1, (alpha - 0.15) / 0.6));
    const ramp = isProfit ? heat.profit : heat.loss;
    el.style.backgroundColor = ramp[Math.min(ramp.length - 1, Math.round(intensity * (ramp.length - 1)))];
    el.style.color = '#e8edf3';
  }
};

const tally = [];
const b = await chromium.launch();

for (const [name, path] of [['history', '/history'], ['dashboard', '/'], ['analytics', '/analytics']]) {
  for (const variant of ['before', 'after']) {
    const ctx = await b.newContext({ viewport: { width: 1440, height: 1100 }, reducedMotion: 'reduce' });
    await ctx.addInitScript(() => { try { localStorage.setItem('calypso-selected-strategy', 'b'); } catch { /* private */ } });
    const p = await ctx.newPage();
    await p.goto(BASE + path, { waitUntil: 'domcontentloaded' });
    await p.waitForTimeout(1700);
    if (variant === 'after') { await p.evaluate(INJECT, { tokens: TOKENS, heat: HEAT, remap: REMAP }); await p.waitForTimeout(350); }
    await p.screenshot({ path: `${OUT}/${name}.${variant}.png`, fullPage: false });
    // Score the pixels that were just captured, with the SAME probe that found
    // the failures — a preview claiming "this passes" is worthless otherwise.
    const bad = await p.evaluate(CONTRAST_PROBE);
    const nodes = bad.reduce((n, x) => n + x.count, 0);
    const worst = bad.length ? bad[0].ratio : '—';
    tally.push({ name, variant, combos: bad.length, nodes, worst });
    console.log(`  ${OUT}/${name}.${variant}.png    ${String(nodes).padStart(3)} failing node(s), worst ${worst}:1`);
    if (variant === 'after' && bad.length) {
      for (const x of bad.slice(0, 6)) console.log(`        still failing: ${x.ratio}:1  ${x.fg} on ${x.bg}  "${x.sample.slice(0,28)}"`);
    }
    await ctx.close();
  }
}

await b.close();

console.log('\n  surface          before → after (failing text nodes)');
for (const name of [...new Set(tally.map((t) => t.name))]) {
  const bf = tally.find((t) => t.name === name && t.variant === 'before');
  const af = tally.find((t) => t.name === name && t.variant === 'after');
  console.log(`  ${name.padEnd(16)} ${String(bf.nodes).padStart(3)} → ${String(af.nodes).padStart(3)}   worst ${bf.worst}:1 → ${af.worst}:1`);
}
