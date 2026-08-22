# syntax=docker/dockerfile:1
# Never build on the NAS — a Vite build on a J3455 is double-digit minutes (§4b). This image
# is built on Windows or in CI; the NAS only ever pulls the finished runtime layer.
FROM node:22-slim AS builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html
# Regenerates config.js from PERSEVERER_API_BASE_URL at container start, via nginx's own
# stock docker-entrypoint.d mechanism -- see docs/adr/0008-phase-5-frontend.md.
COPY docker/frontend-entrypoint.d/20-generate-config.sh /docker-entrypoint.d/20-generate-config.sh
RUN chmod +x /docker-entrypoint.d/20-generate-config.sh
EXPOSE 80
