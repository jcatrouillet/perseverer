#!/bin/sh
# Writes config.js at container START (not image build), so the API base URL and the CARTO
# basemap key come from perseverer.env rather than being baked into the Vite build. Runs via the
# nginx image's own docker-entrypoint.d mechanism. An empty PERSEVERER_API_BASE_URL means the
# web app calls the API on its own origin (nginx proxies /api/ to the api container).
set -eu

config_file=/usr/share/nginx/html/config.js
printf 'window.__PERSEVERER_CONFIG__ = { apiBaseUrl: "%s", cartoApiKey: "%s" };\n' \
    "${PERSEVERER_API_BASE_URL:-}" "${PERSEVERER_CARTO_API_KEY:-}" > "$config_file"
echo "20-generate-config.sh: wrote $config_file"
