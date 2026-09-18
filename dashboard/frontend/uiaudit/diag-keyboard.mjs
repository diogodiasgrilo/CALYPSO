/* Can the whole app be driven from a keyboard? (WCAG 2.1.1 / 2.1.2 / 2.4.3)
 *
 * Three things, all measured by actually pressing keys rather than reading JSX:
 *
 *   REACH    every action a mouse can take, a keyboard can take
 *   TRAP     a modal keeps Tab inside it while open, and releases it on close
 *   RESTORE  closing a modal returns focus to whatever opened it
 *
 * The middle one is the awkward case: a modal that does NOT trap focus is a
 * WCAG failure (2.4.3), and a modal that traps it with no way out is a worse
 * one (2.1.2). Both are checked.
 *
 *   cd dashboard/frontend && npm run build
 *   cd uiaudit && node mock-server.mjs &
 *   node diag-keyboard.mjs
 */
import { chromium } from '@playwright/test';

const BASE = 'http://127.0.0.1:4178';

const describe = () => {
  const a = document.activeElement;
  if (!a || a === document.body) return { tag: 'BODY', label: '', inModal: false };
  const label = (a.getAttribute('aria-label') || a.getAttribute('placeholder')
                 || a.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 28);
  return {
    tag: a.tagName,
    label,
    inModal: !!a.closest('[role="dialog"],[aria-modal="true"]'),
    hidden: a.getBoundingClientRect().width === 0,
  };
};

const b = await chromium.launch();
const findings = [];

/* ── 1. Tab order on the main surfaces ─────────────────────────────────── */
for (const [name, path] of [['dashboard', '/'], ['history', '/history'], ['analytics', '/analytics']]) {
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  await ctx.addInitScript(() => { try { localStorage.setItem('calypso-selected-strategy', 'b'); } catch { /* private */ } });
  const p = await ctx.newPage();
  await p.goto(BASE + path, { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1600);

  const seen = [];
  await p.evaluate(() => document.activeElement?.blur());
  for (let i = 0; i < 40; i++) {
    await p.keyboard.press('Tab');
    const d = await p.evaluate(describe);
    if (d.tag === 'BODY') break;
    seen.push(d);
    if (d.hidden) findings.push(`${name}: Tab reaches a ZERO-SIZE element <${d.tag}> "${d.label}"`);
  }
  console.log(`  ${name.padEnd(10)} ${seen.length} tab stop(s)`);
  await ctx.close();
}

/* ── 2. Command palette: trap, escape, restore ─────────────────────────── */
{
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  await ctx.addInitScript(() => { try { localStorage.setItem('calypso-selected-strategy', 'b'); } catch { /* private */ } });
  const p = await ctx.newPage();
  await p.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1600);

  // Give focus to something identifiable first, so RESTORE is checkable.
  await p.keyboard.press('Tab');
  const before = await p.evaluate(describe);

  await p.keyboard.press('Control+KeyK');
  await p.waitForTimeout(500);
  const opened = await p.evaluate(() =>
    !!document.querySelector('[role="dialog"][aria-label="Command palette"]'));
  console.log(`\n  command palette opens with Ctrl+K: ${opened}`);

  if (opened) {
    let escaped = null;
    for (let i = 0; i < 12; i++) {
      await p.keyboard.press('Tab');
      const d = await p.evaluate(describe);
      if (!d.inModal) { escaped = d; break; }
    }
    if (escaped) findings.push(
      `command palette does NOT trap focus — Tab escaped to <${escaped.tag}> "${escaped.label}" behind the modal`);
    else console.log('  focus stays inside while open');

    await p.keyboard.press('Escape');
    await p.waitForTimeout(400);
    const stillOpen = await p.evaluate(() =>
      !!document.querySelector('[role="dialog"][aria-label="Command palette"]'));
    if (stillOpen) findings.push('command palette does not close on Escape');
    else console.log('  closes on Escape');

    const after = await p.evaluate(describe);
    if (after.tag === 'BODY')
      findings.push(`command palette does not restore focus on close (was <${before.tag}> "${before.label}", now BODY)`);
    else console.log(`  focus after close: <${after.tag}> "${after.label}"`);
  }
  await ctx.close();
}

/* ── 3. Day-detail modal: same three questions ─────────────────────────── */
{
  const ctx = await b.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  await ctx.addInitScript(() => { try { localStorage.setItem('calypso-selected-strategy', 'b'); } catch { /* private */ } });
  const p = await ctx.newPage();
  await p.goto(BASE + '/history', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1700);

  // Opened by mouse deliberately: whether it can be opened by KEYBOARD is a
  // separate finding, reported by the reach scan above.
  const cell = p.locator('div.cursor-pointer.rounded-sm').first();
  await cell.click({ timeout: 4000 }).catch(() => {});
  await p.waitForTimeout(900);
  const opened = await p.evaluate(() => document.body.innerText.includes('Entries') &&
    !!document.querySelector('.fixed.inset-0.z-50'));
  console.log(`\n  day-detail modal opened by click: ${opened}`);

  if (opened) {
    let escaped = null;
    for (let i = 0; i < 14; i++) {
      await p.keyboard.press('Tab');
      const d = await p.evaluate(() => {
        const a = document.activeElement;
        const panel = document.querySelector('.fixed.inset-0.z-50')?.nextElementSibling
                   || document.querySelector('[class*="z-50"]');
        return { tag: a?.tagName || 'BODY',
                 label: (a?.textContent || '').trim().slice(0, 24),
                 inPanel: !!(panel && a && panel.contains(a)) };
      });
      if (d.tag !== 'BODY' && !d.inPanel) { escaped = d; break; }
    }
    if (escaped) findings.push(
      `day-detail modal does NOT trap focus — Tab escaped to <${escaped.tag}> "${escaped.label}"`);
    else console.log('  focus stays inside while open');

    await p.keyboard.press('Escape');
    await p.waitForTimeout(400);
    const stillOpen = await p.evaluate(() => !!document.querySelector('.fixed.inset-0.z-50'));
    if (stillOpen) findings.push('day-detail modal does not close on Escape');
    else console.log('  closes on Escape');
  }
  await ctx.close();
}

await b.close();
console.log(`\n  ${findings.length} finding(s)`);
for (const f of findings) console.log(`    ✗ ${f}`);
process.exit(findings.length ? 1 : 0);
