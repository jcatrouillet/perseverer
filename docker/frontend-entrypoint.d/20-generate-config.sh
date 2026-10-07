#!/bin/sh
# Regenerates config.js from PERSEVERER_API_BASE_URL/PERSEVERER_CARTO_API_KEY at container
# START, not image build -- this is what makes the frontend's API base URL and CARTO basemap key
# runtime-configurable rather than baked into the Vite build (docs/DEPLOY.md,
# docs/ARCHITECTURE.md). Runs via nginx's own stock docker-entrypoint.d mechanism
# (nginx:1.27-alpine), so we don't override the base image's ENTRYPOINT/CMD at all. Leaving both
# vars unset keeps the image's committed frontend/public/config.js default untouched.
set -eu

if [ -n "${PERSEVERER_API_BASE_URL:-}" ] || [ -n "${PERSEVERER_CARTO_API_KEY:-}" ]; then
    config_file=/usr/share/nginx/html/config.js
    printf 'window.__PERSEVERER_CONFIG__ = { apiBaseUrl: "%s", cartoApiKey: "%s" };\n' \
        "${PERSEVERER_API_BASE_URL:-}" "${PERSEVERER_CARTO_API_KEY:-}" > "$config_file"
    echo "20-generate-config.sh: wrote $config_file from PERSEVERER_API_BASE_URL/PERSEVERER_CARTO_API_KEY"
fi
