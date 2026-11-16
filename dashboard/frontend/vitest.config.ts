/**
 * Unit-test config, deliberately SEPARATE from vite.config.ts.
 *
 * vite.config.ts carries the PWA plugin (service-worker generation) and the
 * dev proxy. Neither has any meaning under jsdom, and VitePWA writing a
 * service worker during a test run is pure noise. So the test config takes the
 * React plugin only.
 *
 * Why there is a unit runner at all, as of 2026-11-16: the dashboard had six
 * Playwright specs and NO unit runner, so the one thing that could not be
 * asserted was component BEHAVIOUR — above all how many times a component
 * re-renders. That is exactly the defect this runner was added to pin (a
 * selector-less Zustand subscription in App re-rendering the whole tree on
 * every WebSocket push), and a screenshot cannot see it.
 */
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
