import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      // hydra-logo-192.png, NOT the 960px master: `includeAssets` is a
      // PRECACHE list, so leaving the 571,778-byte original here would
      // have kept downloading it on every service-worker update even
      // after no <img> referenced it any more.
      //
      // hydra-logo-120.png is deliberately NOT here: the iOS Scriptable
      // widget fetches it directly (dashboard/scriptable/HYDRA_Widget.js),
      // so it must keep being SERVED, but the web app never shows it and
      // has no reason to precache it.
      includeAssets: ["favicon.ico", "hydra-logo-192.png"],
      manifest: {
        name: "CALYPSO — HYDRA Dashboard",
        short_name: "CALYPSO",
        description: "Real-time monitoring dashboard for HYDRA trading bot",
        theme_color: "#37424f",
        background_color: "#2d353f",
        display: "standalone",
        orientation: "any",
        icons: [
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
        ],
      },
      workbox: {
        runtimeCaching: [
          {
            urlPattern: /\/api\/.*/i,
            handler: "NetworkFirst",
            options: {
              cacheName: "api-cache",
              expiration: { maxEntries: 50, maxAgeSeconds: 300 },
              // ── THE DEADLINE THAT WAS MISSING ───────────────────────────
              // NetworkFirst with no `networkTimeoutSeconds` waits on the
              // network INDEFINITELY before it will consider the cache. On a
              // stalling mobile connection that is an unbounded hang with a
              // perfectly good cached response sitting right there — which is
              // the "it just sits on loading" report this was traced from, on
              // a session where every request that arrived was answered 200 in
              // about 3ms.
              //
              // 5s is far above the measured server latency (single-digit ms),
              // so this only fires on a genuinely stalled link, never on a
              // merely slow one.
              //
              // Serving a cached response is honest here for two reasons: the
              // `expiration` above refuses anything older than 300s, and the
              // live feed is the WebSocket, whose connection state is already
              // rendered in the header — so a reader can see the feed is down
              // rather than being told stale numbers are current.
              networkTimeoutSeconds: 5,
            },
          },
        ],
      },
    }),
  ],
  build: {
    rollupOptions: {
      output: {
        // ── VENDOR CHUNKING ──────────────────────────────────────────────
        // Route splitting alone could not help the LANDING view, because the
        // default dashboard genuinely renders both chart libraries (SPXChart
        // uses lightweight-charts, PnLCurve uses recharts). Splitting the
        // vendors out does two things that still matter:
        //
        //  1. They download in PARALLEL with the app chunk rather than being
        //     one serial 1MB parse.
        //  2. They get their OWN content hash, so a change to our code no
        //     longer invalidates ~600 kB of unchanged third-party JS in every
        //     visitor's cache (and in the service worker's precache). Before
        //     this, every deploy re-downloaded the whole bundle.
        //
        // Only node_modules is split. Splitting our own modules by hand is how
        // circular-initialisation bugs get introduced; Rollup's own analysis of
        // the dynamic imports above is better at that than a hand-written rule.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          // d3-* are recharts' own dependencies — keeping them together avoids
          // a chunk that is useless without the other.
          if (id.includes("recharts") || id.includes("/d3-") || id.includes("victory-vendor")) {
            return "vendor-recharts";
          }
          if (id.includes("lightweight-charts") || id.includes("fancy-canvas")) {
            return "vendor-lightweight-charts";
          }
          if (id.includes("react-router") || id.includes("@remix-run")) return "vendor-router";
          if (id.includes("lucide-react")) return "vendor-icons";
          if (
            id.includes("/react-dom/") ||
            id.includes("/react/") ||
            id.includes("/scheduler/")
          ) {
            return "vendor-react";
          }
          return "vendor";
        },
      },
    },
  },

  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8001", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8001", ws: true },
    },
  },
});
