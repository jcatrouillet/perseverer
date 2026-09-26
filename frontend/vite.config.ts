import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    // Phase 7 Milestone C (ADR 0011 decision 4): installable app shell only -- precaches the
    // built JS/CSS/HTML via a generated Workbox service worker. Deliberately no `runtimeCaching`
    // entries: that would start caching live API responses, which is the "real offline data"
    // scope explicitly deferred in ADR 0011 decision 1. `registerType: "autoUpdate"` refreshes
    // the cached shell silently on the next load rather than prompting the user, since there's
    // no offline-data staleness UI here for a stale-shell prompt to hand off to.
    VitePWA({
      registerType: "autoUpdate",
      workbox: {
        // config.js is regenerated at container START from PERSEVERER_API_BASE_URL (see
        // docker/frontend-entrypoint.d/20-generate-config.sh), never baked into the build --
        // precaching it here would let the service worker keep serving whatever API base URL
        // was live the first time a PWA install happened, surviving future redeploys that
        // change PERSEVERER_API_BASE_URL (its precache revision hash is computed from the
        // committed dev-default file, so it never changes across builds and a stale cached copy
        // would never get invalidated by vite-plugin-pwa's own update mechanism). Must always
        // be fetched live.
        globIgnores: ["config.js"],
        // Paths the *server* answers, not the SPA. By default the generated service worker
        // serves the cached app shell (index.html) for every browser navigation, so in any
        // browser that had already loaded Perseverer, navigating to /authorize (the MCP OAuth
        // flow, ADR 0007 decision 10) or /oauth/login rendered the SPA's "Not found" page and
        // the request never reached the API -- confirmed live. Share pages (/share/...), raw API
        // and MCP URLs, and the OAuth endpoints must always go to the network.
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/mcp/,
          /^\/share\//,
          /^\/oauth\//,
          /^\/authorize$/,
          /^\/token$/,
          /^\/register$/,
          /^\/revoke$/,
          /^\/\.well-known\//,
        ],
      },
      manifest: {
        name: "Perseverer",
        short_name: "Perseverer",
        description: "Self-hosted fitness & health data dashboard.",
        start_url: "/",
        display: "standalone",
        background_color: "#12151c",
        theme_color: "#12151c",
        icons: [
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          {
            src: "pwa-maskable-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
    }),
  ],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
