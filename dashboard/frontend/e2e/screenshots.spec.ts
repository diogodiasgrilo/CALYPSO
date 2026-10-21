/** Manual visual-review tool: renders every page at every viewport to /tmp/shots.
 *
 * WHY IT IS KEPT. The automated assertions in this directory measure things —
 * overflow, tap-target size — and measuring is not seeing. The x-axis labels on
 * Analytics collided into an unreadable run of digits ("07-3008-1108-2109-...")
 * and NOT ONE of those assertions noticed, because a collision is neither an
 * overflow nor a small target. It was found by looking at a screenshot.
 *
 * Skipped unless SHOTS=1, so it never runs in CI (it asserts nothing):
 *     SHOTS=1 npx playwright test e2e/_shots.spec.ts
 */
import { test } from "@playwright/test";
import { stubApi } from "./stubs";

test.skip(!process.env.SHOTS, "visual review tool — run with SHOTS=1");

const ROUTES = [
  ["dashboard", "/"],
  ["history", "/history"],
  ["analytics", "/analytics"],
  ["long-strangle", "/long-strangle"],
  ["comparison-ic", "/comparison/ic_0dte"],
  ["comparison-cal", "/comparison/calendar_multiday"],
] as const;

for (const [name, path] of ROUTES) {
  test(`shot ${name}`, async ({ page }, info) => {
    await stubApi(page);
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(900);
    await page.screenshot({
      path: `/tmp/shots/${info.project.name}__${name}.png`,
      fullPage: true,
    });
  });
}
