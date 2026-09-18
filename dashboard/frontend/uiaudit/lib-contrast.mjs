/* Shared WCAG 1.4.3 probe, used by diag-contrast.mjs (measure the app as it is)
 * and preview-palette.mjs (measure the app with a candidate palette injected).
 *
 * Extracted so the two cannot drift: a preview that says "this passes" is only
 * worth anything if it is scored by the SAME code that reported the failures.
 *
 * Exported as a source string rather than a function because it is evaluated
 * inside the page, where module scope is not available.
 */

export const CONTRAST_PROBE = () => {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map((x) => parseFloat(x));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });
  const lum = ({ r, g, b }) => {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m);
    return (x + 0.05) / (y + 0.05);
  };
  const effectiveBg = (el) => {
    const stack = [];
    let node = el;
    while (node && node !== document.documentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg && bg.a > 0) {
        stack.push(bg);
        if (bg.a === 1) break;
      }
      node = node.parentElement;
    }
    let base = parse(getComputedStyle(document.documentElement).backgroundColor);
    if (!base || base.a === 0) base = { r: 13, g: 17, b: 23, a: 1 };
    let acc = base;
    for (let i = stack.length - 1; i >= 0; i--) acc = over(stack[i], acc);
    return acc;
  };

  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Map();
  let n;
  while ((n = walker.nextNode())) {
    const text = (n.textContent || '').trim();
    if (!text) continue;
    const el = n.parentElement;
    if (!el) continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (el.closest('[disabled],[aria-hidden="true"]')) continue;

    const fgRaw = parse(s.color);
    if (!fgRaw) continue;
    const bg = effectiveBg(el);
    const fg = fgRaw.a < 1 ? over(fgRaw, bg) : fgRaw;

    const px = parseFloat(s.fontSize);
    const weight = parseInt(s.fontWeight, 10) || 400;
    const isLarge = px >= 24 || (px >= 18.66 && weight >= 700);
    const need = isLarge ? 3.0 : 4.5;
    const got = ratio(fg, bg);
    if (got >= need) continue;

    const key = `${s.color}|${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)}|${px}|${weight}`;
    if (seen.has(key)) { seen.get(key).count++; continue; }
    const row = {
      key, count: 1,
      ratio: Math.round(got * 100) / 100, need, px, weight,
      fg: `rgb(${Math.round(fg.r)},${Math.round(fg.g)},${Math.round(fg.b)})`,
      bg: `rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`,
      cls: (el.className || '').toString().slice(0, 56),
      sample: text.slice(0, 40),
    };
    seen.set(key, row);
    out.push(row);
  }
  return out.sort((a, b) => a.ratio - b.ratio);
};
