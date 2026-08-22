# ADR 0008: Phase 5 — core dashboard frontend + per-athlete auth

## Status

Accepted. Backend auth paths (legacy key, per-athlete key, JWT) and the frontend's runtime
config mechanism were verified against the real dev database and a real `podman compose`
build, not just the test suite — see Consequences.

## Context

Phases 1-4 built the backend end to end (raw archive, ingestion, read API, MCP server) but the
frontend was still the bare Phase 0 scaffold — zero dependencies beyond React, no router, no
data-fetching, no charting, `App.tsx` literally said "real views arrive in Phase 5." This phase
builds the "core dashboard": a rollup-backed calendar/recap view, an activity list + detail page
with a stream chart, and inline notes — the minimum that makes the already-built API visible.

Two things `docs/DEPLOY.md` already flagged as open, unresolved TODOs (not new questions this
phase introduced): the frontend's API base URL must be runtime-configurable, never baked into
the Vite build (double-NAT/split-horizon DNS on the user's home network); and whether the UI
sits behind a trusted network boundary or is reachable more broadly. This session's explicit
answer to the second question reframed the scope of the first: **the frontend is reachable more
broadly, so it needs real per-athlete authentication, not a network boundary and not a single
shared secret typed into a browser prompt.**

## Decisions

### 1. Per-athlete credentials: a password (→ JWT) or a standing API key, either athlete-scoped

`athlete` gained four nullable columns (`username`, `password_hash`, `api_key_hash`,
`api_key_created_at`) — additive, matching CLAUDE.md's schema-evolution principle. An athlete
may have neither, either, or both credential types. Password hashing is stdlib PBKDF2-HMAC-
SHA256 (`auth/passwords.py`) — not `bcrypt`/`passlib` — deliberately: this is a single-operator,
low-QPS login endpoint, and a new dependency isn't worth it for that. API keys use a plain
SHA-256 hash (`auth/api_keys.py`), not PBKDF2: a key is already a 256-bit random token
(`secrets.token_urlsafe(32)`), not a low-entropy human password, so a fast direct hash gives the
same "never store the plaintext" property while allowing an O(1) indexed lookup
(`WHERE api_key_hash = ?`) instead of an O(n) scan with a constant-time compare per row.

### 2. JWT session tokens via `pyjwt`, already resolved as a transitive dependency

`POST /auth/login` verifies username/password and issues an HS256 JWT
(`auth/tokens.py::create_session_token`), signed with a new `PERSEVERER_JWT_SECRET` setting.
`pyjwt` was already in `uv.lock` as a transitive dependency of `mcp` (Phase 4) — promoted to an
explicit `pyproject.toml` entry since it's now directly imported. Verified against the actual
installed `pyjwt` 2.13.0 API (`jwt.encode`/`decode` signatures, `ExpiredSignatureError`
subclassing `InvalidTokenError`) by direct introspection, not memory, per CLAUDE.md's standing
rule for vendor libraries.

### 3. `require_api_key` now resolves *which* athlete authenticated, from any of three credentials

Before this phase it checked one process-wide `X-API-Key` against `settings.api_key` and
returned `None`. It now returns the resolved `athlete_id: str`, accepting: (a) `X-API-Key`
matching the legacy shared `PERSEVERER_API_KEY` → resolves to `DEFAULT_ATHLETE_ID`, so **every
existing consumer of the shared key — scripts, and the Phase 4 MCP server — keeps working with
zero changes**, confirmed by running the MCP server's `initialize` handshake against a real
`podman compose` build with the legacy key; (b) `X-API-Key` whose SHA-256 hash matches an
athlete's `api_key_hash`; (c) `Authorization: Bearer <jwt>` verified via
`verify_session_token`. Fails closed exactly as before when nothing could ever succeed (503 when
no credential is presented and neither `PERSEVERER_API_KEY` nor `PERSEVERER_JWT_SECRET` is
configured) — but a *presented* credential that simply doesn't match anything is correctly a 401
even if the legacy key happens to be unset, since per-athlete keys are now an independent valid
mechanism (this narrows the old "unset key → always 503" contract; see the updated test in
`tests/api/test_auth.py`).

Every router (`activities`, `health`, `sleep`, `calendar`, `notes`) switched from a router-level
`APIRouter(dependencies=[Depends(require_api_key)])` declaration to a per-endpoint
`athlete_id: Annotated[str, Depends(require_api_key)]` parameter, since FastAPI can't hand a
dependency's return value to a route handler through the router-level form — and every
previously-hardcoded `DEFAULT_ATHLETE_ID` query filter now uses the resolved value, so a
logged-in athlete's queries are genuinely scoped, not just gated. Verified with a real
cross-athlete isolation test: a second athlete authenticated via their own per-athlete key sees
zero of the default athlete's activities.

### 4. Account creation stays CLI-only

`sync athlete set-password` / `sync athlete create-key` — run by a human, matching the
project's single-operator, no-public-signup model (same reasoning as `sync auth login` for
Garmin credentials). No admin API endpoint, no self-service UI.

### 5. Frontend: `wouter` + `@tanstack/react-query`, no chart library

`wouter` (~1.5KB, hook-based) over `react-router`: this app needs exactly 3 flat routes plus one
`:id` param, none of `react-router` v7's data-router machinery, and Phase 0 deliberately kept
the frontend dependency-free. `@tanstack/react-query` was judged worth its weight specifically
because note-creation needs to invalidate both the calendar and the activity/day's own note
list, and the calendar's variable date range causes frequent refetches — a hand-rolled hook
would reimplement race-condition-safe aborts and manual cache invalidation across four views.
The stream chart is a small hand-rolled SVG line chart (`components/StreamChart.tsx`), not a
charting library: the stream endpoint is already server-downsampled to the `low` tier (same
choice the Phase 4 MCP tool makes, ADR 0007 decision 7), so the point count is bounded and a
plain SVG polyline is enough for one chart need.

### 6. Runtime API-base-URL config via nginx's own `docker-entrypoint.d`, not a bespoke entrypoint

`frontend/public/config.js` sets `window.__PERSEVERER_CONFIG__.apiBaseUrl`, loaded via a
`<script>` tag before the main bundle — Vite's default `publicDir` serves/copies it verbatim in
both dev and build, confirmed, no `vite.config.ts` change needed. The committed file is the dev
default (`http://localhost:8008`). The Docker image regenerates it at container **start**, not
build time, from a `PERSEVERER_API_BASE_URL` env var — via a script dropped in
`/docker-entrypoint.d/`, the base `nginx:1.27-alpine` image's own stock startup-script
mechanism, rather than overriding `ENTRYPOINT`/`CMD` (which would risk clobbering the base
image's own entrypoint chain). This is the actual "never baked into the Vite build" mechanism
`docs/DEPLOY.md` called for — verified by rebuilding the frontend container with
`PERSEVERER_API_BASE_URL` set and confirming the served `config.js` reflected it.

### 7. Auth UX: two credential paths in one gate, not just a key prompt

`components/AuthGate.tsx` wraps the whole app: a password tab (→ `POST /auth/login`, JWT stored
in `localStorage`, sent as `Authorization: Bearer`) and an API-key tab (a CLI-generated key,
sent as `X-API-Key`) — matching the explicit ask that each athlete can authenticate either way.
`api/client.ts` branches on response status rather than treating every failure alike: 401 clears
the stored credential and re-shows the gate (including reactively, via a custom
`perseverer:auth-cleared` event, when a *background* query's token expires mid-session — not
just on the login form's own submit); 503 shows a distinct "server not configured" message
instead of re-prompting, since re-prompting for a credential that can never succeed until the
server's env vars are set would loop forever.

### 8. CORS: `Authorization` added to `allow_headers`

Caught during manual browser verification, not by the test suite (no existing test exercises
CORS headers): the Phase 3 `CORSMiddleware` config only allowed `X-API-Key` and `Content-Type`
cross-origin. A JWT-authenticated request from the Vite dev origin was silently blocked by the
browser's own preflight check before ever reaching `require_api_key`. Fixed by adding
`Authorization` to `allow_headers` (`api/main.py`).

## Consequences

- The 401-vs-503 narrowing in decision 3 means a stale assumption ("unset shared key implies
  every request fails closed") is no longer true in general — future auth-adjacent code must
  reason per-credential, not per-server-config, about what "unconfigured" means.
- Verified end-to-end against real ingested Garmin data (not fixtures) through three surfaces:
  direct `curl` against a host-run `uvicorn` process, the Vite dev server in a real browser
  (login, wrong-password, expired-session re-prompt, API-key path, notes round-trip, stream
  chart against real heart-rate data), and a full `podman compose up --build` — including
  discovering that the compose stack's named volume had never been migrated independent of the
  host's bind-mounted dev data, a pre-existing gap this phase did not introduce and did not fix
  (out of scope; `docs/DEPLOY.md` has no migration step for the containerized volume at all —
  worth a future phase, not silently absorbed into this one).
- Account/credential management staying CLI-only (decision 4) means a second real athlete still
  requires shell access to the host or NAS — acceptable for now, but the natural next step if
  this project ever needs a non-operator user.
