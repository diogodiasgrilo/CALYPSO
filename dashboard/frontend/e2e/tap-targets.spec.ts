/** Every tappable control must meet Apple's 44x44pt minimum on a phone.
 *
 * WHY THIS, AND NOT HORIZONTAL OVERFLOW. The first version of this harness
 * asserted `documentElement.scrollWidth <= clientWidth`, on the assumption that
 * 78 un-prefixed `grid-cols-N` would burst the viewport. Measured against real
 * captured data, ALL 12 of those passed: Tailwind grid columns SHRINK rather
 * than overflow, so a 4-column grid at 390px yields four ~95px columns — ugly,
 * but not a scroll. Overflow was simply the wrong proxy for "bad on a phone".
 *
 * What the instrumented pass actually found, per page at iPhone-12 width:
 *
 *     narrow grid cells (<120px) : 0, 0, 0, and 2 on History
 *     truncated text elements    : 0 everywhere
 *     tap targets under 44x44    : 7-12 PER PAGE   <- the real defect
 *
 * So this is the assertion that earns its place. It is also the one that maps
 * directly onto the stated goal: 44x44pt is the Human Interface Guidelines
 * minimum, and controls at 32x32 are the single most Apple-unlike thing here.
 *
 * Scoped to the phone project — a 32px icon button is fine under a mouse.
 */
import { expect, test } from "@playwright/test";
import { stubApi } from "./stubs";

const MIN = 44;

const ROUTES = [
  ["dashboard", "/"],
  ["history", "/history"],
  ["analytics", "/analytics"],
  ["long-strangle", "/long-strangle"],
] as const;

for (const [name, path] of ROUTES) {
  test(`${name}: tap targets meet 44x44`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "pointer-size rule applies to touch");
    await stubApi(page);
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(400);

    const small = await page.evaluate((min) => {
      const out: { label: string; w: number; h: number }[] = [];
      document.querySelectorAll<HTMLElement>("button, a, [role=button]").forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return;        // hidden
        if (getComputedStyle(el).display === "none") return;
        if (r.width < min || r.height < min) {
          out.push({
            label: (el.innerText || el.getAttribute("aria-label") || el.className.toString()).slice(0, 36),
            w: Math.round(r.width), h: Math.round(r.height),
          });
        }
      });
      return out;
    }, MIN);

    expect(small, `${name}: ${small.length} control(s) under ${MIN}x${MIN}pt — ` +
      small.map((s) => `"${s.label}" ${s.w}x${s.h}`).join(", ")).toEqual([]);
  });
}

test("history day-detail MODAL: tap targets meet 44x44", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "pointer-size rule applies to touch");

  // ⚠️ THE BLIND SPOT THIS CLOSES. The tests above only ever load a page in its
  // INITIAL state, so nothing behind an interaction was measured. Opening the
  // day-detail modal found FIVE controls under the minimum — the day-step
  // arrows at 24x24, the CLOSE button at 28x28 (the most important control in
  // any modal) and both tabs at 24px tall. A green suite said nothing about
  // them, because a suite only covers what it actually renders.
  await stubApi(page);
  await page.goto("/history");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(600);

  const cells = page.locator('[class*="cursor-pointer"][class*="rounded-sm"]');
  const n = await cells.count();
  let opened = false;
  for (let i = 0; i < Math.min(n, 40); i++) {
    const t = (await cells.nth(i).innerText().catch(() => "")) || "";
    if (t.trim()) { await cells.nth(i).click().catch(() => {}); opened = true; break; }
  }
  expect(opened, "no calendar day was clickable — the modal was never opened, "
    + "so this test would pass without testing anything").toBe(true);
  await page.waitForTimeout(600);

  const small = await page.evaluate((min) => {
    const out: { label: string; w: number; h: number }[] = [];
    document.querySelectorAll<HTMLElement>("button, a, [role=button]").forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return;
      if (getComputedStyle(el).display === "none") return;
      if (r.width < min || r.height < min) {
        out.push({
          label: (el.innerText || el.getAttribute("aria-label") || el.className.toString()).slice(0, 36),
          w: Math.round(r.width), h: Math.round(r.height),
        });
      }
    });
    return out;
  }, MIN);

  expect(small, `modal: ${small.length} control(s) under ${MIN}x${MIN}pt — ` +
    small.map((s) => `"${s.label}" ${s.w}x${s.h}`).join(", ")).toEqual([]);
});
