/** The dashboard must not scroll sideways on a phone.
 *
 * THE ASSERTION. `scrollWidth > clientWidth` on the document is the universal
 * symptom of a layout that does not fit: a grid that keeps N columns at 390px,
 * a table with no overflow wrapper, an element with a min-width larger than the
 * viewport. It needs no knowledge of the design, so it cannot go stale as the
 * design changes, and it fails for exactly the reason a human would complain.
 *
 * A small tolerance absorbs sub-pixel rounding in transformed/scaled elements;
 * anything beyond it is a real overflow a finger can feel.
 *
 * 🔴 NOT YET LOAD-BEARING. `stubs.ts` returns empty shapes, so every page
 * currently renders its "No data yet" state — and an empty page cannot
 * overflow. All 12 of these passed on the UNMODIFIED desktop-only dashboard,
 * which means they are presently proving nothing about responsiveness.
 *
 * To make them real, the stubs need REALISTIC fixtures (a populated entry
 * grid, a full history table, a 30-day metrics series). The live backend
 * refuses unauthenticated reads ({"detail":"Not authenticated"}), so the
 * supported route is `dev_mode_open()` — run the backend locally against a
 * copy of the SQLite databases with zero accounts configured, capture the real
 * responses, and commit them as fixtures. Until that is done, treat a green
 * run here as "the harness executes", NOT as "the layout fits".
 */
import { expect, test } from "@playwright/test";
import { stubApi } from "./stubs";

const TOLERANCE_PX = 2;

const ROUTES = [
  ["dashboard", "/"],
  ["history", "/history"],
  ["analytics", "/analytics"],
  ["long-strangle", "/long-strangle"],
] as const;

for (const [name, path] of ROUTES) {
  test(`${name} does not overflow horizontally`, async ({ page }, testInfo) => {
    await stubApi(page);
    await page.goto(path);
    // Let the SPA mount and the gate resolve.
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(400);

    const { scrollWidth, clientWidth } = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));

    expect(
      scrollWidth,
      `${name} @ ${testInfo.project.name}: content is ${scrollWidth}px wide in a ` +
      `${clientWidth}px viewport — ${scrollWidth - clientWidth}px of horizontal scroll`,
    ).toBeLessThanOrEqual(clientWidth + TOLERANCE_PX);
  });
}
