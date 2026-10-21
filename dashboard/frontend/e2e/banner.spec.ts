/** The dry-run banner must sit on ONE line at every width.
 *
 * It is the first thing on every screen and it states whether real orders can
 * be placed, so it should not be the thing that eats the viewport. Two earlier
 * failures, both found by LOOKING at screenshots rather than by any metric:
 * the full sentence wrapped to two lines on a phone (a sixth of the first
 * screen), and at tablet width it wrapped with the closing ⚠ ORPHANED alone on
 * line two.
 *
 * Three tiers now, each the longest form that fits its width. The full text is
 * always in the `title` tooltip, so no information depends on the viewport.
 */
import { expect, test } from "@playwright/test";
import { stubApi } from "./stubs";
test("dry-run banner never wraps", async ({ page }, info) => {
  await stubApi(page); await page.goto("/"); await page.waitForTimeout(600);
  // An inline element that wraps produces MORE THAN ONE client rect — the
  // precise test. Height is not, because the banner carries vertical padding.
  const d = await page.evaluate(() => {
    const host = document.querySelector('[title*="Dry-run"]');
    if (!host) return null;
    const visible = [...host.querySelectorAll("span")].filter(
      (s) => getComputedStyle(s).display !== "none");
    return visible.map((s) => ({ rects: s.getClientRects().length, text: (s.textContent||"").trim().slice(0,46) }));
  });
  console.log(`${info.project.name}: ${JSON.stringify(d)}`);
  expect(d, "banner span not found").not.toBeNull();
  for (const s of d!) expect(s.rects, `${info.project.name} wrapped: "${s.text}"`).toBe(1);
});
