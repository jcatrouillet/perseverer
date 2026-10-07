import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";
import { VitePWA } from "vite-plugin-pwa";

/** Reads KEY=value lines from the repository's perseverer.env (the one configuration file). */
function readPersevererEnv(): Record<string, string> {
  const file = resolve(__dirname, "..", "perseverer.env");
  if (!existsSync(file)) return {};
  const env: Record<string, string> = {};
  for (const line of readFileSync(file, "utf-8").split(/\r?\n/)) {
    const match = /^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
    if (match) env[match[1]!] = match[2]!;
  }
  return env;
}

/** Serves /config.js on the dev server from perseverer.env, the same values the production
 * frontend container writes into config.js at start (docker/frontend-entrypoint.d). */
function persevererRuntimeConfig(): Plugin {
  return {
    name: "perseverer-runtime-config",
    configureServer(server) {
      server.middlewares.use("/config.js", (_req, res) => {
        const env = { ...readPersevererEnv(), ...process.env };
        const apiBaseUrl =
          env.PERSEVERER_API_BASE_URL ||
          `http://localhost:${env.PERSEVERER_HOST_API_PORT || "8000"}`;
        const config = { apiBaseUrl, cartoApiKey: env.PERSEVERER_CARTO_API_KEY ?? "" };
        res.setHeader("Content-Type", "text/javascript");
        res.end(`window.__PERSEVERER_CONFIG__ = ${JSON.stringify(config)};\n`);
      });
    },
  };
}

export default defineConfig({
  plugins: [
    react(),
    persevererRuntimeConfig(),
    // Installable app shell only -- precaches the built JS/CSS/HTML via a generated Workbox
    // service worker. Deliberately no `runtimeCaching` entries: that would start caching live
    // API responses, which is the "real offline data" scope deliberately not built (see
    // docs/ARCHITECTURE.md). `registerType: "autoUpdate"` refreshes the cached shell silently
    // on the next load rather than prompting the user, since there's no offline-data staleness
    // UI here for a stale-shell prompt to hand off to.
    VitePWA({
      registerType: "autoUpdate",
      workbox: {
        // config.js is written at container START from perseverer.env (see
        // docker/frontend-entrypoint.d/20-generate-config.sh), never baked into the build --
        // precaching it would let the service worker keep serving the values that were live the
        // first time the app was installed, surviving later configuration changes. Must always
        // be fetched live.
        globIgnores: ["config.js"],
        // Paths the *server* answers, not the SPA. By default the generated service worker
        // serves the cached app shell (index.html) for every browser navigation, so in any
        // browser that had already loaded Perseverer, navigating to /authorize (the MCP OAuth
        // flow, docs/ARCHITECTURE.md) or /oauth/login rendered the SPA's "Not found" page and
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
