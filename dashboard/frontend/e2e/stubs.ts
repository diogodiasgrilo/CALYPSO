/** Stub every API route so the SPA renders with no backend and no auth.
 *
 * `/api/auth/me` is the gate: LoginGate renders the dashboard only once it
 * returns 200. Everything else returns an empty-but-valid shape — enough for
 * components to mount and lay out, which is all a LAYOUT test needs.
 */
import type { Page } from "@playwright/test";

export async function stubApi(page: Page) {
  await page.route("**/api/**", (r) => {
    const url = r.request().url();
    const body = (() => {
      if (url.includes("/strategies/meta")) {
        return { strategies: [], groups: [] };
      }
      if (url.includes("/health")) return { status: "ok" };
      // A permissive empty shape: arrays for lists, nulls elsewhere. Components
      // that need more will render their own empty state, which is itself a
      // layout worth testing.
      return {};
    })();
    return r.fulfill({ status: 200, contentType: "application/json",
                       body: JSON.stringify(body) });
  });

  // ⚠️ REGISTERED AFTER the catch-all ON PURPOSE. Playwright matches routes
  // LAST-REGISTERED-FIRST, so a generic "**/api/**" declared later shadows a
  // specific one declared earlier. That exact mistake made the first version of
  // this harness vacuous: /api/auth/me returned {}, `getCurrentUser()` read no
  // `username`, the LOGIN SCREEN rendered instead of the dashboard, and four
  // overflow tests passed against 39 elements with zero grids and zero tables.
  await page.route("**/api/auth/me", (r) =>
    r.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify({ username: "e2e", authenticated: true }) }));

  // The dashboard opens a websocket; let it fail fast rather than hang.
  await page.route("**/ws/**", (r) => r.abort());
}
