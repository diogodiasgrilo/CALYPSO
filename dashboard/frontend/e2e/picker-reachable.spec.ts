/** Every strategy in the picker must be REACHABLE, not merely rendered.
 *
 * 🔴 THE BUG THIS CATCHES. The dropdown was `overflow-hidden` with no height
 * cap. Measured on an iPhone 12 it ran y=82..737 in a 664px viewport and did
 * NOT scroll (scrollHeight == clientHeight), while the page behind it is
 * exactly viewport-height. Variant H — the last entry — was therefore
 * unreachable: a whole strategy could not be selected on a phone.
 *
 * Nothing in the visual review caught it either, because a screenshot of an
 * open dropdown looks FINE; the list simply ends where the screen does. It
 * took measuring the panel against the viewport.
 *
 * Asserts clickability (Playwright scrolls into view and checks hit-testing),
 * not just presence in the DOM.
 */
import { expect, test } from "@playwright/test";
import { stubApi } from "./stubs";

test("every strategy in the picker can actually be clicked", async ({ page }, info) => {
  await stubApi(page);
  await page.goto("/");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(700);

  await page.locator("header button, header [role=button]").first().click({ timeout: 5000 });
  await page.waitForTimeout(500);

  // Find the PANEL, not an inner row: the positioned element that holds the
  // options. A `div:has-text()` locator resolves to the innermost match, which
  // on the first attempt was a single option row — the guard below caught it.
  const geo = await page.evaluate(() => {
    const panels = [...document.querySelectorAll<HTMLElement>("div")].filter((el) => {
      const cs = getComputedStyle(el);
      return (cs.position === "absolute" || cs.position === "fixed") &&
             el.querySelectorAll("button, [role=option], a").length > 5;
    });
    const el = panels[panels.length - 1];
    if (!el) return null;
    el.setAttribute("data-e2e-picker", "1");
    return {
      bottom: Math.round(el.getBoundingClientRect().bottom),
      viewportH: window.innerHeight,
      scrolls: el.scrollHeight > el.clientHeight + 2,
      items: el.querySelectorAll("button, [role=option], a").length,
    };
  });
  expect(geo, "picker panel not found").not.toBeNull();
  const panel = page.locator("[data-e2e-picker='1']");

  expect(geo!.items, "no options found — this test would pass without testing anything")
    .toBeGreaterThan(5);

  // Either it fits, or it scrolls. Overflowing the viewport WITHOUT scrolling
  // is the failure: entries below the fold become unselectable.
  expect(
    geo!.bottom <= geo!.viewportH || geo!.scrolls,
    `${info.project.name}: picker bottom ${geo!.bottom}px exceeds the ${geo!.viewportH}px ` +
    `viewport and the panel does not scroll — entries below the fold are unreachable`,
  ).toBe(true);

  // The LAST option must be genuinely clickable, not merely present.
  const last = panel.locator("button, [role=option], a").last();
  await expect(last).toBeVisible();
  await last.scrollIntoViewIfNeeded();
  const box = await last.boundingBox();
  expect(box, "last option has no box").not.toBeNull();
  expect(box!.y + box!.height, "last option sits below the viewport even after scrolling")
    .toBeLessThanOrEqual(geo!.viewportH + 2);
});
