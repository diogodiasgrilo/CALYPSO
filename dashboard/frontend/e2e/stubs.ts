/** Serve REAL captured API responses so the SPA renders real layouts.
 *
 * `fixtures.json` holds actual responses from the dashboard backend, captured
 * by driving it with TestClient against a copy of variant B-A's database (118
 * daily summaries, 353 entries). Invented fixtures would not have exercised the
 * grids: the first version of this harness stubbed everything as `{}`, every
 * page rendered "No data yet", and four overflow tests passed against an empty
 * document — proving nothing.
 *
 * Nothing here touches the VM, the live backend or trading data.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";

type Fx = Record<string, { status: number; body: unknown }>;
// Read from disk rather than `import ... from "./fixtures.json"`: Node ESM
// requires an `with { type: "json" }` attribute for JSON imports, which is
// still version-sensitive tooling. readFileSync has no such dependency.
const FX: Fx = JSON.parse(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), "fixtures.json"), "utf8"),
);

/** Match a request path against the captured keys, ignoring volatile params. */
function lookup(pathname: string, search: string): unknown | undefined {
  const exact = FX[pathname + search];
  if (exact) return exact.body;
  for (const key of Object.keys(FX)) {
    const [kPath] = key.split("?");
    if (kPath === pathname) return FX[key].body;
  }
  return undefined;
}

export async function stubApi(page: Page) {
  await page.route("**/api/**", (route) => {
    const u = new URL(route.request().url());
    const body = lookup(u.pathname, u.search);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      // An endpoint we did not capture returns an empty object rather than a
      // 404, so a component renders its empty state instead of an error page.
      body: JSON.stringify(body ?? {}),
    });
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
