/** Responsive regression harness for the HYDRA dashboard.
 *
 * WHY IT EXISTS (2026-10-21): the dashboard was built desktop-first — its main
 * pages carry ZERO responsive breakpoints — and making 46 files / ~12.8k lines
 * responsive by eye is unverifiable. `@playwright/test` was already a
 * dependency with no suite behind it; this is that suite.
 *
 * It runs entirely LOCAL: `page.route()` stubs `/api/**`, including
 * `/api/auth/me`, so no backend, no VM, no TOTP login and no trading data are
 * involved. Layout is what is under test, not content.
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  reporter: [["list"]],
  timeout: 30_000,
  // ⚠️ `localhost`, not 127.0.0.1: vite binds ::1 (IPv6) only by default,
  // so an IPv4 literal gets connection-refused while the server is up.
  use: { baseURL: "http://localhost:5199", trace: "off" },
  webServer: {
    command: "npm run dev -- --port 5199 --strictPort",
    url: "http://localhost:5199",
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [
    // The three that matter. iPhone 12 is the narrowest realistic target and
    // the one the current layout is expected to fail.
    { name: "phone",   use: { ...devices["iPhone 12"] } },
    { name: "tablet",  use: { ...devices["iPad Mini"] } },
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
});
