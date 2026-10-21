/** A scroll edge may be faded ONLY while it genuinely overflows.
 *
 * `.table-scroll` / `.nav-scroll` fade their right edge so a cut-off column or
 * tab reads as "continues" rather than "broken". CSS cannot ask whether an
 * element overflows, so the mask first applied at EVERY width — and measured,
 * the comparison tables do NOT overflow on desktop (scrollWidth 1374 ==
 * clientWidth 1374) or tablet (696 == 696). The fade was dimming the last
 * column of a table that fits, which is strictly worse than no fade: it hides
 * data to signal a scroll that does not exist.
 *
 * `useOverflowFade` measures and sets `data-overflowing`; the CSS keys off it.
 * The default (attribute absent) is UNFADED, so a failure to measure shows
 * data rather than hiding it — the right direction to fail for a financial
 * table.
 *
 * ⚠️ IT ASSERTS THE COMPUTED `mask-image`, NOT the data attribute. The first
 * version compared `data-overflowing` against measured overflow — which the
 * hook sets itself, so the test could not see the thing that actually renders.
 * Its negative control proved it: re-applying the mask unconditionally in CSS
 * left the attribute correct and the suite GREEN while every desktop table was
 * visibly faded again.
 *
 * Meaningful at every viewport because the expected answer DIFFERS: no mask on
 * desktop/tablet, a mask on phone.
 */
import { expect, test } from "@playwright/test";
import { stubApi } from "./stubs";

const PAGES = ["/comparison/ic_0dte", "/history", "/"] as const;

for (const path of PAGES) {
  test(`fade matches real overflow on ${path}`, async ({ page }, info) => {
    await stubApi(page);
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(800);

    const rows = await page.evaluate(() => {
      const out: { cls: string; really: boolean; faded: boolean }[] = [];
      document.querySelectorAll<HTMLElement>(".table-scroll, .nav-scroll").forEach((el) => {
        const mask = getComputedStyle(el).maskImage ||
                     (getComputedStyle(el) as unknown as Record<string, string>).webkitMaskImage;
        out.push({
          cls: el.className.toString().slice(0, 40),
          really: el.scrollWidth > el.clientWidth + 2,
          // What the browser will ACTUALLY paint.
          faded: !!mask && mask !== "none",
        });
      });
      return out;
    });

    expect(rows.length, `${path}: no scroll containers found — this test would ` +
      `pass without checking anything`).toBeGreaterThan(0);

    const wrong = rows.filter((r) => r.really !== r.faded);
    expect(wrong, `${path} @ ${info.project.name}: ` + wrong.map((r) =>
      `"${r.cls}" overflow=${r.really} faded=${r.faded}`).join(", ")).toEqual([]);
  });
}
