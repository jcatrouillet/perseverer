#!/bin/sh
# Regenerates config.js from SPORTHEALTH_API_BASE_URL at container START, not image build --
# this is what makes the frontend's API base URL runtime-configurable rather than baked into
# the Vite build (docs/DEPLOY.md, docs/adr/0008-phase-5-frontend.md). Runs via nginx's own
# stock docker-entrypoint.d mechanism (nginx:1.27-alpine), so we don't override the base
# image's ENTRYPOINT/CMD at all. Leaving SPORTHEALTH_API_BASE_URL unset keeps the image's
# committed frontend/public/config.js default untouched.
set -eu

if [ -n "${SPORTHEALTH_API_BASE_URL:-}" ]; then
    config_file=/usr/share/nginx/html/config.js
    printf 'window.__SPORTHEALTH_CONFIG__ = { apiBaseUrl: "%s" };\n' "$SPORTHEALTH_API_BASE_URL" \
        > "$config_file"
    echo "20-generate-config.sh: wrote $config_file from SPORTHEALTH_API_BASE_URL"
fi
