# syntax=docker/dockerfile:1
# Built on the dev host or in CI, never on the production server, which only pulls the
# finished runtime layer.
FROM node:22-slim AS builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend ./
# workoutSyntax.test.ts imports this JSON fixture via a relative path (../../tests/fixtures/...)
# that assumes the real repo's own sibling layout (frontend/ and tests/ side by side) -- one
# fixture table drives both the Python and TS parser test suites, kept in sync by construction
# rather than duplicated (see tests/fixtures/workout_syntax_cases.json). `npm run build` runs
# `tsc --noEmit` first, which type-checks *.test.ts too (tsconfig's own "include": ["src", ...]),
# so this file must exist here even though this build never actually runs the test itself --
# confirmed the real failure mode live: omitting this COPY broke build-and-push (frontend) with
# `TS2307: Cannot find module '../../tests/fixtures/workout_syntax_cases.json'`, since this
# stage's own build context only ever copies the frontend/ subtree above, not its repo-root
# siblings. Copied to /tests/fixtures (a sibling of /build, matching /build/src/../../tests/
# fixtures' own resolved path) rather than under /build itself.
COPY tests/fixtures /tests/fixtures
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html
# Regenerates config.js from PERSEVERER_API_BASE_URL at container start, via nginx's own
# stock docker-entrypoint.d mechanism -- docs/ARCHITECTURE.md.
COPY docker/frontend-entrypoint.d/20-generate-config.sh /docker-entrypoint.d/20-generate-config.sh
RUN chmod +x /docker-entrypoint.d/20-generate-config.sh
EXPOSE 80
