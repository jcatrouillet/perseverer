# AGENTS.md

**Perseverer** — a self-hosted fitness & health data platform. Garmin + Strava in, one owned SQLite+Parquet
archive, a REST/JSON API an AI agent can write notes through, a fast web frontend. Runs on
`bercy`, an Intel NUC6i55SYH (i5-6260U, AVX2, 32GB RAM) running Ubuntu Server 26.04 LTS, behind
an existing reverse proxy; developed on Windows + Podman Desktop. Production moved off an
original Synology DS1019+ target — see `docs/DEPLOY.md`'s history note and decision 8 of
`docs/adr/0001-phase-0-foundations.md` for the now-superseded NAS-era rationale (Docker via DSM
Container Manager vs. Podman in dev) that no longer applies now that both dev and prod run
Podman. Production is deployed as systemd Quadlet units (`quadlet/`), not Compose — see
`docs/DEPLOY.md`.

**Current phase: 9 — backup + restore automation and hardening (dependency-vulnerability
scanning in CI, login brute-force lockout, container hardening for `api`/`worker`) are shipped;
see `docs/adr/0014-phase-9-backup-hardening.md`. This phase's third original thread, an LLM
narrative layer over the rules-based `insight` table, is deliberately deferred (not built) —
see that ADR's decision 1 for the reference design and why. Phase 8 (strava_export importer,
merge visibility/split, rules-based insight engine — see
`docs/adr/0012-phase-8-strava-merge-insights.md`), Phase 7 (map explorer, recaps, PWA/offline
shell — see `docs/adr/0011-phase-7-map-recaps-pwa.md`; image export was scoped in but dropped
after verification showed the chosen library hangs on this app's Recharts-heavy pages, and PWA
service-worker registration itself still needs verification on a real device, not just the
build/manifest checks done so far). Phase 6.1 (frontend design overhaul — theming, Recharts,
colour/icon system, calendar date-navigator, rich activity cards, multi-panel activity detail,
day view, bounded activity-context enrichment), Phase 6 (calendar grid, weekly/monthly rollups,
Fitness & Form, health dashboard), Phase 5 (core dashboard frontend + per-athlete auth), Phase 4
(MCP server exposing the read API + notes), Phase 3 (read API, precomputed rollups, DuckDB,
notes write path), and Phase 2 (garmin_export/garmin_connect adapters, scheduler, staleness,
health/wellness ingestion, real-export nested-zip discovery) are complete.**
See the phase table in the project brief (kept outside this repo) for the full 10-phase plan.
Do not skip ahead — each phase has its own ADR in `docs/adr/` and its own acceptance criterion.

## The one rule that overrides everything else

**Raw first, always.** Every byte fetched from any vendor is archived verbatim — original
FIT/TCX/GPX bytes, and the raw JSON of every HTTP response with request URL, timestamp,
status, and SHA-256 — *before* it is parsed. Parsing is a pure function over that archive. If
we discover a field we ignored today, we must be able to re-derive the entire database from
the archive **without re-contacting any vendor**. And: **never drop an unknown field.**
Unmapped FIT fields and unmapped JSON keys get auto-registered into `metric_definition`, not
discarded. If you're writing an adapter or a parser and you're about to throw away a field
because you don't recognize it — stop, that's the bug.

## Non-negotiable principles (§3 of the project brief)

1. Raw first, always — see above.
2. Never drop an unknown field — see above.
3. **Idempotent, re-runnable ingestion.** Running any sync twice is a no-op. Content hashing
   plus upserts, never blind inserts.
4. **Provenance per field, not per record.** Every stored value knows its source and when.
5. **Never destructive.** No migration or sync deletes raw data. Soft-delete only.
6. **SI units in storage** (metres, seconds, m/s, kg, °C, W, bpm), converted at the
   presentation layer. Timestamps: UTC + local UTC offset + IANA timezone name.
   **Implementation note**: every `DateTime` column is a *naive* Python datetime that's
   implicitly UTC — SQLite/SQLAlchemy don't actually round-trip `tzinfo`, so declaring
   `timezone=True` would be a lie (see `docs/adr/0002-phase-1-schema-and-ingestion.md`
   decision 10, and `docs/DATA_DICTIONARY.md`). Never assume a value read from the DB is
   timezone-aware.
7. **Additive schema evolution.** A new activity type or health metric needs zero migrations
   and zero code changes — only a registry row.

## Standing operational requirements

Apply these to every change, including work performed only in the local development
environment:

- Run every available automated check before delivering a feature: the complete Python test
  suite, whole-project Ruff and mypy checks, the complete frontend test suite, the production
  frontend build, formatting checks, package build, dependency audit, and migration validation.
  Do not call a feature ready when a relevant check has not completed successfully. Report any
  environmental limitation explicitly.
- For UI work, follow Perseverer's existing graphic charter and reusable styles. Verify the
  finished result in the local browser at desktop and narrow responsive widths. Do not claim a
  control is visible or working without inspecting it in the rendered page.
- Keep feature documentation current. When an API changes, update `docs/API.md` and the rendered
  API guide used by the frontend.
- Develop and verify locally first. Deploy to production only when the project owner asks for it.
- Preserve unrelated work in a dirty working tree.

## Architecture

- **Bronze/raw archive** (`data/raw/`, table `raw_object`): every vendor HTTP response and
  every FIT/TCX/GPX file, gzipped, content-addressed at `<sha256[:2]>/<sha256>.gz`, immutable.
  Each blob has a JSON sidecar (`<sha256>.json`) mirroring its `raw_object` row — the sidecar,
  not the SQLite row, is the durable catalog; `sync rebuild` restores `raw_object` from
  sidecars first, which is what makes rebuilding after deleting the database entirely work.
- **SQLite (WAL mode)**: metadata, daily/summary health observations, activity summaries,
  laps/splits, rollups, notes. What you filter and join on. See `docs/DATA_DICTIONARY.md` for
  the full table list.
- **Parquet, one file per activity** (`data/parquet/<athlete_id>/<activity_id>.parquet`):
  full-resolution per-second activity streams. What you plot. Never rows-in-SQLite for this.
  Downsampling into `low`/`medium`/`high` tiers happens at serving time
  (`stream_query.py::downsample`, one DuckDB bucket-averaging SQL statement against the
  Parquet file — never ingestion-time, never a Python loop).
- **DuckDB**, attached read-only over the SQLite database (`api/duckdb_conn.py`), for the one
  query that actually benefits from it: `GET /activities/{id}/stream`. Everything else in the
  read API (activity list/detail, health, sleep, the rollup-backed calendar) stays on plain
  SQLAlchemy — DuckDB isn't a general query-builder abstraction here. See
  `docs/adr/0006-phase-3-read-api-and-rollups.md`.
- **MCP server** (`api/mcp_server.py`), mounted at `/mcp` inside the same `api`
  container/process, not a separate service — a deliberate, informed deviation from an early
  Phase-0 guess, made once Streamable HTTP's actual shape (a plain mountable ASGI app) was
  known. Originally eight tools, one per REST endpoint above plus `create_note`/`list_notes`;
  each tool calls its REST endpoint in-process via `httpx.ASGITransport`, reusing the REST
  layer's logic rather than a second implementation. Gated by the same `X-API-Key` via a raw
  ASGI wrapper (`Mount` bypasses FastAPI's own `Depends`). `mcp>=1.9,<2` — 2.x just went stable
  and isn't adopted yet. See `docs/adr/0007-phase-4-mcp-server.md`.
  **Revision: expanded from 8 to 41 tools** — reported directly as a real gap: the tool surface
  had stayed frozen at its original Phase-4 scope while the REST API grew to ~100 endpoints
  across a dozen features (Fitness & Form, Performance/VO2max/pace-HR-zones/race-readiness/
  performance-curve, Insights, Health dashboard/stream, Gear, Blood tests, Goals, Planned
  workouts/races, Weather forecast), none of it reachable through MCP. Every new tool is a
  thin `_call_api` proxy exactly like the original eight — no new logic, just closing the gap
  between what the REST API can answer and what an agent could actually ask it. Scope is
  deliberately bounded to **read-only informational tools, plus completing the Notes CRUD**
  (`update_note`/`delete_note`, alongside the pre-existing `create_note`/`list_notes` — the one
  concrete gap in this project's own "an AI agent can write notes through" mission statement,
  since only half the CRUD existed). That first pass deliberately withheld every other write.
  **Revision: write access added (41 → 65 tools), on the owner's explicit request** — "I want
  agents to be able to create a training plan." Now exposed: planned workouts (`create_/update_/
  delete_planned_workout`, `complete_/uncomplete_planned_workout`, `push_planned_workout`,
  `create_recurring_planned_workout`), planned races (create/update/delete), distance goals
  (`set_goal`/`delete_goal`), blood tests (single/panel create, update, delete), gear
  (`create_shoe`, `set_default_shoe`, `retire_shoe`, `set_activity_shoe`), and per-activity
  corrections (`set_activity_sport`/`_race`/`_name`/`_fueling`). The one place this app writes
  to a third-party account is now reachable by an agent, so know the mechanics: creating/
  updating a workout only saves a **draft** (`push_status="draft"`); the existing daily job
  pushes anything due within `PERSEVERER_PLANNED_WORKOUT_PUSH_WINDOW_DAYS` (default 7) to the
  athlete's Garmin automatically, and `push_planned_workout` pushes one immediately. The
  `create_planned_workout`/`create_recurring_planned_workout` tool *descriptions* embed the full
  running text-syntax grammar (`_WORKOUT_SYNTAX_HELP`), the yoga/bouldering and hiit/
  strength_training shapes, and the blank-line-ends-a-repeat-block rule, since an agent has no
  other way to learn them — registered via `@mcp.tool(description=...)`, not by mutating
  `__doc__` after decoration (FastMCP snapshots the docstring at decoration time). A malformed
  line does not reject the workout: it comes back in `parse_errors`, which the agent must check.
  The syntax help's own example is verified to parse cleanly (a test caught that an earlier draft
  had intensity *after* the duration; the grammar is `[intensity] duration ...`, e.g. `recovery
  90s`). Still deliberately excluded: every `settings/*` endpoint (credentials, API keys,
  password, rebuild, bulk import), `auth/login`, `share.py`, `calendar_feed.py`, and
  trim/merge/split/climb-route edits (visual-review workflows a human should confirm).
  **Revision: OAuth 2.1 on `/mcp`, so claude.ai's custom connector can authenticate** — that
  dialog takes a URL (plus optional OAuth client credentials) and cannot send the `X-API-Key`
  header, so the header-only gate made a URL-only connector 401 on every call. The MCP SDK's own
  authorization-server support serves `/.well-known/oauth-authorization-server`,
  `/.well-known/oauth-protected-resource/mcp`, `/authorize`, `/token`, `/register` (RFC 7591
  dynamic client registration) and `/revoke`, with PKCE verified by the SDK. `auth/oauth.py`
  (`PersevererOAuthProvider`) supplies storage: `oauth_client` (the registered application — the
  one new table exempt from `athlete_id` scoping, since it exists before anyone logs in, like
  `metric_definition`), `oauth_authorization_code` (single-use, deleted on exchange) and
  `oauth_token` (access 1h / refresh 30d, rotated on every refresh, revoked as a pair via
  `grant_id`); codes and tokens are stored only as SHA-256 hashes, and everything is in SQLite
  rather than memory because `api` runs two uvicorn workers. The consent step is this app's own
  page, `GET/POST /oauth/login` (`api/routers/oauth.py`): `/authorize` redirects there carrying a
  short-lived signed JWT of the pending request (no table needed), and a correct Perseverer
  username/password — checked by `auth/credentials.py::authenticate_athlete`, the very same
  lockout-protected, constant-time check `/auth/login` now also uses, extracted so there is one
  implementation to audit — mints the code and redirects to the client's registered
  `redirect_uri`. **Only the default athlete may authorize**: the MCP tools call the REST layer
  with the shared API key, i.e. as `DEFAULT_ATHLETE_ID`, so letting a second athlete log in would
  show them the first athlete's data (the login page refuses with a 403 rather than mislead).
  The header path is preserved, not replaced: the ASGI wrapper rewrites a valid `X-API-Key`
  into `Authorization: Bearer <key>` and `load_access_token` accepts the raw API key as a token,
  so Claude Code's existing registration works unchanged; a wrong key is still a 401. The wrapper
  lets exactly the OAuth paths through to the SDK and keeps every other non-`/mcp` path a plain
  404 (the incident documented in `test_mcp_mount_fallthrough.py`). Deployment needs
  `PERSEVERER_PUBLIC_BASE_URL` (the issuer/resource identifier in the discovery documents) and
  `PERSEVERER_JWT_SECRET` set, and `docker/nginx.conf` forwards those paths to the API. SDK
  quirk worth knowing: `/revoke`'s request model requires a `client_secret` field even for a
  public client, so such a client must send it empty.
  **A real deploy-time bug this surfaced**: the PWA service worker (`vite.config.ts`, workbox's
  default `navigateFallback`) answers *every* browser navigation with the cached SPA shell, so in
  any browser that had loaded Perseverer before, opening `/authorize` rendered the SPA's "Not
  found" page and never reached the API (curl and a fresh browser worked, which is why tests and
  server logs looked fine). `navigateFallbackDenylist` now excludes `/api/`, `/mcp`, `/share/`,
  `/oauth/`, `/authorize`, `/token`, `/register`, `/revoke` and `/.well-known/` — the same
  denylist also fixes any share link opened in a browser with the app installed. A browser with
  the old worker needs one normal visit to the app (which installs the new worker) before the
  connector's Connect step will work.
  **Revision: `get_activity_stream` can go past the fixed `low` tier** — originally hardcoded to
  `low` (~200 points) regardless of what the caller asked for, on the theory that bulk per-second
  data doesn't belong in an agent's context window (ADR 0007 decision 7). Reported directly
  against a real use case that theory didn't cover: checking a specific few-minute stretch of an
  activity for something a coarse 200-point-per-whole-activity shape genuinely can't show. The
  tool now takes `tier`/`channels`/`start_s`/`end_s`, all passed straight through to
  `/activities/{id}/stream`'s own params (still defaulting to `tier="low"`, so a caller asking
  for nothing extra sees no change) — the REST endpoint itself gained `start_s`/`end_s` in the
  same change (elapsed seconds from the activity's own start, intersected with an active trim
  rather than ever escaping it). `stream_query.py::downsample` had to change alongside this: its
  bucket-width math previously sized buckets from the *whole activity's* `duration_s` even when a
  narrower `window` was given (the pre-existing trim-serving mechanism this reuses), which would
  have quietly defeated the whole point — a `tier="high"` request narrowed to a 3-minute window
  of a 2-hour activity would otherwise still get ~36-second buckets sized for the full 2 hours,
  not the true near-1-second resolution the narrower window asks for. Bucket width is now sized
  from the *window's own* span when one is given (falling back to `duration_s - window_start`
  for an open-ended window, e.g. an untrimmed trim end).
- **Per-athlete auth (Phase 5)**: `require_api_key` (`api/dependencies.py`) resolves — not just
  gates — the authenticated `athlete_id` from any of three credentials: the legacy shared
  `PERSEVERER_API_KEY` (→ `DEFAULT_ATHLETE_ID`, so the MCP server and existing scripts keep
  working unchanged), a per-athlete `X-API-Key` (SHA-256-hashed, `athlete.api_key_hash`), or an
  `Authorization: Bearer` JWT issued by `POST /auth/login` (password checked via stdlib
  PBKDF2, `auth/passwords.py`; signed with `PERSEVERER_JWT_SECRET`, `auth/tokens.py`). Every
  router query is scoped to the resolved athlete, not a hardcoded default. `sync athlete create
  --display-name ...` creates the row itself (the one athlete-provisioning step no other command
  does — `set-password`/`create-key` only `UPDATE` a row that already exists); credentials are
  then provisioned via `sync athlete set-password`/`create-key` — CLI-only, no self-service
  signup. Once logged in, an athlete CAN change their own password thereafter via `PUT
  /settings/password` (Settings page) — verifies the current password first through the exact
  same `is_locked_out`/`verify_password`/`record_attempt` sequence `POST /auth/login` uses, then
  writes only `password_hash` (never `username`). See `docs/adr/0008-phase-5-frontend.md` and
  docs/DEPLOY.md's "Provisioning a second athlete" for the full second-athlete sequence.
- **Frontend** (`frontend/src/`): Vite + React 19 + TS-strict, `wouter` for routing,
  `@tanstack/react-query` for data fetching, a hand-rolled SVG chart (no charting library) for
  the one stream-chart need. The API base URL is runtime-configured
  (`frontend/public/config.js`, regenerated at container start from
  `PERSEVERER_API_BASE_URL` via nginx's own `docker-entrypoint.d` mechanism) — never baked
  into the Vite build, since the reverse-proxy hostname doesn't resolve the same way inside vs.
  outside the house (double-NAT/split-horizon DNS, see `docs/DEPLOY.md`). `AuthGate` offers
  either credential path (password or a pasted API key); a background 401 clears the stored
  credential and re-prompts automatically. Every map surface (`ActivityMap` thumbnail,
  `ActivityRouteMap` detail view, `MapExplorerPage`) renders CARTO's Positron **vector** basemap
  (`CartoBasemapLayer.tsx`, `@maplibre/maplibre-gl-leaflet` mounting a MapLibre GL layer inside
  the existing react-leaflet `MapContainer` — markers/polylines/popups all stay on Leaflet's own
  renderer, only the basemap layer is MapLibre) — a revision of ADR 0011 decision 1's original
  raster-tiles/OSM choice, made once a real CARTO API key was available. The key
  (`window.__PERSEVERER_CONFIG__.cartoApiKey`, `mapBasemap.ts`) follows the exact same
  runtime-config mechanism as `apiBaseUrl` above (`PERSEVERER_CARTO_API_KEY` env var), even
  though it isn't actually secret — CARTO's basemap product is designed for client-side
  embedding, like a Mapbox public token. `mapTiles.ts`'s canvas rasterizer for GIF export
  deliberately stays on CARTO's *raster* tiles (just now carrying the same key) — capturing a
  vector/WebGL basemap into a still frame needs an actual MapLibre render pass read back via
  `getCanvas()`, a materially different problem left for if it's ever actually needed.
- **Activity list, with a per-day wellness strip (`ActivityListPage.tsx`, Phase 6.1 Milestone B)**:
  activities grouped by `local_date`, each day getting a slim strip (sleep, resting HR, steps)
  above that day's `ActivityCard`s — reuses `GET /health/dashboard`/`GET /sleep` (already fetched
  for the Health page), no new endpoint. **Revision: the sport filter is a `<select>` over
  `metricStyle.ts::KNOWN_SPORTS`, not free text** — the original filter took a raw string, giving
  no clue which value actually matches (`GET /activities?sport=` is an exact match, so a typo or
  the wrong casing silently returns nothing rather than erroring). `KNOWN_SPORTS` is the same
  fixed 14-sport catalog `ActivitySportCorrection.tsx`'s own picker already uses, not sports
  derived from the athlete's real activity history the way `MapExplorerPage.tsx`'s sport dropdown
  is — that page already holds its *entire* unpaginated activity history in memory for its own
  map-rendering needs, but this page's list is server-paginated (`useActivities`'s own
  `limit`/`offset`), and `useAllActivities`'s own docstring already warns against paging through
  the whole archive just to read one field off each row (a real, previously-hit performance
  problem, not a hypothetical one) — a closed, fixed catalog is the honest option here, not a
  hardcoded stand-in for real data that's simply too expensive to fetch for this purpose.
- **Weekly/monthly rollups + Fitness & Form (Phase 6)**: `period_rollup`/
  `health_metric_period_rollup` are a rollup OF `day_rollup`/`health_metric_daily_rollup`
  (sum-of-sums, weighted averages), not of raw tables — `period_type` (`"week"`|`"month"`)
  discriminated rather than four separate tables. Week starts Monday. `fitness_daily_rollup`
  stores an independently-computed Coggan/Banister CTL(42d)/ATL(7d)/TSB EWMA over daily
  `fit.session.training_load_peak` — Garmin's own exports have no CTL/ATL/TSB at all, so the
  frontend shows this alongside (not reconciled against) Garmin's own Training Readiness/Status.
  Full history recompute on every relevant ingest run (`fitness.py::refresh_fitness_rollup`),
  not incremental — cheap even at ~1,400+ days, and a retroactive correction invalidates
  everything forward from it regardless of scheme. `GET /health/dashboard` merges the same
  logical field's several raw `metric_key` namespaces (`api/routers/health.py::
  LOGICAL_METRICS`, verified field-by-field against the real database, not assumed from parser
  docstrings). See `docs/adr/0009-phase-6-calendar-rollups-fitness-health.md`.
- **Fitness & Form / Health tabs (`FitnessPage.tsx`/`HealthPage.tsx`)**: both pages are entirely
  metric-over-time trend charts, one metric selected at a time — not every chart stacked at
  once (an earlier version of this redesign did stack them; the user changed their mind once it
  shipped, since scrolling past a dozen charts to reach one was worse than the old flat list it
  replaced). `MetricExplorer.tsx` is the shared list+detail shell (a left-hand metric list, the
  selected one's chart on the right, falling back to the first available metric if the current
  selection has no data); each page builds its own `ExplorerMetric[]` filtered by
  `hasAnyRawValue` against the *unwindowed* daily series, so the list only ever offers a metric
  that actually has some data somewhere in history, not just in the current window. `TrendChart.tsx`
  (a generic Recharts `ComposedChart` over any number of named series, one or two Y axes,
  optional area fill) renders the active metric's chart; one navigator, `TrendControls.tsx`
  (Week/Month/Year/All-time resolution + prev/next), is hosted once via `MetricExplorer`'s
  `detailHeader` slot and drives whichever chart is currently selected. Switching resolution
  (Week → Month → Year) keeps the same `anchor` date rather than resetting it to today — a week
  in August 2025 switches to "August 2025", not the current month — since `computeWindow` only
  ever needs *a* date inside the target window to re-derive that window's own start/end, and the
  anchor already names one (the app's own state, not a value TrendControls hands back). The
  prev/next arrows themselves also stay at a fixed screen position across every click:
  `.trend-controls__label` (`layout.css`) is given a fixed `min-width` and centered text so the
  label's own varying width (`"May 2026"` vs `"September 2026"`, or a week's date-range label,
  the widest of any resolution) can't shift the whole nav block, which sits flush right via the
  parent's `justify-content: space-between`. `trendWindow.ts` computes the
  window (`computeWindow`) and buckets an already-fetched daily series into it
  (`bucketSeriesToWindow`): week buckets by day (one point per day, i.e. no averaging needed),
  month by ISO week, year and all-time by calendar month — averaging whichever real observations
  fall in each bucket, `null` (not zero, not omitted) where none do. Deliberately client-side:
  neither `fitness_daily_rollup` nor `health_metric_daily_rollup` has a week/month/year
  pre-aggregation (only `period_rollup`'s own week/month grain exists, and that's activity
  totals, not fitness/health), and a daily series over this app's real history (~1,400+ days) is
  cheap enough to fetch once (`EARLIEST_PLAUSIBLE_DATE` to today, one `GET /fitness` +
  one `GET /health/dashboard` call per page) and slice/average client-side on every navigation
  click with zero further network round-trips. Fitness & Form charts VO2max, HRV, Lactate
  threshold (pace + heart rate, dual-axis — see the `garmin_connect` bullet below for where this
  data comes from), and the combined Fitness (CTL)/Fatigue (ATL)/Form (TSB) triad (also
  dual-axis, TSB on its own axis with a zero reference line, same shape the old single-purpose
  `FitnessChart.tsx` used). Health charts all fifteen `LOGICAL_METRICS` this project surfaces
  for an athlete's own body/vitals — weight, BMI, SpO2, steps, and the ten Eufy body-composition
  fields each on their own chart, plus three combined two-line charts following the same pattern
  as CTL/ATL (Heart rate: max+resting; Respiration: waking+sleep; Blood pressure: systolic+
  diastolic, the latter two newly promoted to `LOGICAL_METRICS` — blood pressure had no logical
  metric before this, `apple_health.blood_pressure_{systolic,diastolic}` being its only source)
  — plus Sleep time, read from `GET /sleep` (`sleep_session`, not `health_observation`) since
  that's a dedicated table, not part of the EAV metrics pipeline. `FitnessChart.tsx`/`HealthTrendChart.tsx`/
  `HealthMetricTiles.tsx`/`SleepDurationChart.tsx` are untouched and still power the Fitness &
  Form/Health cards embedded in the calendar's own Month/Year/All-time views (a different,
  calendar-period-scoped use case this redesign didn't touch) — `CORE_METRICS`/`HRV_METRIC`/
  `WEIGHT_METRIC` stay exported from `HealthPage.tsx` unchanged since those views import them.
  `earliestDateForKeys` (`trendWindow.ts`) scopes "All time" (and `canGoPrevious`) to the
  *selected* metric's own keys, not a merge across every metric the page fetches — both pages
  fetch one combined series per data source (all of `LOGICAL_METRICS` in one `GET
  /health/dashboard` call, say), so an unscoped `earliestDate` over that whole merge meant
  every metric's "All time" silently started wherever the single oldest metric on the page
  began (Sleep's own 2022-onward data showing an x-axis stretching back to a 2016 weight
  reading it has nothing to do with) — a real bug, not a hypothetical one. Each page resolves
  its own currently-active metric (mirroring `MetricExplorer`'s own selected-or-first-available
  fallback, since the page needs that resolved key *before* handing `MetricExplorer` a window
  to bucket against) and computes `dataStart` from only that metric's keys. Heart rate's Max +
  Resting pairing is the one `series()` call in `HealthPage.tsx` that takes an explicit colour
  override rather than deriving it from `metricStyle.ts` — both are legitimately the same `"hr"`
  tone everywhere else in the app (that's metricStyle.ts's whole point, one hue per metric), but
  reusing it for both lines on this one *combined* chart made them indistinguishable, so Resting
  gets `toneColor("pace")` instead, scoped to just this chart.
- **Blood test results (`blood_test_result`, `blood_tests.py` API schemas/router,
  `BloodTestsPanel.tsx`)**: athlete-entered lab results, one row per marker per draw, several
  rows sharing one `local_date` forming one logical panel (a lipid panel drawn the same day, say).
  Deliberately a plain CRUD table, not the `health_observation` EAV pipeline every vendor adapter
  feeds — that machinery exists specifically to catalog *auto-discovered* fields from a raw
  vendor payload (`metric_definition`'s own "never drop an unknown field" contract), but a blood
  panel is typed in by the athlete directly, with no raw byte stream to archive and no
  unknown-field problem to solve. Reference ranges (`reference_low`/`reference_high`) are the
  athlete's own, copied from their lab report — nullable, and deliberately never a hardcoded
  "normal range" catalog this project asserts, since ranges genuinely vary by lab, assay, sex, and
  age; the one thing this app does with them is flag a value outside the athlete's *own* stated
  range, never claim clinical validity. `POST /blood-tests/batch` is the primary write path (one
  draw date, any number of markers, in one call, sharing one `lab_name`/`notes`); single-marker
  `POST`/`PUT`/`DELETE` routes exist for adding one more marker afterward or correcting a value;
  `DELETE /blood-tests/by-date/{local_date}` removes a whole panel at once rather than the athlete
  deleting each of a panel's markers one by one. Frontend: `BloodTestsPanel.tsx` on the Health
  page (a plain sibling section below the metric-explorer trend charts, not woven into
  `MetricExplorer`'s own list+`TrendControls` shape, since a blood panel is an event with many
  named markers, not a single continuous metric to chart over time) — panels grouped by draw date
  into collapsible `<details>` (most recent open by default), each a table of marker/value/
  unit/reference-range with an out-of-range flag computed against the athlete's own stored range,
  per-row inline Edit (mirroring `NotesPanel.tsx`'s own reveal-in-place editing) and Delete, plus
  a collapsed-by-default "+ Add blood test" form (same reveal-on-click convention `NotesPanel.tsx`
  established) with dynamic marker rows (add/remove) since a real panel typically has many markers
  entered at once. A real bug this surfaced: `formatDate` initially called `toLocaleDateString`
  on a UTC-midnight `Date` (from `parseIsoDate`) with no `timeZone: "UTC"` option, which silently
  rolls the displayed date back a day for any athlete west of UTC — confirmed live (entering
  "2026-09-13" rendered back as "Sep 12, 2026") and fixed the same way `RunningStats.tsx`'s own
  `formatShortDate` already had to for the identical parse-then-format shape; pinned down with a
  regression test asserting the exact formatted string (this repo's own test environment defaults
  to `America/Los_Angeles`, so the test genuinely exercises the bug, not just *a* rendered date).
  **The add form's own marker field is a dropdown, not free text** (`buildMarkerCatalog`), built
  from the athlete's own already-fetched history, unioned with a small bundled `COMMON_MARKERS`
  list of plain marker *names* (Total Cholesterol, HbA1c, TSH, Vitamin D, and ~20 others) —
  deliberately just names, never a hardcoded "normal" range, which would directly contradict the
  reference-range principle two paragraphs up (a name asserts nothing clinical; a range does).
  `GET /blood-tests` already returns every result ordered `local_date` desc, so the first row
  seen for a given marker name is already that marker's own most recent entry — the dropdown
  offers every distinct marker name the athlete has typed before (auto-filling unit/reference
  low/high from that same most-recent row, still freely editable afterward, since a different lab
  or a genuinely changed range is real too) plus any common-panel name not already in that
  history (offering no autofill, since there's no personal data behind it yet). The bundled list
  exists specifically so a dropdown still has something to offer on an athlete's very first
  entry — confirmed live that with only the athlete's-own-history source, a brand-new athlete saw
  no dropdown at all, a real gap this closed. A "+ New marker…" option switches that one row to a
  free-text input instead, with a "Choose existing" link back, for anything not in either list.
- **Gear: shoe mileage tracking and replacement alerts (`shoe`, `athlete_default_shoe`, `gear.py`,
  `/gear` page)**: athlete-owned shoe pairs (`brand`/`model`/optional `size`/`comments`,
  `initial_distance_km` for mileage already on the pair before it was entered, `max_distance_km`
  defaulting to 800 km or `null` for no limit) with mileage computed live from immutable activity
  summaries on every read (`gear.py::shoe_mileages`), never copied into a mutable counter —
  correcting an activity's sport or distance immediately corrects every affected pair's total,
  with nothing to keep in sync. Two ways a shoe attaches to an activity, in priority order: an
  explicit per-activity choice (`activity.shoe_id`, nullable, set via `PUT
  /gear/activities/{id}/shoe`, `ActivityShoePicker.tsx` on the activity detail page — rejects an
  activity with no real distance, since a shoe pair only makes sense for a distance-bearing
  activity) or, when unset, the athlete's own dated sport default (`athlete_default_shoe`, one
  row per `(athlete_id, sport)`, `PUT /gear/defaults/{sport}`) — **assignment time is the ledger
  boundary**: a default only ever accrues mileage from activities on or after its own
  `assigned_at`, so changing a sport's default shoe never retroactively reassigns past activities
  to it. `GET /gear/shoes` (+ `include_retired=true`) returns each pair's live `distance_km`
  (`initial_distance_km` plus accrued mileage), `remaining_km`, and `over_limit`; `PUT
  /gear/shoes/{id}/retire` preserves history (mileage/history stay queryable) while excluding the
  pair from normal lists, clearing it as anyone's sport default, and stopping further alerts —
  deliberately never a hard delete (this app's own "never destructive" principle). `GET
  /gear/alerts` (banner: `GearAlertBanner.tsx`, rendered globally alongside the top nav, not
  scoped to one page) surfaces every pair whose live mileage has reached its own limit;
  `send_over_limit_alerts` additionally emails once per pair the first time it crosses the limit
  (`shoe.alert_emailed_at`, set only on a successful send — the banner itself stays live and
  computed fresh regardless, so the athlete keeps seeing it until they actually retire/replace
  the pair; the emailed flag only prevents a daily worker run from becoming a daily inbox
  reminder), folded into the existing daily-sync worker job per athlete rather than a separate
  schedule. **Revision: the shoe limit became optional, and retirement was added** — the original
  `max_distance_m` was `NOT NULL`; a follow-up migration (`b7c4d8e9f102`) made it nullable (no
  limit at all is a real, common preference) and added `retired_at`, deliberately never
  backdating a downgrade — retiring a pair is persistent user history the schema keeps even if
  this migration were ever rolled back.
  **Revision: the activity-detail shoe picker resolves and shows the sport default, not a blank
  "Add" prompt** — reported directly: a freshly-imported run already counted toward the default
  pair's mileage total on the Gear page, but `GET /gear/activities/{id}/shoe` only ever returned
  the raw `activity.shoe_id` column (`null` for anything never explicitly assigned), so the same
  activity's own picker showed a bare "Shoes · Add" as if nothing applied at all. `gear.py::
  resolve_activity_shoe` centralizes the resolution both the `GET` and `PUT` endpoints now share:
  an activity's own explicit choice if set, else the athlete's dated sport default when the
  activity falls on or after that default's own `assigned_at` — the identical ledger-boundary
  check `shoe_mileages` already enforces in aggregate, just resolved for one activity instead of
  summed across all of them. `ActivityShoeOut` gains `is_default` (`true` when `shoe_id` came from
  the fallback rather than an explicit pick) for provenance, though the picker itself shows the
  resolved shoe's real name either way, per the athlete's own ask — never the word "default" as a
  placeholder — and stays just as overridable: opening the picker pre-selects whatever's currently
  resolved (the default's own pair, if that's what's showing), and picking a different one (or
  explicitly clearing back to `null`) always saves as a real per-activity choice, exactly as
  before. Clearing an explicit choice via `PUT ... {"shoe_id": null}` now also returns the
  newly-resolved default (if one applies) in the same response, rather than a bare `null`, so the
  UI reflects the fallback immediately without a second round-trip.
- **Performance Curve — best sustained pace/GAP/heart rate across a whole date range
  (`performance_curve.py`, an Insights tab)**: a Runalyze-style "Heart Rate Curve"/cycling
  "Critical Power Curve" — for a chosen metric and date range, the single best D-second window
  *anywhere* across every qualifying activity, for each of a fixed set of durations (1s through
  2h, `DURATION_BUCKETS_S`), plotted duration (log x-axis) vs. best value. Genuinely new
  algorithmic territory for this app: no sliding-window "best effort of duration D" primitive
  existed anywhere before this — `runningStats.ts::personalRecords`'s own docstring already
  flags this exact gap and works around it by picking the fastest whole *activity* instead of a
  real sub-window. `best_window_over_stream` is a two-pointer O(N)-amortized scan per duration
  bucket (both pointers only ever move forward across the whole scan, since the minimal window
  end needed to reach a given duration is non-decreasing as the start advances) — used for both
  `mode="mean"` (heart rate: mean of the samples in the window) and `mode="rate"` (pace/GAP: a
  real distance/actual-elapsed-time rate). The two modes use genuinely different windowing
  conventions, not one shared one: `mode="mean"` follows the standard GoldenCheetah/TrainingPeaks
  convention that duration D means D *samples*, so the window is right-exclusive `[i, j)`
  (`count = j - i`); `mode="rate"` has no such sample-counting convention (it's a real physical
  rate), so the window stays inclusive `[i, j]`, dividing by the real elapsed span. **Gap
  disqualification, never silent bridging**: a candidate window is rejected if reaching
  `duration_s` needed a span more than 10% longer (`_SPAN_TOLERANCE`), or if any single
  inter-sample gap inside the window exceeds `_MAX_GAP_S` (15s) — checked via binary search
  against a once-per-call precomputed list of the stream's own gap positions, not recomputed per
  window; a small gap under the threshold is tolerated, an explicit, adjustable app policy, the
  same never-fabricate discipline `race_readiness.py`/`weather.py` already apply elsewhere.
  GAP reuses `gap.py`'s own per-interval logic rather than re-deriving it:
  `compute_gap_adjusted_distances` was extracted from `compute_avg_gap_speed_mps`'s own inner
  loop (an efficiency-preserving refactor — `compute_lap_gap_speeds_mps` now precomputes this
  array once and passes it into each per-lap call via an internal-only `_per_interval` param,
  avoiding an O(N) → O(N × lap_count) regression), and the curve module cumsums it into a
  GAP-equivalent cumulative-distance array fed through the exact same sliding-window path as
  plain pace. `pace`/`gap` are running-only always, regardless of any `sports` filter — matching
  every other pace feature's exact-`sport=="running"` convention (GAP's Minetti cost-of-running
  model has no meaning for other gaits); `heart_rate` instead takes an athlete-chosen `sports`
  filter, since a hard bike ride or hiit session is a real sustained HR effort too. One combined
  DuckDB query across every qualifying activity's own Parquet file (`read_parquet($1,
  filename=true)` bound to a Python list of paths, confirmed empirically to work against the
  installed DuckDB 1.5.5, not assumed) — a new pattern for this codebase, every prior Parquet
  read having been one `read_parquet(single_path)` call — grouped by filename in Python, with
  both `best_window_over_stream` and `gap.py`'s own `compute_gap_adjusted_distances` (a
  pre-existing, unrelated function this feature calls once per activity) written with numpy
  rather than a per-sample Python loop — verified empirically necessary, not preemptive: a first,
  pure-Python version measured 24-40s for "all time" over this app's own real multi-year history
  (~1,000 running activities), well past the bar this app already set with the
  `ix_activity_metric_athlete_key` precedent below; vectorizing both hot paths (same
  two-pointer/widening-window boundary logic, just found via `np.searchsorted` instead of a
  per-index while-loop — mathematically identical, verified against the existing 40+12 unit tests
  plus a 200-trial randomized property check comparing scalar and vectorized output index-by-
  index) brought "all time" down to 8-15s and every shorter preset under 4s.
  `activity_trim_override` windows honored per activity (a trimmed-out stretch of car travel
  can't set a nonsensical short-duration record) before the per-bucket sliding-window search
  runs. The athlete's own already-computed threshold pace/HR (`performance_daily_rollup`, a
  plain "exact `as_of`-dated row" read) are returned purely as reference values for the frontend
  to draw as dashed lines — never blended into the curve itself, the same "shown alongside,
  never reconciled" posture Race Readiness's own VDOT-based prognosis already establishes.
  `GET /performance/curve` is request-time, not rollup-backed (the same bounded, occasional-
  lookup exception `vo2max_analysis.py`/`pace_hr_zones.py`/`race_readiness.py` already
  establish). Frontend:
  `PerformanceCurveChart.tsx`, a metric selector (Pace/GAP/Heart rate), a date-range preset
  dropdown (Last 3/6 months, Year, All time — no custom pickers, a deliberate v1 scope cut), HR
  sport checkboxes derived from the athlete's own real activity history (never a hardcoded
  catalog, same precedent `buildMarkerCatalog` establishes for blood-test markers), a Recharts
  log-scale line chart with dashed `ReferenceLine`s (each `ifOverflow="extendDomain"`, since
  Recharts' own default silently discards a reference line that falls outside the curve's own
  auto-computed value domain — a real rendering bug caught by a test asserting an exact
  reference-line count, not just "at least one"), and 20-min/60-min stat tiles plus a plain
  comparison sentence against the matching threshold value.
- **Running Eddington number, per year (`eddington.ts`, `EddingtonChart.tsx`, an Insights tab)**:
  the largest integer E such that the athlete completed at least E runs of at least E km each in
  a given calendar year — a classic cycling-logging statistic (VeloViewer and others use it for
  rides) applied here to running, mathematically identical to the h-index (sort distances
  descending; E is the largest N whose Nth-largest value, 1-indexed, is itself >= N). Computed
  entirely client-side over the same full running-history fetch
  (`useAllActivities({ sport: "running" })`) this page's own Pace trends tab already performs —
  no new backend endpoint, the same "fetch once, aggregate in the browser" precedent
  `runningStats.ts`'s own `bestVdot`/`personalRecords`/streak logic already established. Raw
  `distance_m`, not GAP-adjusted (Eddington number is traditionally a real-distance-covered
  statistic, not an effort-adjusted one); exact `sport === "running"` via the same API filter
  `useAllActivities` already applies, matching this app's own established precedent of exact-sport
  matching over `sport_family()` for running-specific stats. Each year also gets a "progress to
  next" figure (`runsTowardNext`/`runsNeededForNext`) — how many of that year's runs already meet
  the *next* Eddington number's own distance threshold, and how many more such runs are needed;
  a mathematical invariant of the h-index algorithm guarantees `runsTowardNext` never reaches the
  next threshold on its own (otherwise that would already be the current Eddington number), so
  `runsNeededForNext` is always >= 1. The current calendar year additionally gets the classic
  VeloViewer-style bar chart (`computeEddingtonBars`, `EddingtonBarChart.tsx`): one bar per
  integer km from 1 to the longest run that year (rounded up), height = how many of that year's
  runs reached at least that far — a non-increasing step function by construction, since every
  run counted at km also counts at every smaller km. Green while the bar still clears its own
  threshold (`count >= km`, i.e. `km <= that year's Eddington number`), red once it falls short,
  with a dotted y=x reference line overlaid — the visual crossing point is the Eddington number
  itself. A Recharts `ComposedChart` (`<Bar>` with per-row `<Cell>` coloring, same pattern
  `TrainingBandsChart.tsx`'s own aggregate chart already uses, plus a dashed `<Line>` for the
  diagonal — `<ReferenceLine>` only supports horizontal/vertical lines, not y=x).
- **PR progress — has this year's pace actually improved on last year's, at every distance
  (`prProgress.ts`, `PrProgressChart.tsx`, an Insights tab)**: a pace-vs-distance scatter plotting
  one dot per ISO week (Monday-Sunday) — the week's single highest-VDOT running activity, same
  "one representative effort per week" precedent `PaceTrendsChart.tsx`'s own "gold trace" already
  established, computed client-side over the exact same full running-history fetch
  (`useAllActivities({ sport: "running" })`) that tab and `EddingtonChart.tsx` already perform, no
  new backend endpoint. Every dot is colored by whether its own week fell at least a year before
  `today` (`yearAgo`, a calendar-year anniversary that clamps Feb 29 back to Feb 28 rather than
  overflowing into March) — red for "one year or older," blue for "less than one year" — and two
  **exact-distance pace frontiers** are drawn as red/blue step lines over those same two point
  sets (`recordFrontier`: sort by distance descending, keep a point only when its pace beats every
  point at an equal-or-longer distance already kept, i.e. "no equally long or longer run is as
  fast" — the same "personal record at or beyond this distance" concept
  `runningStats.ts::personalRecords` already establishes, just as a continuous frontier across
  every observed distance instead of a handful of named race distances). The chart's real point
  isn't the two frontiers alone but **where recent beats old**: `improvements` walks every
  distance boundary either frontier has a point at and keeps only the intervals where a
  *non-older* point on the *combined* frontier (both years pooled) is strictly faster than the
  older-only frontier at that same distance — rendered as extra blue segments directly on top of
  the red step exactly where a real improvement happened, genuinely adjacent improving intervals
  merged into one continuous polyline (rather than two separate `<Line>` elements, which could
  each carry a different `best.pace` and so still trace a real step within that one merged
  stretch) specifically so a real *non*-improving gap between two improving stretches never gets
  bridged by a stray connecting line — confirmed by its own test, "keeps disconnected improvements
  separate across a non-improving interval." An
  exact tie (recent matching, not beating, an old record) is deliberately never colored as an
  improvement (`best.pace >= old.pace - 1e-9`, a float-equality epsilon) — same "don't overclaim
  a tie as progress" instinct as `recordFrontier`'s own tie-break (prefers the older point on an
  exact pace tie, so an unchanged record can never itself masquerade as the frontier's newest
  point). Only ever extrapolates the blue improvement backward to distance 0 or up to the next
  real boundary already on one of the two frontiers — never beyond the longest distance the older
  frontier actually covers, since there is no older baseline to compare a longer recent run
  against yet. Each dot is a real `<a href="/activities/{id}">` (keyboard-focusable, not just
  hover-only) surfacing that run's own name/date/distance/pace/VDOT/duration in a `aria-live`
  details strip and linking straight to the activity.
- **Insights: independently-computed race predictions + threshold/max-HR**:
  `performance_daily_rollup` (`performance_rollup.py::refresh_performance_rollup`) is a second,
  separate rollup alongside `fitness_daily_rollup` — same full-history-recompute-on-every-ingest
  contract, same "shown alongside, never reconciled" posture toward Garmin's own precomputed
  fields (`garmin.daily_race_predictions.*`, `garmin.daily_lactate_threshold.*` stay untouched;
  `athlete_running_load_config`'s manually-configured threshold pace for rTSS is untouched too —
  this is a separate, display-only value). Everything derives from one **42-day trailing
  maximum** of the athlete's own rolling VDOT (`vdot.py`, already used by the Pace-trends tab and
  `fitness_daily_rollup`'s own training-load input) — a *maximum*, not an average/EWMA, because
  an easy run's VDOT reads low from intensity rather than fitness (`DATA_DICTIONARY.md`'s own
  VDOT section already warns about this; `runningStats.ts::bestVdot` already takes the highest
  for the same reason). From that rolling VDOT: threshold pace is a closed-form inversion of
  `vdot.py`'s own `VO2(v)` equation at 88% of vVO2max (`compute_threshold_pace_s_per_km` — a
  representative point within Daniels' cited 86-92% range, verified by hand against published
  VDOT tables); the four race times (5k/10k/half/marathon) come from `predict_race_time_s`, a
  bisection search over duration exploiting that `compute_vdot(distance, T)` is monotonically
  decreasing in `T` (verified numerically, not assumed) — chosen over Riegel's simpler power-law
  formula because research shows Riegel underestimates marathon time from shorter races for many
  runners, while VDOT stays close across the same gap. Max HR is a **365-day** trailing maximum,
  deliberately much longer than VDOT's 42-day window since a true max-HR effort is rare
  week-to-week and a shorter window would flicker based on incidental recent effort rather than
  physiology — and deliberately **all sports**, not running-only, since a max-HR effort from
  cycling or hiit is equally real and restricting to running would silently discard it. This
  project's own empirical max HR is used as the PRIMARY source in place of any age-based formula
  (220-age, Tanaka, HUNT) — research shows all of them carry ~10-15bpm error even in their best
  forms — and is never overridden by a formula once real data exists. A brand-new athlete has
  zero empirical max HR for weeks, though, so `max_hr_bpm` falls back to the Tanaka formula
  (`208 - 0.7×age`, via the athlete's own optional `athlete.birthdate` set through
  `GET/PUT /settings/profile`, and `athlete_age.py::age_years_as_of`) for just that gap —
  `max_hr_source` (`"empirical"|"formula_fallback"|null`) records which path fired. This also
  unblocks threshold HR's own existing 88%-of-max-HR fallback below for a new athlete, since that
  fallback needs a non-null `max_hr_bpm` to compute from. Threshold
  HR is **empirical-first**: the median HR among runs within ±5% of that day's own threshold pace
  (by grade-adjusted pace) over the same 365-day window, requiring at least 3 qualifying runs to
  resist a single outlier; below that it falls back to 88% of that day's max HR (a representative
  point within the 85-92%-of-max-HR range research cites for well-trained runners) —
  `threshold_hr_source` (`"empirical"|"fallback"`) records which path fired, same provenance
  instinct as storing `training_load` alongside CTL/ATL. Wired unconditionally alongside
  `refresh_fitness_rollup` in `garmin_connect.py` (its rolling windows are date-dependent, so it
  must keep advancing through rest days exactly like `fitness_daily_rollup` does), and inside the
  touched-dates guard everywhere else (`fit_folder.py`, `garmin_export.py`, `strava_export.py`,
  `rebuild.py`, plus the trim/merge cascade and threshold-pace-config save in
  `api/routers/activities.py`/`settings.py`). `GET /performance` mirrors `GET /fitness` exactly.
  Frontend: two more Insights tabs, `RacePredictionsChart.tsx` (4 *separate* single-series
  charts, not one combined chart — the four distances span a ~10x time range that would flatten
  5k/10k to near-invisibility on one shared axis, and `TrendChart` only supports two y-axes
  anyway) and `ThresholdMaxHrChart.tsx` (threshold pace as its own single-line chart; threshold HR
  paired with max HR on one shared-axis chart instead, not with pace — both are bpm and directly
  comparable, e.g. threshold HR always sitting below max HR, whereas pace+HR together would need
  two separate axes) — both reuse the exact `MetricExplorer`/`TrendControls`/`trendWindow.ts`
  wiring `FitnessPage.tsx` established. `TrendChart.tsx`'s Y-axis ticks now use each axis's own
  series `formatValue` too (previously only the tooltip and the "latest" note did) — a bare-number
  axis reading e.g. "1529" for a race-time-in-seconds series was confirmed confusing in practice,
  not just a cosmetic gap.
  **VO2max, explicitly** (a third Insights tab, `Vo2maxChart.tsx`): `rolling_vdot` is not just an
  intermediate input to threshold pace/race predictions above — under the Daniels-Gilbert model
  it already *is* the VO2max estimate itself, in the same ml/kg/min units clinical VO2max is
  measured in, so this tab surfaces that same rollup field directly rather than computing
  anything new, labeled explicitly as this project's own estimate, never Garmin's. Only one
  metric exists here (unlike the two siblings above), so `Vo2maxChart.tsx` skips
  `MetricExplorer`'s list+detail shell entirely — a picker with nothing to pick between would
  just be clutter — while still reusing the same `TrendControls`/`trendWindow.ts`
  week/month/year/all-time/custom wiring. Paired on the same tab with `Vo2maxFactorAnalysis.tsx`
  ("which activities contributed, what's missing"), backed by a new `GET
  /performance/vo2max-analysis` (`vo2max_analysis.py::compute_vo2max_factor_analysis`) —
  deliberately request-time, not rollup-backed, since `rolling_vdot`'s own window
  (`ROLLING_VDOT_WINDOW_DAYS`, exported from `performance_rollup.py` for this reuse) is tiny (a
  handful of the athlete's own runs), a world apart from the multi-year aggregates the rollup
  mandate above targets — the same "bounded, occasional diagnostic lookup" exception
  `/activities/needs-trim`/`/activities/possible-duplicates` already establish, not a new
  precedent. Since `rolling_vdot` is a rolling *maximum*, not an average, exactly one run in the
  window ever sets the value (`driving_activity`) — every other qualifying run
  (`other_contributors`, sorted by VDOT descending) is careful to read as "ready to take over,"
  never as jointly averaged in. `missing` is a small set of plain-language, concretely-grounded
  diagnostics (never a guessed constant with no stated reasoning, same discipline as every
  fraction/window above): no qualifying run yet at all; the driving run ages out within
  `_EXPIRING_SOON_DAYS` (7) with or without a replacement already in the window; no qualifying
  run in over half the rolling window (`_STALE_EFFORT_DAYS`); or only one qualifying run total.
  A real bug this feature surfaced and fixed along the way: `activity_metric`'s existing indexes
  both lead with either `athlete_id, activity_id, metric_key` (the uniqueness constraint) or
  `activity_id, metric_key` — neither serves "every row for this athlete carrying one specific
  metric_key across every activity," the exact shape both of `vo2max_analysis.py`'s own window
  queries need. Confirmed live via `EXPLAIN QUERY PLAN` and direct timing (33s cold on real
  ~1,400-day history, a full per-athlete scan across every metric ever recorded) before adding
  `ix_activity_metric_athlete_key` (`athlete_id`, `metric_key`) dropped it to 0.002s — the same
  "verify against real data, don't guess" methodology, and the same missing-index failure mode,
  as `ix_sleep_stage_session` earlier this project.
- **Threshold Analysis: aerobic threshold added alongside the existing anaerobic one, plus a
  factor-analysis breakdown** (Insights tab renamed from "Threshold & Max HR", `vdot.py`,
  `performance_rollup.py` -- the tab and its `threshold_analysis.py` factor-analysis module were
  since removed, see the Revision note below): the athlete's own "threshold pace" had always
  meant the anaerobic/lactate threshold ("LT2"/"VT2", `THRESHOLD_VO2MAX_FRACTION = 0.88`, unchanged
  field names `threshold_pace_s_per_km`/`threshold_hr_bpm` — every existing consumer, `hr_zones.py`
  and `running_load.py` included, keeps meaning this one). New `aerobic_threshold_pace_s_per_km`/
  `aerobic_threshold_hr_bpm`/`aerobic_threshold_hr_source` columns compute the aerobic threshold
  ("LT1"/"VT1", always a slower pace) via the exact same `compute_threshold_pace_s_per_km`
  function at `vdot.AEROBIC_THRESHOLD_VO2MAX_FRACTION = 0.73` instead — one model, a second
  representative point on it, not a second formula. Both fractions were checked against real,
  recent, running-specific literature rather than left resting on Daniels' 1979 model alone: Fathi,
  Shahidi & Alhusaen Aga (2025, *Int J Exercise Science* 18(5):1381-1392, n=12 trained runners,
  gas-exchange/visual-inspection method) measured VT1 at 73.2±4.1% VO2max and RCP/VT2 at
  89.6±3.8%; Esteve-Lanao, Sellés-Pérez, Arévalo-Chico & Cejuela (2026, *Sports* 14(1):29, n=1,411
  endurance runners) measured VT1 at 67.5-73.4% VO2peak and VT2 at 83.8-87.4% depending on
  performance level, plus VT1/VT2 heart rate at 85.1±4.6%/93.5±2.5% of HRpeak. 0.73 is where the
  two studies converge on VT1 to within a fraction of a percent; 0.88 (anaerobic, unchanged) sits
  between the two on VT2 rather than shifted toward either alone, since real downstream consumers
  already calibrate against it. The aerobic threshold's own HR fallback fraction
  (`AEROBIC_THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR = 0.851`) is new and uses Esteve-Lanao's VT1
  figure directly; the anaerobic HR fallback (0.88, from an older, more generic citation) is
  deliberately left unchanged in this same pass — Esteve-Lanao's own 93.5% VT2 figure would be a
  real, materially different number, but revising an already-relied-upon HR-zone fallback used by
  existing athletes is a separate decision from adding a brand-new one, not bundled in here. A
  shared `compute_threshold_hr` helper (exported from `performance_rollup.py`, alongside
  `ROLLING_VDOT_WINDOW_DAYS`/`MAX_HR_WINDOW_DAYS`/`THRESHOLD_HR_WINDOW_DAYS`/
  `THRESHOLD_PACE_TOLERANCE`/`MIN_THRESHOLD_HR_SAMPLES`/`MAX_HR_METRIC_KEYS`/`AVG_HR_METRIC_KEYS`/
  `priority_merge`/`median`) computes both thresholds' empirical-median-or-fallback HR so they can
  never silently drift onto different logic — same reuse instinct `vo2max_analysis.py`'s own
  `ROLLING_VDOT_WINDOW_DAYS` reuse already established.
  **Revision: the "Threshold Analysis" tab (a factor-analysis breakdown of these same two
  point-in-time numbers) was replaced entirely by a complete 5-zone Pace/HR table** — asked for
  "recovery/basic endurance/aerobic threshold/lactate threshold/VO2max," each a real pace range
  and HR range with a plain-language "when/how to use it," computed from the athlete's *entire*
  running history rather than a day-to-day snapshot, plus the actual runs behind each range.
  `threshold_analysis.py`, `ThresholdAnalysisChart.tsx`, and `ThresholdFactorAnalysis.tsx` are
  gone (nothing else depended on them — confirmed by grep before deleting; `performance_rollup.
  py`'s own `threshold_pace_s_per_km`/`aerobic_threshold_pace_s_per_km`/HR fields stay exactly as
  documented above, since `PerformanceCurveChart.tsx`'s reference lines still read them straight
  from `performance_daily_rollup` via `GET /performance`/`GET /performance/curve`, independent of
  the removed factor-analysis endpoint). See `pace_hr_zones.py`'s own module docstring for the
  full model and every citation; summarized: the two hard boundaries (Zone 2/3 = aerobic
  threshold, Zone 4/5 = lactate threshold) reuse `vdot.AEROBIC_THRESHOLD_VO2MAX_FRACTION`/
  `THRESHOLD_VO2MAX_FRACTION` exactly (0.73/0.88 VO2max, unchanged) plus, on the HR side,
  Esteve-Lanao et al.'s own directly-measured VT1/VT2 heart rates (85.1%/93.5% of HRpeak) — a
  first-time use of the more precise VT2 figure, since this new feature has no installed-base
  fallback to preserve the way `performance_rollup.py`'s own 0.88 anaerobic-HR fallback does. The
  other two boundaries (Zone 1/2, Zone 3/4) are coaching judgment calls stated plainly as such —
  90% of the aerobic threshold, and the exact midpoint between the two thresholds — mirroring
  Seiler's own three-zone polarized-training skeleton (Seiler & Kjerland 2006; Seiler 2010)
  subdivided once more on each end rather than a different model. Deliberately built on the
  athlete's ALL-TIME best VDOT and ALL-TIME max HR (each with the one activity that set it), not
  `performance_daily_rollup`'s own 42-day/365-day rolling windows — this is a stable reference
  table an athlete keeps using for months, the wrong job for a "current fitness" tracker; same
  "best of the period, not a moving trend" reasoning `runningStats.ts::bestVdot` already
  established, just extended to unbounded history. Each zone's own HR range is empirical-first,
  formula-fallback-second, extending `performance_rollup.py::compute_threshold_hr`'s philosophy
  from "the two threshold points" to all five zones: every qualifying run (VDOT-eligible, real
  avg HR, GAP pace inside that zone's band) across the athlete's whole history contributes its
  own avg HR, and the reported range is the real 25th-75th percentile of that distribution once
  there are enough of them (`MIN_ZONE_HR_SAMPLES`, reusing the rollup's own bar) — a formula
  fraction of max HR, widened into a small band, only below that. `GET /performance/pace-hr-zones`
  is the same "bounded, occasional diagnostic lookup" exception `vo2max_analysis.py` already
  establishes. Frontend: `PaceHrZonesTable.tsx` — one table (zone/pace/HR/description) plus one
  collapsed-by-default "Why these numbers" `<details>` per zone (Zone 2 open by default, since
  that's where most easy running actually happens) listing the real qualifying runs, each linked
  to its own activity page, evenly sampled down to a cap when there are many so the low/middle/
  high of the real spread all stay visible rather than an arbitrary slice. A real bug caught by
  its own component test before shipping: the open-ended zones (Zone 1 has no pace floor, Zone 5
  no pace ceiling) initially rendered "Faster than"/"Slower than" backwards — Zone 1's one defined
  edge is its *fastest* allowed pace (the zone extends slower, unbounded), so the correct wording
  is "slower than" that edge, not "faster than," and symmetrically for Zone 5.
  **Revision: `profile_vdot` prefers a marked race over any training run** — the athlete reported
  the table's paces as unrealistically fast, and pointed at the fix: `activity.is_race` already
  marks which activities were real races (`garmin_activity_summary.py`'s own eventTypeId
  heuristic plus the athlete's own `PATCH /activities/{id}/race` correction, see the sport_override
  bullet below). Checked against this app's own real history, not a hypothetical: the single
  highest VDOT across *any* running activity ever recorded (47.7) came from a 12.6-minute, ~3.1 km
  segment named "Santa Clara Other" — not a race, and almost certainly a hard training
  effort/interval whose "moving duration" undercounts recovery time — while the best VDOT among
  activities actually marked as races tops out at a materially lower, more honest 44.5 (a real
  10K). `perseverer.performance.vdot` (`performance.py::refresh_vdot`) is computed for every
  running activity with no minimum-duration floor, but the Daniels VDOT formula is calibrated
  against genuine race performances — a short, all-out training segment can post a VDOT well above
  anything the athlete could actually hold for a real race distance, and the original "single
  highest VDOT ever, from any activity" design had no defense against that. `_all_time_best_vdot`
  now tries `is_race == True` activities first and only falls back to the original unfiltered
  all-time-best behavior when the athlete has no marked race at all; `profile_vdot_source`
  (`"race"`/`"training_run"`/`null`) records which path fired, mirroring `profile_max_hr_source`'s
  own provenance instinct, and a `training_run`-sourced profile gets an explicit `missing` caveat
  pointing at the "Mark as a race" action on that activity's own detail page. Deliberately scoped
  to VDOT/pace only — `profile_max_hr_bpm` stays the single highest heart rate across every
  activity ever recorded, any sport, unchanged, since a genuine max-HR effort doesn't need a race
  context to be real the way a race-calibrated pace formula does.
  **Revision: the Zone 1/Zone 2 boundary itself was still too fast** — after the race-VDOT fix
  above, the athlete reported the *direction* was right but the Recovery zone still didn't reach
  slow enough (its own upper pace bound, "90% of the aerobic threshold," only moved to 5:55/km,
  when their own real recovery jogging is closer to 6:30+/km). The original "90% of VT1" figure
  was a generic ventilatory-threshold-zone-calculator convention with no direct tie to this app's
  own model — replaced with `ZONE1_2_VO2MAX_FRACTION = 0.59`/`ZONE1_2_HR_FRACTION = 0.65`, the
  *floor* of Jack Daniels' own published "Easy" (E) pace range (59-74% VO2max, ~65-78% HRmax,
  *Daniels' Running Formula*) — the same Daniels-Gilbert model this app's VDOT is already built
  on, so this reuses an authority already load-bearing here instead of a new, weaker one. A nice
  consistency check this surfaced: Zone 2 (Basic Endurance, this boundary through the aerobic
  threshold at 0.73) now runs almost exactly Daniels' own E-pace range end to end, since 0.73
  already sits right at Daniels' own 0.74 E-pace ceiling — Zone 1 (Recovery) is genuinely *below*
  easy pace, not merely a bit under threshold. On the real data this moved the Recovery boundary
  from 5:55/km to 6:26/km.
  **Revision: windowed lookback (365 days for training runs, 730 for races/max HR), not the
  athlete's literal entire history** — the athlete then asked, reasonably, why this feature reached
  so far into their own past at all, and proposed the fix directly: cap training runs at one year,
  but give races a longer two-year window since they're rare (this athlete averages roughly one
  every five months — a one-year race window would often come up empty and silently fall back to a
  training run, undoing the race-preference fix above). `TRAINING_RUN_WINDOW_DAYS = 365`/
  `RACE_WINDOW_DAYS = 730`/`MAX_HR_WINDOW_DAYS = 730` (`pace_hr_zones.py`, see its own "How far
  back" docstring section) replace the original all-time, unbounded lookback across every one of
  `_best_vdot_in_window`/`_max_hr_in_window`/`_qualifying_runs_in_window` (the last of these also
  bounds the per-zone empirical-HR evidence pool, not just the profile VDOT itself) — the original
  design had followed the athlete's own first request literally ("based on all the runs I have done
  in the past"), which this revision supersedes. The real cost the unbounded version paid: the
  athlete's own all-time-best race was from 2023, so the whole table stayed anchored to a
  nearly-2-year-old data point regardless of how their fitness had since changed — a materially
  bigger source of staleness than either bug fixed above.
- **Adapters** implement one `SourceAdapter` protocol (`health_check`, `authenticate`,
  `list_changed`, `fetch_raw`, `parse` — see `adapters/base.py`). Five exist now:
  - `fit_folder` (`adapters/fit_folder.py`) — polling directory importer, content-hash
    idempotent. The universal offline importer/test harness for every other adapter. Also
    recognizes two narrow Garmin Connect-shaped health JSON filename patterns
    (`daily_summary_*.json`, `hydration_*.json`) — not a general JSON importer.
  - `garmin_export` (`adapters/garmin_export.py`) — historical backfill from a Garmin "Export
    Your Data" archive (directory or `.zip`), zero network calls. Recursively finds FIT files
    at any depth, including nested inside further `.zip` files — confirmed necessary against a
    real export, whose FIT files (activity and health mixed together) all live one zip-level
    deeper than the top-level archive; see `docs/adr/0005-phase-2-garmin-export-real-data.md`.
    Health JSON under `DI-Connect-Wellness`/`Metrics`/`Aggregator` is parsed generically
    (`garmin.export.<report_kind>.<field>`, one parser for all ~24 report kinds, not bespoke
    code per kind — ADR 0005). Everything else (account data, bulk activity summaries, and
    unrelated non-fitness product domains a Garmin account's export can bundle) is archived
    raw but not parsed.
  - `garmin_connect` (`adapters/garmin_connect.py`) — the primary, incremental, unattended
    sync path, and the adapter most likely to break. See the safety rules below before
    touching this file. Seven daily fetches per rolling-window date, each independently
    rate-limited/429-abortable and self-healing on the next run: activities (FIT download),
    `get_stats` (steps/HR/etc., `garmin.daily_summary.*`), `get_sleep_data`
    (`garmin.daily_sleep.*` + a real `sleep_session`/`sleep_stage` row — added after this
    adapter turned out to never fetch sleep at all, silently going stale the moment the last
    `garmin_export` backfill's own data ran out even though the daily sync kept succeeding),
    `get_hrv_data` (`garmin.daily_hrv.*`), `get_training_readiness`
    (`garmin.daily_training_readiness.*`), `get_training_status` (one call covering three
    export report kinds at once — `garmin.daily_vo2max.*`, `garmin.daily_heat_altitude.*`,
    `garmin.daily_training_status.*`), and `get_hydration_data` (reuses the same
    `parse_hydration_json` `fit_folder.py` already had, just a new `kind` string). Plus one
    *range* fetch per run (not per-date): `get_race_predictions` covers the whole rolling
    window in a single call (`garmin.daily_race_predictions.*`), and likewise
    `get_lactate_threshold(latest=False, aggregation="daily")` (`garmin.daily_lactate_threshold.
    {speed,heart_rate,power}` — a real, live-verified endpoint feeding the Fitness & Form tab's
    Lactate threshold chart, found by introspecting the installed `garminconnect` package per
    this project's own "verify, don't assume" rule for fast-moving vendor libraries; confirmed
    live against the real account that Garmin only emits an entry for a day it actually
    recomputed the value on, not one per calendar day in range). `heart_rate`/`power` are
    already real units (bpm/W); `speed` is NOT plain m/s despite the name — the raw value needs
    ×10 first (confirmed empirically: the athlete's own real numbers only make physiological
    sense, sitting between their real VO2max-interval and easy-run paces, after that
    correction), applied at the point of use (`FitnessPage.tsx`), matching this same adapter's
    existing single-foot-cadence-doubling precedent of storing the raw vendor value and
    converting on read, not at ingest. A comprehensive metric-by-
    metric audit (checking every `garmin.export.*` report kind against the installed
    `garminconnect` package's live `get_*` methods) found and closed this same "never fetched
    live" gap for all of the above — each had silently gone stale the moment the last
    `garmin_export` backfill's data ran out, exactly like sleep above, even though the daily
    sync kept reporting success. Two low-value report kinds (`healthStatusData` — internal
    data-quality metadata, `outliersCount` almost always 0; `AbnormalHrEvents` — only 2 events
    across ~4 years of real data) were deliberately left export-only rather than wired live.
    Body battery (Phase 9, revised): `HealthStreamPoint`s under `garmin.daily_body_battery.level`
    in `health_stream`/Parquet, not `health_observation` — added because no daily-summary or
    GDPR-export field carries a real per-minute body-battery series, only 8 sparse named
    checkpoints. The first live source tried, `get_body_battery` (a *range* fetch, one call per
    run), itself turned out on live verification to return only ~6 sparse checkpoints/day —
    nowhere near dense enough. `get_stress_data` (a *per-date* fetch, like sleep/HRV/etc. above,
    not a range call — this endpoint only takes a single date) turned out to carry a genuinely
    dense, ~3-minute-cadence `bodyBatteryValuesArray` instead (confirmed live against the real
    account, not assumed), and is now the adapter's live body-battery source — see
    `health/json_parser.py::parse_daily_stress_json`'s own docstring. The original
    `get_body_battery`-based fetch/parser (`parse_daily_body_battery_json`) stay in place
    unchanged, purely so already-archived raw bytes of that shape still replay on `sync rebuild`
    (raw-first/never-destructive) — `rebuild.py` has a branch for each raw JSON `kind`. This
    response also carries a real intraday *stress* series and daily stress scalars
    (`avgStressLevel`/`maxStressLevel`) that aren't parsed into observations yet — cataloged via
    `unrecognized_field_keys`, not dropped, pending a future stress feature. Surfaces as a real
    intraday chart on the day view (`GET /health/stream`, the first endpoint to read
    `health_stream` at all), not the sparse connect-the-dots version the old fetch produced.
  - `strava_export` (`adapters/strava_export.py`, Phase 8) — historical backfill from Strava's
    "export your data" archive, zero network calls. `.fit`/`.fit.gz` files go through the same
    `ingest_dispatch.ingest_fit_bytes` as every other source (often literally the same Garmin
    FIT bytes Strava received via auto-upload); `.gpx`/`.gpx.gz`/`.tcx.gz` go through new
    `gpx/parser.py`/`tcx/parser.py` (bare geometry + whatever sensor extensions the file
    carries), with `activities.csv`'s own totals/sport classification overlaid afterward since
    GPX/TCX carry far less than FIT. See `docs/adr/0012-phase-8-strava-merge-insights.md` for
    the real archive shape this was built against (not assumed from docs).
  - `eufy` (`adapters/eufy.py`) — body composition (weight, body fat, muscle/bone mass, water %,
    BMR, visceral fat, metabolic age, protein ratio, BMI) from a Eufy smart scale, via the same
    Eufy Life app API a sibling `eufy-health-sync` project already uses in production, ported
    directly rather than reimplemented. Garmin has no body-composition data in this project at
    all, so this is the sole source. Unlike `garmin_connect`, deliberately NOT built around a
    persisted token-store/never-auto-login model — no evidence Eufy's API shares Garmin's SSO
    lockout fragility, and the sibling project's own plain-env-var, fresh-login-per-run pattern
    has run safely, daily, unattended for months. The one `last_device_data` endpoint always
    returns the athlete's *entire* reading history in one call (the `limit` param is silently
    ignored, confirmed live) — no separate backfill-vs-incremental mode; every run reprocesses
    full history, relying on content-addressed archiving and idempotent upsert to make repeat
    runs of unchanged readings cheap no-ops. `health/eufy_parser.py` flattens *every* scalar
    `scale_data` field into `eufy.scale.<field>` (not just the 9 fields the sibling project's
    own extraction uses) — see `docs/DATA_DICTIONARY.md` for the full field list and unit
    handling. `sync_eufy()` itself still just takes credentials as plain parameters, but where
    those come from is now per-athlete: `athlete_eufy_config` (one row per athlete, same
    "narrow config, upsert not history" shape as `athlete_hr_zone_config`) rather than one global
    `Settings.eufy_*` env-var set — `resolve_eufy_credentials` (also in `adapters/eufy.py`) picks
    a DB row when one exists and falls back to those original env vars only for
    `DEFAULT_ATHLETE_ID`, so the original single-athlete deployment needs no migration.
    `athlete_eufy_config` is now settable from the Settings page too (`EufyCard.tsx`,
    `POST /settings/eufy/login`), not just `sync athlete set-eufy-credentials` — the web path
    verifies the credential against Eufy's own login endpoint before saving, mirroring
    `POST /settings/garmin/login`'s own "don't persist something we haven't confirmed works"
    posture (`device_id`/`customer_id` can't be verified this way, so they're stored as given
    either way, same as the CLI).
    `GET /health/dashboard`'s `bmr_kcal` gets a formula-computed FALLBACK (`bmr.py::
    compute_bmr_kcal`, Mifflin-St Jeor) for a day with a resolved weight but no real Eufy `bmr`
    reading — e.g. any athlete with no Eufy scale at all — using that day's weight plus the
    athlete's own optional `birthdate`/`height_cm`/`sex` profile (`GET/PUT /settings/profile`).
    Only fires when all three profile fields are set; a real Eufy reading always wins and is
    marked with a synthetic `source_metric_key = "computed.mifflin_st_jeor"` +
    `n_observations = 0` when it's the formula instead — same provenance instinct as
    `threshold_hr_source` above, reusing fields `HealthDashboardDayOut` already had rather than a
    schema change. `metabolic_age` gets no such fallback (no legitimate formula for a
    Eufy-proprietary population-comparison figure).
  - `apple_health_export` (`adapters/apple_health_export.py`) — historical backfill from an
    Apple Health "export.xml" archive (Settings > [Name] > Export All Health Data on iOS), zero
    network calls. Imports blood pressure (full history — nothing else in this project has any
    BP source) and body mass/BMI/body-fat percentage dated strictly before the athlete's earliest
    `eufy.scale.weight` reading (`weight_before`, auto-detected from the DB by the CLI unless
    overridden) — this fills the real gap Eufy itself can't (Eufy only has data from when that
    scale was bought), rather than duplicating what Eufy already covers. A record whose
    `sourceName` is `"eufy Life"` is excluded even when its date falls before the cutoff — that's
    Apple's own sync of the same first Eufy reading landing on the other side of a UTC/
    local-date boundary, not a distinct pre-Eufy data point. `BodyFatPercentage` values are a
    0–1 fraction despite carrying `unit="%"` (a real HealthKit quirk, confirmed against a real
    export) and are multiplied by 100 to match `eufy.scale.body_fat`'s already-established true-
    percent convention; `BodyMass` gets a defensive lb→kg conversion (not exercised by any real
    export seen so far — every one has been 100% kg). Unlike every other file-shaped adapter in
    this codebase, which archives raw bytes per *unit fetched* (one raw_object per FIT file, per
    Garmin JSON report, per Eufy API reading), this adapter archives the **entire** export.xml as
    a single `raw_object` (`kind="apple_health_export_xml"`) — a real export can run to
    gigabytes with millions of XML elements, so per-record archiving (no precedent anywhere in
    this codebase) would mean millions of tiny raw_object rows for a one-time import, with no
    benefit over archiving the source file once. A single streaming parse
    (`health/apple_health_parser.py`, the only `ET.iterparse`-based parser in this codebase —
    every other XML parser here is small-file, non-streaming) then extracts the ~5 record types
    this pass cares about out of the ~76 present in a real export; everything else (ECG,
    nutrition, mindfulness, Apple Watch-era vitals Garmin already covers, and 996 real
    clinical/medical records from a connected health-records account — a different, more
    sensitive category of data entirely) stays deliberately unparsed, not lost: because the whole
    file is archived, extending this parser later to pull more record types never requires the
    user to re-supply the original export, just a code change plus `sync rebuild`.
    `weight_kg`/`bmi`/`body_fat_pct` on `GET /health/dashboard` (`api/routers/health.py`) carry a
    second alias for these three `apple_health.*` metric keys alongside their Eufy ones, so the
    existing weight/BMI/body-fat charts extend back through the pre-Eufy era with no frontend
    changes — the two sources never share a date by construction, so the same sequential
    outlier-rejection walk (`_body_composition_daily`) that already guards against a shared
    bathroom scale picking up someone else's reading keeps working unmodified across the source
    boundary. Blood pressure itself has no dashboard chart yet (deliberately deferred — see
    `docs/DATA_DICTIONARY.md`), queryable via `GET /health/observations` only for now.
  Every `.fit` file, from any adapter (except `garmin_connect`, which only ever downloads
  activity FIT files), goes through `ingest_dispatch.ingest_fit_bytes` — archives once, tries
  the shared activity parser (`fit/parser.py`), falls back to the shared health parser
  (`health/fit_parser.py`) if it isn't an activity. No adapter has its own parser. See
  `docs/adr/0004-phase-2-health-ingestion.md`.
  When a vendor breaks — and Garmin already has, twice, as of this writing — the fix is
  confined to one adapter file. If fixing a vendor break means touching the schema, the design
  is wrong; stop and say so.
- **Manual per-activity corrections (`activity_sport_override`, `sport_override.py`)**: the
  shared durable-override mechanism several other features cite as precedent (bouldering route
  corrections, the cross-source merge below) but that never got its own writeup. `sport`/
  `sub_sport`, `is_race`, `name`, and `carbohydrates_g`/`sodium_mg` are each settable
  independently (`PATCH .../sport`, `.../race`, `.../name`, `.../fueling`) without disturbing the
  others, keyed by `(athlete_id, start_time_utc)` — not `activity.id` (a fresh ULID minted on
  every `sync rebuild`) — so a correction survives a full wipe-and-replay via
  `apply_sport_overrides`, reapplied at the end of every rebuild and immediately once when a
  correction is first set. Real, confirmed needs behind each: a third-party tool
  ("Sauce for Strava") writes `sport=running` into every FIT file it reconstructs regardless of
  what the activity actually was; Garmin's own `eventTypeId`-based `is_race` heuristic
  (`garmin_activity_summary.py`) only reflects whether the athlete flagged a race *inside* Garmin
  Connect, missing a real race never flagged there; carbohydrate/sodium intake has no vendor
  source in either the FIT profile or the Garmin Connect API at all — pure athlete input from the
  start. An earlier, broader attempt at automatic name correction (trusting Garmin Connect's own
  name unconditionally) was reverted after it mass-overwrote 396 real custom titles with a single
  generic auto-template ("Santa Clara Other") no more informative than what it replaced — there's
  no reliable automatic signal for "this name is a real title, not a template," so a boring name
  is a manual, per-activity call only the athlete can make. **The one place this project does
  automate a name correction safely**: `garmin_connect_activity_name.py::
  backfill_garmin_activity_names` (`sync backfill-garmin-activity-names`) writes to this same
  `name` override, using Garmin Connect's own cloud-side `activityName` field (already archived
  raw, just never parsed before this) — but *only* when the current name is still the sport's own
  generic device default (`_GENERIC_DEFAULT_NAME_BY_SPORT`), never a real custom title, avoiding
  the earlier mistake by construction. Live-verified against the athlete's own account that
  Garmin's cloud name can be genuinely richer (a FIT file's bare "Running" vs. Garmin's own
  "Santa Clara - W12 Fri . [Consolidation] Easy" for the same activity) — this same audit also
  found and fixed a real gap where 3 yoga activities stayed stuck on the bare "Yoga" placeholder
  because `training` (a generic container sport) had no recognized default at all.
- **Merge engine + visibility (`merge/engine.py`, Phase 1; UI Phase 8)**: `is_same_activity()`
  is source-agnostic by design — comparing only start time / sport family / duration — and
  already runs on every ingest via `fit_folder.py::_find_merge_match`, so cross-source
  deduplication needed zero new matching code when `strava_export` arrived; every decision is
  logged to `merge_decision` with human-readable reasons. `GET /activities/{id}/sources` and
  `POST .../sources/{link_id}/split` (`api/routers/activities.py`) make merges inspectable and
  reversible — split re-parses that one source's already-archived raw bytes via `reparse.py`
  and `adapters/fit_folder.py::insert_new_activity`, without deleting anything or re-running
  merge-matching (which could just re-merge it right back). See ADR 0012.
- **Manual cross-source merge, per field (`activity_merge_override`, `activity_merge.py`)**: for
  a duplicate `_find_merge_match` failed to catch at ingest time — real, confirmed need: this
  athlete's own archive has activities recorded by both a Garmin device and synced to Strava,
  independently imported, that never merged, sometimes because a sport correction landed *after*
  the other source was already imported (merge-matching only ever runs once, at ingest, for the
  incoming candidate — an existing activity's later correction never retroactively re-triggers
  it), sometimes because the two platforms genuinely computed a different value for the same
  activity (a duration disagreement only the athlete can resolve). Unlike `_find_merge_match`'s
  own automatic merge (whichever source ingests first silently wins every field), this lets the
  athlete pick *per field* which side survives — `MERGEABLE_SCALAR_FIELDS`
  (distance/duration/elevation/etc.), `MERGEABLE_METRIC_FIELDS` (avg/max HR, training load —
  copies the *whole* `activity_metric` row, value plus its own `source`, preserving provenance
  per field rather than just the number), and `MERGEABLE_COLLECTION_FIELDS` (route/laps/splits/
  stream — whole-collection swaps only, never per-point/per-lap, which would need a much bigger
  UI control for no real benefit). `GET /activities/possible-duplicates` runs the Settings page's
  list-wide scan (benchmarked ~0.5s over this athlete's full ~1,800-activity archive, a
  deliberate occasional-visit-only exception to this app's usual never-scan-list-wide rule);
  `GET /activities/{id}/merge-preview/{other_id}` builds the per-field comparison the athlete
  picks from; `POST /activities/{id}/merge` applies it. Same durable-override shape as
  `bouldering_overrides.py`/`activity_trim.py`, for the same reason: `sync rebuild` wipes and
  re-derives `activity`/`activity_source_link` from raw bytes every run, re-splitting the two
  activities right back apart unless reapplied (`apply_activity_merge_overrides`, keyed by
  `(source, external_id)` pairs — not `activity.id`, a fresh ULID every rebuild, and not
  `start_time_utc` alone, since two merged activities share it *by definition*, so it can't tell
  "kept" from "absorbed" apart once a rebuild re-splits them into two identically-timestamped
  rows again).
- **Bouldering route corrections (`bouldering_route_status_override`, `bouldering_manual_route`,
  `bouldering_overrides.py`)**: the reverse-engineered `climb_result`/`climb_grade` decode (see
  `docs/DATA_DICTIONARY.md`'s own "Bouldering per-route data" section for the raw-FIT-field
  crack) gets it wrong occasionally, and a route climbed after the watch was stopped has no
  FIT-derived split at all. Same durable-override shape as `sport_override.py`, for the same
  reason: `sync rebuild` re-derives `split` from scratch every run, so a bare `UPDATE` would
  vanish on the next one. Keyed by `(athlete_id, activity_start_time_utc, split_index)` — not
  `activity.id` (unstable across a rebuild) — with a manually-added route getting its own
  independent `manual_order` sequence, since it has no FIT-derived `split_index` to key off of.
  `apply_bouldering_route_overrides` reapplies every correction and re-inserts every manual route
  at the end of every rebuild, mirroring `apply_sport_overrides` exactly.
- **Insight engine (`insights/`, Phase 8)**: rules-based, deterministic, no LLM involved (that's
  Phase 9's narrative layer, not this). Pure rule modules (`rules_efforts.py`,
  `rules_streaks.py`, `rules_pb.py`, `rules_load.py`, `rules_health.py`) take already-assembled
  plain-Python inputs and return `Insight` objects with no DB access of their own;
  `insights/engine.py::refresh_insights` does the one DB-touching assembly step and a full
  delete-and-reinsert into the `insight` table per athlete per run — same precedent as
  `fitness_daily_rollup`'s full recompute. Wired into every ingest entry point's touched-dates
  block, plus unconditionally into `garmin_connect.py`'s own daily-scheduled sync (no separate
  APScheduler job — that function already runs once/day regardless of new data, which is
  exactly what a "last 30 days"-style window needs). `GET /api/v1/insights` is a plain read,
  same rollup-mandate discipline as everything else. See `docs/adr/0012-phase-8-strava-merge-
  insights.md` for the exact windows/dimensions and the deliberately-adjustable thresholds.
- **`garmin_connect` safety rules, non-negotiable**: it never constructs a credentialed client
  automatically — `authenticate()` only loads the token store (`data/garmin_tokens/`), and if
  that fails, raises `GarminAuthRequired` rather than falling back to credentials. Credentials
  are only ever used in two places, both human-initiated and one-shot: `sync auth login` (CLI,
  MFA-capable) and `POST /settings/garmin/login` (the Settings page's own login form —
  MFA-*incapable* by deliberate choice, since bercy's API container runs multiple uvicorn
  workers with no shared memory, and Garmin's MFA resume needs one in-process client object
  across two requests; an MFA-challenged account falls back to the CLI). Both funnel through
  `adapters/garmin_connect.py::login_with_credentials`, one shared function, so this invariant
  has one implementation to audit, not two. A 429 (`GarminConnectTooManyRequestsError`) aborts
  the current run immediately — no retry, ever, anywhere. Garmin's SSO 429-locks per account
  with no recovery path; see `docs/adr/0003-phase-2-garmin-adapters.md` for how this is enforced
  structurally, not just by convention.
- **Scheduled workouts (`planned_workout`/`planned_workout_step`)**: the *one* place this app
  writes to a third-party account rather than only reading from it. Three sport tiers: **running**
  gets a full text syntax the athlete authors on the calendar (a real subset of intervals.icu's
  own workout-builder syntax — duration, a pace/HR/zone target, cadence, a simple `Nx` repeat
  block), parsed by two independent implementations kept in sync via one shared fixture table —
  `workout_syntax.py` (authoritative, server-side) and `workoutSyntax.ts` (instant client-side
  preview), same `gap.ts`/`gap.py` precedent — pushed via `GarminConnectAdapter.
  push_planned_workout` uploading a real Garmin `RunningWorkout`
  (`planned_workouts.py::build_running_workout`). **Yoga/bouldering** (`PLACEHOLDER_SPORTS`) are
  deliberately simpler — a name, a `duration_minutes`, and a display-only `scheduled_time`
  ("HH:MM", Garmin's own `schedule_workout()` has no time-of-day API at all), no structured
  syntax at all, pushed as a single no-target step for the whole duration
  (`build_placeholder_workout`; yoga gets a real Garmin sport type. Bouldering is really a
  rock-climbing sub-discipline — this app already knows that for *recorded* activities
  (`garmin_activity_summary.py`'s own `sport="rock_climbing"`/`sub_sport="bouldering"` pair) —
  but has no slot in the Workout Builder's own separate sport list at all, confirmed live against
  Garmin's real `GET /workout-service/workout/types` response (not the `garminconnect` package's
  own hardcoded `SportType` class): falls back to `SportType.OTHER`, a real constraint of that one
  Garmin subsystem, not a gap this app introduced). **hiit/strength_training**
  (`EXERCISE_SPORTS`) get real, named Garmin exercises picked from a bundled 1,527-exercise
  catalog (`garminconnect.exercises`, regenerated by `scripts/generate_exercise_catalog.py`) via
  the frontend's own picker (`ExerciseStepEditor.tsx`) — steps arrive already-structured, never
  parsed from text, and push through `build_exercise_workout`/`_build_exercise_step`
  (`weightValue` in grams despite `weight_kg`'s own kg storage unit, `category`/`exerciseName` as
  extra `ExecutableStep` fields via `ConfigDict(extra="allow")`, both live-verified against a
  real push + read-back). `build_workout_segment` (the repeat-group/step-order assembly, both
  hiit's factory and bouldering's) is shared across all three tiers via a `StepBuilder` callback
  rather than duplicated, and already handles any number of independent repeat groups plus
  standalone steps in one step list — the same mechanism running's own multi-`Nx`-block text
  syntax relies on. `ExerciseStepEditor.tsx`'s own authoring model exposes this as a flat
  top-level list of *items*, each either a standalone exercise/rest or a *set* (several
  exercises/rests repeated together N times, e.g. "3 rounds of squat, push-up, rest") — added
  after the first version only supported one repeat wrapping the entire workout, a frontend-only
  gap since the backend needed no change at all. The frontend's own exercise catalog is a
  dynamically-imported
  (code-split) asset, not a static import — a static import once pushed the app-shell bundle past
  vite-plugin-pwa's 2MB single-file precache limit and broke the production build. All three
  tiers push via the same `GarminConnectAdapter.push_planned_workout`, following the adapter's
  existing safety contract exactly (never a credentialed client of its own, rate-limited per
  call, abort-no-retry on 429) — via the generic `upload_workout(workout.to_dict())`, not the
  sport-specific `upload_running_workout`, which raises `TypeError` for anything but a real
  `RunningWorkout` (confirmed live by reading the check inside the installed `garminconnect`
  package itself; a real bug this project shipped once and fixed once yoga/bouldering exposed
  it). Push is automatic for anything due within `PERSEVERER_PLANNED_WORKOUT_PUSH_WINDOW_DAYS`
  (default 7) days (`worker/main.py::run_daily_workout_push`, its own daily schedule), plus a
  manual `POST /planned-workouts/{workout_id}/push` override (id-keyed, not date-keyed -- see the
  "more than one workout per day" bullet below). Running's own push is live-verified end
  to end (2026-09-03, a real push + read-back against the author's own Garmin account): a cadence
  target riding alongside a pace target on the same step (`_cadence_extra`, an
  initially-undocumented Garmin field) does reach and round-trip correctly — Garmin's server
  echoes it back as `workoutTargetTypeKey: "cadence"` on read. See
  `docs/adr/0015-scheduled-workouts.md`. A separate `planned_workout_step.comment` column (added
  later) attaches a freeform note to one specific step — for running, an inline trailing
  `# comment` token on that step's own `source_text` line (`workout_syntax.py`'s grammar, works
  on a repeat-marker `<N>x` line too, attaching to that block); for hiit/strength_training, a
  small input on that exercise/rest row in `ExerciseStepEditor.tsx` (and one for a "set"/group
  itself, mapping to its own repeat-marker row — lost if the set's repeat count stays at 1, since
  no marker row is ever emitted then). Yoga/bouldering get neither — they already have an
  equivalent via their own freeform `source_text`. Never parsed further, never pushed to Garmin.
  A second, separate `planned_workout.comment` column (added later still) is a general note for
  the *whole* workout, read before any step — running/hiit/strength_training only (yoga/
  bouldering again excluded, `source_text` already being freeform notes there). Also stored
  verbatim/never parsed, but pushed: it maps directly onto Garmin's own `description` field on
  every workout class (`garminconnect.workout.BaseWorkout`, confirmed by introspecting the
  installed package — every sport-specific workout subclasses it), so it needs no synthetic
  step. Threaded through the same fields `scheduled_time` already flows through end to end
  (`save_planned_workout` → `PlannedWorkoutIn`/`Out`/`RecurringWorkoutIn` → the frontend's
  `PlannedWorkoutFields`/`RecurringPlannedWorkoutInput` → the one `WorkoutEditForm`), and
  included (ahead of the existing per-sport text) in both the iCal feed's event description
  (`calendar_feed.py`) and the weekly email's coming-workouts section (`email_reports.py`).
  **Revision**: a day originally held at most one `planned_workout` row (`UniqueConstraint` on
  `(athlete_id, local_date)`) -- lifted once the athlete asked to schedule more than one workout
  on the same day (e.g. a morning run plus an evening strength session). Every workout is now
  addressed by its own `id`, never by date alone: `POST /planned-workouts` creates, `GET/PUT/
  DELETE /planned-workouts/{workout_id}` and `POST /planned-workouts/{workout_id}/push` act on one
  workout, and `GET /planned-workouts/by-date/{local_date}` lists every workout on one date
  (sorted by `scheduled_time`, nulls last, then `id`) -- the old date-keyed single-object GET/PUT/
  DELETE/push routes are gone. `POST /planned-workouts/recurring` no longer skips a date that
  already has a workout scheduled; it always creates alongside it. `ScheduleWorkoutForm.tsx`
  renders a list of the date's own workouts (each with its own Edit/Push/Copy/Delete) plus an
  "Add another workout" affordance, splitting what used to be one single-workout form into an
  outer list view and an inner create-or-edit form component.
  **Compliance, sport by sport (Week view)**: a new "Compliance" card on `WeekView.tsx`, one
  `StatTile` per sport with at least one scheduled workout that week — `{sport} compliance`,
  e.g. `Running compliance: 67% (2 of 3 done)`. Deliberately count-based (completed workouts /
  scheduled workouts), not distance- or load-weighted: count is the one signal every sport tier
  carries identically, since `planned_workout_stats.py`'s own distance/load estimate is
  running-only (`estimated_distance_m`/`estimated_load` are always null for yoga/bouldering/
  hiit/strength_training). Scoped to `local_date <= today` (the browser-local date `WeekView.tsx`
  already computes for its "today" column highlight) so a workout later in the same week that
  hasn't happened yet doesn't drag down a week still in progress — a fully future week (every
  date beyond today) naturally produces no compliance entries at all, needing no separate case,
  and the whole card is omitted, not shown empty, in that case. Backed by `GET /planned-workouts?
  start_date=&end_date=` (`PlannedWorkoutListItemOut`, the same summary-list endpoint the Month
  view's own per-day grid indicator already uses) gaining two more fields, `completed_at` and
  `matched_activity_id` — the one bulk-range call this needs, rather than the per-day
  `usePlannedWorkoutsForDate` hook `WeekDayPlannedWorkouts` calls 7 times for its own full-detail
  per-day rendering.
  **Revision: matched against recorded activities, not manual-only** — the first version counted
  only `completed_at` (the athlete's own explicit "I did this" marker, still the only thing
  `POST .../complete`/`.../uncomplete` ever write), which meant a workout Garmin had already
  confirmed still showed as not-done until the athlete separately clicked "Mark as done" — real
  friction reported directly against a synced yoga session that already existed as a recorded
  `activity` row. `planned_workouts.py::matching_activity_id` (backed by
  `activities_by_local_date`, one query per request for the whole date range, not one per
  workout — the same "fetch once, match in Python" precedent `race_readiness.py`'s own weekly
  queries already establish) now supplies a second, read-time-only, never-persisted signal:
  whether a same-day recorded activity's own `(sport, sub_sport)` plausibly satisfies the planned
  workout's sport tier (`activity_matches_planned_sport` — running via `merge/engine.py`'s own
  `sport_family()`, so a trail/treadmill run still satisfies a plain "running" plan; yoga/
  strength_training/hiit each checked against FIT's generic `"training"` container sport with the
  real discipline in `sub_sport`, confirmed against `garmin_activity_summary.py`'s own
  `GARMIN_ACTIVITY_TYPE_MAP`, alongside whichever literal top-level sport that discipline can also
  arrive as). Exposed as `matched_activity_id` (`PlannedWorkoutListItemOut`/`PlannedWorkoutOut`),
  never written back to `completed_at` and never overriding what the athlete explicitly set there
  — `db/schema.py::planned_workout`'s own docstring documents the distinction. Compliance (and
  the Day/Month view's own "Done" badge, now labeled "Done (via Garmin)" when only the match is
  present, with a tooltip explaining why) treats `completed_at != null OR matched_activity_id !=
  null` as done; the "Mark as done"/"Mark as not done" toggle itself still only ever reads
  `completed_at` alone, deliberately — basing it on the combined signal would let a matched-only
  workout's button read "Mark as not done" while actually being unable to clear the match, a
  promise the click can't keep.
  **Revision: the `lap` duration keyword** — a running step's duration may be `lap` instead of a
  time or distance, ending it on Garmin's own `ConditionType.LAP_BUTTON` when the athlete
  physically presses the watch's lap button, rather than any GPS-derived threshold. The athlete's
  own concrete failure: four downhill long runs are out-and-backs whose turnaround is a real
  landmark (a dam), not an exact distance, and GPS on that trail isn't accurate enough — authored
  as `5km` steps, the watch swapped from the climb's HR target to the descent's pace target while
  still climbing, exactly what the climb's own HR cap exists to prevent. `lap` may be followed by
  an ordinary duration token (`lap 5km`, `lap 40m`) kept purely as a calendar-side estimate — no
  migration needed, since `duration_type` was already a free string and both existing estimate
  helpers already read `duration_time_s`/`duration_distance_m` without branching on it — and
  **never** forwarded to Garmin as an end condition (`endConditionValue` stays `None` for a
  `lap_button` step; if it leaked through, the step would advance at 5 km and the whole feature
  would be pointless). `LAP_BUTTON_CONDITION_ID` is read via `getattr`, not a plain attribute: the
  installed `garminconnect` renumbers `ConditionType` between releases (an older release had no
  `LAP_BUTTON` member at all and numbered `DISTANCE`/`HEART_RATE` differently), so a future bump
  that drops or renames the member degrades to the known wire id instead of raising
  `AttributeError` mid-push. A real gap this left behind, caught only once the athlete tried to
  use the feature ("you did not add it to the step options"): `StepBuilderModal.tsx` (the "Add
  step" GUI wizard, decision 5 of the ADR) never got a matching "Lap button" option in its
  duration-type dropdown — its "Time or distance?" selector only offered minutes/seconds/km/
  meters and *required* a numeric value, so `lap` could only be authored by typing the syntax by
  hand until fixed. See `docs/adr/0015-scheduled-workouts.md` decision 13 for the full reasoning.
  **Revision: the Intervals table and the Pace-panel workout overlay now tolerate a device's own
  autolap setting splitting one planned step into several recorded laps** — a real reported bug:
  a watch's distance-based autolap (independent of the pushed workout's own step boundaries) split
  a planned 10-minute step into a 6:10 lap plus a 3:50 lap, and both `ActivityDetailPage.tsx`'s
  Intervals table and `ActivityCharts.tsx`'s `workoutBands` overlay had always assumed a strict
  `laps[i] <-> expandedSteps[i]` positional zip (one lap per executed step, confirmed against a
  real structured-workout FIT file, which normally holds) — the extra lap silently shifted every
  following lap's expected-step association by one for the rest of the activity.
  `workoutSteps.ts::alignLapsToWorkoutSteps` replaces the raw positional lookup in both places:
  it greedily accumulates consecutive laps' own duration/distance against the *current* expected
  step's own target until 90% of it is reached (generous enough to absorb ordinary GPS/timer noise
  on a genuine one-lap-per-step match, strict enough that a lap barely a third of the way into a
  step never advances early) before moving to the next step — so two or more laps that together
  complete one step are both correctly matched to it. A step with no time/distance target to
  measure against (an open step, or `lap_button`, which by definition ends exactly on a lap
  boundary already) always advances after exactly one lap, preserving the original assumption for
  those. The chart overlay additionally merges consecutive laps aligned to the same step back into
  one band spanning all of them, rather than drawing a separate (and now-misaligned) band per raw
  lap.
  **Revision: the Pace and GAP panels' Y-axis is reversed, faster on top** — reported directly:
  the per-second Pace/GAP charts plotted a plain ascending axis (the default for every other
  panel here — higher HR/cadence/power at the top reads naturally), but pace/GAP are min/km,
  where a *lower* number is the *faster* effort, so the un-reversed axis put the slowest pace at
  the top and the fastest at the bottom — backwards from how a runner reads a pace chart, and from
  Garmin Connect's own convention. `ActivityCharts.tsx`'s Y-axis gets `reversed={panel.unit ===
  "/km"}` (true for exactly the Pace and GAP panels, both denominated in min/km — every other
  panel's own unit is untouched) — this also makes the pre-existing `workoutBands` target overlay
  (the grey step-fill shading "at or faster than the prescribed pace") finally match its own
  long-standing code comment, which had always described `domainMin` (the fastest recorded pace)
  as sitting at "the top edge" even though nothing had actually reversed the axis to make that
  true until now. The public share page's own hand-rolled SVG pace chart
  (`sharing.py::_svg_series_chart`, `pace_format=True`) had the identical un-reversed default and
  got the identical fix (`y_at`'s own fraction inverted, and the two axis min/max labels swapped
  to match) — this page exists specifically to mirror the authenticated app's own activity-detail
  charts, so leaving its pace chart's own orientation stale would have reintroduced the same bug
  on the one surface this project explicitly built to avoid a second, drifting implementation.
- **Exercise library page (`/exercises`, `ExerciseLibraryPage.tsx`)**: a browsable reference for
  every exercise the hiit/strength_training picker's catalog supports — 47 categories collapsed
  by default (native `<details>`, same convention as `ActivitySourcesPanel.tsx`'s own "Why these
  were merged"), each expanding to its exercise list with a photo (click to enlarge, via
  `Modal.tsx`'s own `panelClassName` lightbox variant), description, and muscle groups. Sourced
  by `scripts/generate_exercise_library.py` into `frontend/src/data/exerciseLibrary.json`
  (dynamically imported, like `ExerciseStepEditor.tsx`'s own catalog) — three real, honestly
  distinguished tiers rather than one blended guess, after an earlier draft's category-level
  fallback images repeated the same photo across dozens of exercises and the user rejected it
  outright: (1) ~160 exercises Garmin itself calls "detailed" have a real Garmin-authored photo
  and description, confirmed live against `connect.garmin.com`'s public (no-auth) exercise-data
  endpoint; (2) ~200-300 more get a photo + instructions matched by name against free-exercise-db
  (public domain) — reuse of one photo across several Garmin exercises is fine when they're
  genuinely the same base exercise once equipment words are stripped (not position/tempo/
  laterality words, which stay — a decline push-up is never treated as the plain one), with the
  weaker fuzzy-similarity tier capped to a handful of claimants per photo so it can't reproduce
  the original complaint (unbounded reuse there once let one photo stand in for 50+ only
  loosely-related variants, and separately let one bad match slip a chest-press photo onto an
  overhead-press exercise before the threshold was tightened); the rest fall through to
  (3) muscle-group text only (still real data, from Garmin's own master exercise list, just no
  photo) rather than a misleading stand-in. `garmin_url` is set **only for tier 1** — confirmed
  live (2026-09, in the user's own logged-in Chrome) that Garmin's own exercise page hangs on an
  infinite loading spinner for anything but a detailed exercise, even signed in, while a detailed
  one loads its real video/steps/tips correctly; linking every exercise there regardless of tier
  was the original mistake this corrected.
  **Revision: step-by-step instructions, and a small hand-curated set for tier 3's own generic
  entries** — the user asked for detailed steps (plus pictures/video) for the ~1,150 tier-3
  exercises by searching exrx.net/darebee.com, but checking both sites' actual terms live first
  (not assumed) ruled that out at this catalog's scale: exrx.net's own
  [Link Policy](https://exrx.net/Notes/LinkGuidelines) reserves its exercise directory content and
  caps even bare links at under 75% of any one subdirectory's exercises, and darebee.com licenses
  its content CC BY-NC-ND (No Derivatives) — reformatting either into this catalog's JSON isn't
  something either site's terms permit at anywhere near this scale. What shipped instead, after
  confirming with the user: (1) tier 2's free-exercise-db `instructions` are now kept as a real
  `steps` array (`ExerciseLibraryEntry.steps`) instead of being flattened into one `description`
  paragraph — free, safe, public-domain data this catalog already had; `description` stays `null`
  for tier 2 so the same content isn't shown twice. Lowering the fuzzy-match jaccard threshold
  below 0.7 to close more of the tier-3 gap was tried and rejected — checked against the real
  ~1,527-exercise catalog (never tune this kind of heuristic on a few examples alone), it
  reintroduces genuinely bad matches (e.g. "One-arm Push-up" fuzzy-matching "One Arm Chin-Up" at
  0.6) — but the reuse cap had real headroom, since at 0.7 only 39 exercises total ever clear the
  bar for a fuzzy match; raising `JACCARD_REUSE_CAP` from 3 to 5 rescues 3 more of those
  already-qualifying matches with no quality cost. (2) A small, deliberately bounded
  `HAND_CURATED_STEPS` dict in `generate_exercise_library.py` adds originally-written steps (my
  own wording, general exercise-science knowledge, never copied from any one site) plus one
  external `reference_url`/`reference_label` link for further reading, but ONLY for the dozen
  tier-3 exercises that are genuinely Garmin's own generic "family" entry for a whole category
  (name equals categoryLabel — "Row", "Battle Rope", "Lateral Raise", "Leg Raise",
  "Hyperextension", "Chop", "Carry", "Hip Swing", "Sledge Hammer", "Sled", "Stair Stepper",
  "Ladder" — a real single technique) — never one of the many differently-named, visually-distinct
  variants inside that category (a decline push-up, a banded row, a single-leg hip raise), which
  stay honest muscle-group-only entries exactly as before. A handful of other name-equals-category
  entries exist (Cardio, Total Body, Olympic Lift, Suspension, Core, Plyo, Warm-up, etc.) but were
  deliberately left uncurated — they're genuinely a training modality/goal, not one technique, and
  writing steps for them would be dishonest the same way a copied description would be. A plain
  hyperlink (unlike copying content or images) isn't something any site's terms restrict at this
  small a scale.
  **Revision: composed step-by-step instructions for ~800 more tier-3 exercises, from real
  bases, with a careful pass to exclude/correct where Garmin's own categorization is wrong** —
  after the 12-exercise pass above, asked to go further ("step by step for every exercise"). Of
  the ~1,150 tier-3 exercises, roughly 800 turned out to be named variants inside a category
  that already has a real base movement (tier 1's own Push-up/Squat/Plank/Crunch/Lunge/Hip
  Raise/Pull-up/Sit-up, tier 2's own Bench Press/Calf Raise/Curl/Deadlift/Leg Curl/Shoulder
  Press/Shrug/Triceps Extension, or one of the 12 hand-curated ones) — e.g. "Decline Push-up" is
  a named variant of "Push-up". `generate_exercise_library.py` composes each of these from its
  base's own real steps (`BASE_STEPS_OVERRIDE`, originally written for the tier-1 movements that
  only had Garmin's prose description before, applied ONLY to the literal base entry itself —
  not to every tier-1 exercise sharing that category, a real bug caught before shipping: an
  earlier version slapped generic "hold a plank" steps onto Garmin's own correctly-described
  "Mountain Climber," which happens to share PLANK's category) plus honest, specific notes on
  exactly what that variant's name adds beyond the base (`MODIFIER_NOTES`, ~60 phrases like
  "single-leg," "Swiss ball," "kneeling," each describing a real, verified technique difference,
  never a generic "this is a variant" filler) — checked longest-phrase-first against the words
  the variant's name adds beyond its base name. **Reading every compositional category's full
  exercise list (not assuming from the name pattern) surfaced real Garmin miscategorization
  that would have made composition actively wrong**: some categories are grab-bags (Lateral
  Raise contains "Ring Muscle-up," "Weighted Rope Climb," and "Calorie Row," none of them a
  raise), some words mean something different depending on the movement family ("reverse" means
  step-backward for a lunge but a wholly different leg-driven exercise for a hip raise/crunch,
  "front raise" filed under Shoulder Press is not a press at all), and some barbell moves are
  genuinely different, technical lifts sharing a category with a basic one (several
  Olympic-lift-derived snatches/cleans filed under Squat, where "just squat down" would actively
  mislead). `EXCLUDE_FROM_COMPOSITION` (~30 exercises) leaves these with only the honest
  muscle-group description, same as any exercise with no real base, rather than composing wrong
  instructions; `SUB_BASE_OVERRIDES` (~27 exercises: reverse crunch, reverse hip raise, lat
  pulldown, good morning, dip) gives each of these real families its own originally-written
  base instead, since they're common enough to deserve real content, just not the category's own
  base. The Lunge base itself needed a direct fix, not just a note: its steps said "step
  forward," which is flatly wrong for a reverse lunge (19 exercises) — made direction-neutral
  ("step into a lunge position") instead, with a `CATEGORY_SCOPED_NOTES["LUNGE"]` entry
  clarifying the direction for reverse/walking/side variants specifically, since a bare
  "reverse"/"side" note would be wrong in the other families sharing those exact words.
  End state: 161 tier-1 + 213 tier-2 + 770 tier-3 (15 hand-curated + 755 composed) = 1,144 of
  1,527 exercises (75%) now carry real step-by-step instructions, up from 225 (12+213) before
  this pass; the remaining 383 tier-3 exercises are sports/cardio/mobility categories with no
  single describable base movement (Warm-up stretches, Core, Hip/Shoulder Stability, Plyo,
  Cardio machines, Olympic Lift, Sandbag, Total Body) and keep their honest muscle-group-only
  description, deliberately not forced through a mismatched composer. The dynamically-imported
  `exerciseLibrary.json` chunk grew from ~865KB to ~1.39MB gzip-uncompressed — still comfortably
  under vite-plugin-pwa's 2MB single-file precache limit, confirmed by a real build, but worth
  tracking if more content is added later.
- **Share links (`share_link`, `sharing.py`, `api/routers/share.py`)**: an athlete-issued token
  granting unauthenticated, read-only access to one activity or one summary period. `GET
  /share/{token}` is public by omission, same exemption mechanism `/healthz`/`/version`/
  `/auth/login` already use — a route is "public" purely by never taking
  `Depends(require_api_key)`. Both pages are server-rendered HTML (hand-escaped f-strings, no
  template engine) built to mirror the *authenticated* frontend's own activity-detail and
  period-summary views as closely as a public page reasonably can — full stat sections
  (distance/HR/elevation/power/training-effect/running-dynamics/temperature/respiration), the
  Intervals (laps) table with server-computed GAP plus (when the activity has a planned workout)
  Interval/"Exp. pace or dist."/Expected-pace columns (the header text is the athlete's own
  verbatim wording, deliberately terser than the authenticated app's own column label) —
  `_expand_workout_steps` is a
  straight Python port of `frontend/src/workoutSteps.ts::expandWorkoutSteps` (unrolling
  `repeat_until_steps_cmplt` groups so `laps[i] <-> expanded[i]` positionally, same alignment the
  real app relies on), per-second interactive charts, and — for bouldering — the routes-by-grade
  chart and routes table. Deliberately excludes anything health-related (weight/HRV/sleep — this
  app's own existing stance is that health metrics aren't vetted for public exposure) and the
  context/comparison sections (percentile-rank/similar-activity tables, already a second
  aggregate query each — not worth exposing to anonymous traffic). Weather and time-in-zone,
  once excluded for the same "don't build a second implementation" reasoning, are shown after
  all: weather via `_cached_weather`, a plain read-only `SELECT` mirroring `weather.py::
  _read_cached`'s own query (duplicated, not imported — that function is module-private) that
  never calls the live fetch path (`get_or_fetch_activity_weather`), so a public URL still can't
  trigger a paid API call — an activity nothing ever fetched weather for just shows no Weather
  section; time-in-zone via the *device-reported* fallback only (`fit.time_in_zone.*` metrics,
  already loaded), never the athlete's-own-configured-zones stream computation.
  `.time-in-zone__fill` needs an explicit `display: block` in this page's own `<style>` block —
  the exact same bug `TimeInZoneChart.tsx`'s own CSS already hit and documented once: a bare
  `<span>` is `display: inline` by default, which ignores a percentage `width` entirely, so the
  zone bars rendered as empty (0×0) tracks despite the correct percentage in their inline style,
  confirmed live via `getBoundingClientRect()`, not just by reading the CSS. The per-second
  charts (`_svg_series_chart`) now carry real axis units and are wrapped for
  `_CHART_HOVER_SCRIPT` — one shared script, emitted once, reading each chart's own sibling
  `<script type="application/json">` blob to show a crosshair + exact value/elapsed-time tooltip
  on hover/touch — because a static chart image with no way to read a value off it wasn't enough.
  Every color on both share pages (stat tiles, chart lines, badges, page background/text) now
  comes from the exact same tone tokens `frontend/src/styles/theme.css` defines (dark default,
  `prefers-color-scheme: light` override — this page has no JS theme toggle to persist an
  explicit choice, so it only ever follows the visitor's OS setting) — CSS custom properties
  resolve fine as literal SVG presentation-attribute values (`stroke="var(--color-heart-rate)"`)
  since this is inline SVG in the same HTML document, not a standalone `.svg` file. The activity
  page's own stat tiles (distance/HR/elevation/power/training-effect/running-dynamics/temperature/
  respiration, bouldering's time-and-calories) go one step further, reproducing
  `StatTile.tsx`'s exact icon-chip + tone mechanism rather than just its colors: `st(label,
  value, icon, tone)` builds a `_Stat`, `_stats_grid_iconed` renders each as `<div class="stat
  tone-{tone}">` with an `<span class="icon-chip">` wrapping an inlined copy of the matching
  hand-rolled icon's raw SVG path data from `Icon.tsx` (`_ICON_PATHS`/`_icon_svg` — copied
  directly rather than shared via a symbol/sprite, since this is a one-off static page with no
  other icon reuse need); `.icon-chip`'s `color`/`background` read the same `--tone` custom
  property `.tone-*` sets, `color-mix(in srgb, var(--tone) 15%, transparent)` and all, matching
  `layout.css`'s own rule byte-for-byte. Every icon/tone pairing was verified against
  `ActivityStatsGrid.tsx`'s real per-stat assignments, not guessed from the label text — the one
  deliberate exception is Fueling (`ActivityFueling.tsx`), which has no `StatTile`/icon usage at
  all in the authenticated app either, so its share-page section stays on the older, plain
  `_stats_grid`/tuple rendering rather than inventing a mapping that doesn't exist upstream. The
  period share's
  sport breakdown covers Running, Hiking, Climbing (bouldering, with the same grade chart as the
  activity page, aggregated across every session in range), and Fitness (hiit/strength_training —
  note `sport_family()` in `merge/engine.py` has no single bucket for either of these, so this
  page unions `sport_family(sport)=="strength"` with the literal `sport=="hiit"` fallback itself)
  — Fitness & Form (CTL/ATL) stays, health stays excluded. Every one of these sections, plus the
  top-level totals, now carries the same icon-chip + tone treatment as the activity page, matched
  against each section's *own* real authenticated-app component rather than one blanket mapping:
  the top-level stats mirror `PeriodStatsCard.tsx` (Month/Year/All-time's real card — Distance/
  Longest streak/Total elevation as heroes plus Activities/Moving time/Active days/Longest
  activity/Busiest week-or-month-or-year/Favorite day/Average distance-time-speed/Average+Max
  heart rate; `earliestDateForKeys`-style per-field Python ports of `runningStats.ts`/
  `yearStats.ts`'s streak/weekday/busiest/average logic feed these, straight from the same
  `activity`+`activity_metric` query this function already runs, plus one extra query resolving
  avg/max heart rate per activity via `AVG_HR_METRIC_KEYS`/`MAX_HR_METRIC_KEYS`, already imported
  here for the activity page's own HR stats). `WeekView.tsx` has a materially different "Week
  stats" card with no streak/busiest/favorite-day/averages concept at all for a single week, so a
  **week** share deliberately keeps the original six-stat set (now iconed, not expanded) rather
  than inventing stats the real week view doesn't have. Running/Hiking/Climbing's own tiles are
  tuned to `RunningStats.tsx`/`HikeStatsCard.tsx`/`ClimbingStatsCard.tsx` respectively — Climbing
  here is deliberately all-`"elevation"`, a different mapping than the activity page's own single-
  climb section (`ActivityStatsGrid.tsx`), because they're two different real components with
  their own real mappings, not one shared source of truth. `Activities by type` is new: a colored,
  iconed horizontal-bar breakdown by sport (count and time together, since a static page can't
  offer `PeriodStatsCard`'s own live count/time pie toggle) — `_SPORT_ICON_PATHS` holds ten
  pictogram paths copied verbatim from the installed `@phosphor-icons/react` package's own
  `dist/defs/<Name>.es.js` "fill" entries (MIT), since `Icon.tsx`'s sport icons are real Phosphor
  components, not the hand-rolled stroke glyphs every other icon on this page reproduces — a
  second `.icon.icon--filled` CSS override (`fill: currentColor; stroke: none`) renders them,
  matching `layout.css`'s own identically-named rule.

  `RunningStats.tsx`'s own distance-bucket bar chart, trailing-window line chart, calendar
  heatmap, and personal-records table are now ported too (Month/Year/All-time only — see below)
  — a second, later pass once "match everything, be precise" made clear that "Running" meaning
  just four stat tiles wasn't actually close enough. Exact `sport == "running"`/`"hiking"`
  matching (not `sport_family()`) is used throughout, confirmed against `GET /activities?sport=`
  itself doing a plain `==`, not a family grouping — trail_running/track_running and walking/
  snowshoeing/alpine_skiing are real, deliberate exclusions from these two sections, not an
  oversight; this corrected a latent inaccuracy in the *original*, four-stat version of this
  section from earlier the same session. Which of the three real RunningStats "modes" a period
  gets is derived exactly like the real component (`spanDays <= 31` / `> 366` / else) from the
  *overall* activity date range (every sport, matching AllTimeView.tsx passing
  its own all-sport `start`/`end` into RunningStats, not a running-only range) — Month gets daily
  buckets/a 7-day trailing window/a one-row "Daily distance" strip; Year gets monthly buckets/
  90-day trailing/a "Daily distance" week-grid (`.running-heatmap__grid`, one column per week,
  one row per weekday, month-boundary dividers); All-time gets yearly buckets/365-day trailing/a
  "Weekly distance" year-rows grid (one row per calendar year). The heatmap itself needed no SVG
  at all — `running-stats.css`'s own encoding is pure CSS (`color-mix()` for an ordinary day's
  intensity wash, `conic-gradient()` for a long run's proportional-circle overlay, a `:hover`-
  revealed absolutely-positioned tooltip) — so this page reuses the *exact* same class names and
  rules rather than an SVG approximation of them; the one real deviation is a plain `<span>`
  where the authenticated app has a `<Link>`, since there's no public per-day/week view for an
  anonymous visitor to navigate to. Personal records (`_personal_records`, a straight port of
  `personalRecords()`'s own 0.9x-1.3x-tolerance-band approach) get an all-time-PR badge for
  Month/Year (a second, unbounded `sport == "running"` query, mirroring `useAllActivities`) but
  not All-time, matching `AllTimeView.tsx` itself never passing `allTimeRecords` at all (every
  record there already *is* the all-time one). `HikeStatsCard.tsx`'s featured-hike cards
  (Longest hike / by time / most elevation gain / highest point reached, each only if it's a
  genuinely different hike from ones already featured) resolve their location name via
  `geocoding.py::read_cached_location` — the same cache-only guarantee weather already has,
  confirmed live: an uncached hike's card simply omits the location rather than triggering a
  live Nominatim lookup. `YearView.tsx`'s own 12-tile month grid closes out the page, reusing the
  exact `period_rollup` query the "Distance by month" bar chart above it already runs (extended
  for two more columns) rather than a second query for the same rows. hiit/strength_training
  keeps the older plain `_stats_grid` (no confirmed icon/tone mapping — the authenticated app has
  no dedicated card for these sports at all, only the generic totals and the Activities-by-type
  breakdown), same "don't invent a mapping that doesn't exist upstream" reasoning as Fueling
  above; the day-by-day calendar grid (WeekView/MonthView's own per-day activity list, a
  different kind of view than a summary page) stays out of scope. The route map is the one
  deliberate
  exception to this file's otherwise zero-JS pages: a real interactive MapLibre GL map (CARTO's
  Positron vector basemap, same style the authenticated frontend's `CartoBasemapLayer.tsx` uses),
  loaded from a CDN as a `type="module"` script — MapLibre v6 shipped no UMD/global build at all
  (confirmed against the real published package: `dist/` only has `.mjs` files), so this needs
  named ESM imports and an explicit `setWorkerUrl()` pointed at the CDN's own worker file, not a
  plain `<script src=...>` global. A Play/Pause route-playback marker rides along on top of the
  same map, driven by the activity's own `lat`/`lon` Parquet stream channels (yes, GPS position
  really is stored per-sample, one real Parquet column each, not just baked into `route_geom`'s
  polyline — confirmed directly against `fit/parser.py`) at the same "low" tier (200 points) the
  charts already fetch, so no second, heavier stream read is needed just for a smooth-looking
  marker. Replay always takes a fixed 20 real seconds regardless of the activity's own actual
  duration — a deliberately simplified, non-draggable version of `ActivityRouteMap.tsx`'s own
  scrubber. `PERSEVERER_CARTO_API_KEY` is read by the API's own `Settings`
  now too (previously frontend-only) — not actually a larger exposure, since it's the same
  client-embeddable key the authenticated frontend already ships to every visitor's browser.
  `PERSEVERER_PUBLIC_BASE_URL` must be the athlete-facing origin (bercy: the frontend's own
  `:443` reverse-proxy rule, which nginx forwards `/share/*` from to the api container — see
  `docker/nginx.conf`), **not** `PERSEVERER_API_BASE_URL` — those are two different origins
  whenever the reverse proxy fronts api/frontend on different ports. **bercy itself has since
  migrated to single-origin routing** (docs/DEPLOY.md's "Single-origin routing" section):
  `nginx.conf` also proxies `/api/`/`/mcp`, so `PERSEVERER_API_BASE_URL` and
  `PERSEVERER_PUBLIC_BASE_URL` are now the same origin there, and the old `:444`/`:81` DSM rule
  and UniFi port-forward are gone. Both env vars are kept working either way — a deployment that
  still fronts api/frontend on two separate origins needs `PERSEVERER_PUBLIC_BASE_URL` set
  explicitly (an unset value falls back to the *incoming* request's own base URL, which is the
  api's own port when the create-share call arrives there, not the origin a browser should
  open); single-origin routing needs neither var to differ, since that fallback is already
  correct once everything shares one origin.
- **Calendar feed (`calendar_feed.py`, `api/routers/calendar_feed.py`)**: a Google-Calendar-
  subscribable public iCalendar (.ics) feed of the athlete's own `planned_workout` calendar —
  deliberately a *parallel* mechanism to `share_link` above, not a third `target_type` grafted onto
  it: `share_link.target_type` is a closed two-way branch (`"activity"`|`"period"`) with a bespoke
  `target_id` encoding per kind, and the whole file only ever emits `HTMLResponse`; a calendar feed
  is one continuously-regenerated view (not one activity/period) needing `text/calendar`, not HTML.
  Instead mirrors `athlete.api_key_hash`/`api_key_created_at`'s own shape — two new nullable
  columns directly on `athlete` (`calendar_feed_token_hash`/`calendar_feed_created_at`), one
  standing secret per athlete, replace-on-rotate, not a growing history of one-off tokens.
  `GET /share/calendar/{token}.ics` (mounted with `prefix="/share"`) reuses the exact same
  `location /share/` nginx prefix rule `share.py`'s own docstring describes — zero infra change.
  Never includes completed activities, only the athlete's own authored planned workouts — Google
  polls a subscribed feed roughly every 8-24h, not live, so this is rebuilt fresh on every request
  (no caching, no rollup precedent needed — `planned_workout` has none of its own either). Event
  rendering per sport tier (`planned_workouts.py::EXERCISE_SPORTS`/`PLACEHOLDER_SPORTS`):
  running/yoga/bouldering already have a human-readable `source_text` (workout syntax or freeform
  notes respectively), used verbatim as the event `DESCRIPTION`; hiit/strength_training has no
  `source_text` at all (steps arrive already-structured, never parsed from text — ADR 0015), so a
  small purpose-built renderer lists each real exercise/rest step instead — deliberately not a
  reuse of `workout_syntax.py::steps_to_source_text`, which is shaped for *recorded* pace-only
  steps, a different domain — each step's own `comment` (see the scheduled-workouts bullet above)
  is appended to that step's own line here. Running/yoga/bouldering need no code of their own for
  this at all: an inline `# comment` the athlete typed is already part of `source_text`'s raw
  text, flowing straight through to `DESCRIPTION` verbatim. A workout with `scheduled_time` set
  becomes a timed event using the
  athlete's own stored `athlete.timezone` (`zoneinfo.ZoneInfo`, real VTIMEZONE block via
  `icalendar`'s own `add_missing_timezones()` — verified empirically against the installed
  version rather than assumed); one without becomes an honest all-day event rather than a guessed
  time. `GET/POST/DELETE /settings/calendar-feed` (authenticated, alongside hr-zones/running-load
  in `settings.py`) publish/rotate/unpublish; the frontend's `CalendarFeedCard.tsx` mirrors
  `RebuildCard.tsx`'s status-query-plus-mutation shape, showing the fresh URL inline (via the same
  `share-button__url-row` markup `ShareButton.tsx` already uses) only once per publish/rotate,
  never re-shown afterward — same "raw token never recoverable again" posture as every other
  hashed secret in this codebase.
- **Weekly / monthly email reports (`email_reports.py`, `email_delivery.py`)**: opt-in training
  digests emailed to the athlete's own `athlete.email` — **weekly** (Sunday 18:00 local: the
  Mon–Sun week just ended, totals + per-sport split, plus the coming week's planned workouts) and
  **monthly** (month's last day 18:00: that month's totals only). Two `APScheduler` jobs in
  `worker/main.py` (`run_weekly_email_report`/`run_monthly_email_report`), resolved against
  `schedule_timezone` like the sync/backup/push jobs. Opt-in is per-athlete via
  `athlete_email_report_config` (one row, `weekly_enabled`/`monthly_enabled`, both default False —
  same one-row-upsert shape as `athlete_hr_zone_config`); no row means no emails. The SMTP relay
  is deployment-global (`PERSEVERER_SMTP_*`, `config.py::smtp_configured`), read by both the
  `worker` (scheduled sends) and `api` (the "send test email" button) containers — all-or-nothing
  optional, same log-and-skip contract as the Eufy/backup credentials. `smtp_security` picks the
  wire mode: `starttls` (port 587), `ssl` (port 465), or `none`; port 587 is mail-submission and
  is STARTTLS, not implicit TLS. Totals come from `period_rollup` (the sanctioned aggregate); the
  per-sport split is one extra bounded `activity` query; coming-week running workouts reuse
  `planned_workout_stats.estimate_workout` for the same distance/duration/load line the calendar
  shows. HTML is deliberately email-client-safe — one inline-styled table, light palette, no
  `<style>`, no external resources — with a plaintext alternative. A send reads the local DB
  (newest data = that morning's 04:15 sync), so the send day's own activities may lag; the footer
  says so. `GET/PUT /settings/email-reports` (+ read-only `smtp_configured`/`recipient_email`
  context) and `POST /settings/email-reports/test` (sends the current weekly report now — 400 if
  unconfigured, 502 on send failure); frontend `EmailReportsCard.tsx` under Settings → External
  tools. The weekly report additionally carries a per-day running-distance bar chart, the week's
  average running pace, and a per-day steps bar chart — all Mon–Sun of the week just ended (the
  monthly report is unchanged, totals-only). Both bar charts (`_bar_rows`/`_bar`) are one row per
  weekday with a horizontal bar sized to that day's value against the week's own max: a real,
  confirmed-live rendering trap here is that a percentage-width `<div>`, and even a percentage-
  width nested `<table width="100%">`, both collapse to **0px rendered width** when their
  containing `<td>` (the row's own middle column) has no width of its own for the outer table's
  auto layout to resolve a percentage against — a `&nbsp;`-only cell gives that algorithm no real
  content to size from, so the whole column collapses and every percentage inside it becomes 0.
  Only literal **pixel** widths (`_BAR_TRACK_PX`, the containing `<td>` and the nested bar table
  both fixed to the same pixel value) render correctly regardless of the parent's own auto-layout
  decisions — this class of email/table layout bug is invisible to a plain string assertion on
  the rendered HTML (the "sensible-looking" percentage markup was there in both broken versions
  too); it only showed up rendering the actual HTML and reading `getBoundingClientRect()`, the
  same way this codebase's own `.time-in-zone__fill` bug (share pages) was originally caught.
  Average running pace (`_running_avg_pace_s_per_km`) and both daily series
  (`_running_distance_by_day`/`_steps_by_day`) are exact `sport == "running"` matches / the same
  `LOGICAL_METRICS["steps"]` alias-merge `api/routers/health.py` uses (duplicated as
  `_STEPS_ALIASES` rather than imported, so this module doesn't depend on the API layer — same
  precedent `insights/engine.py::_RESTING_HR_ALIASES` already established). The "By sport" table's
  own grouping was also a real bug this pass fixed: a recorded yoga/strength/breathwork session is
  stored `sport="training"`/`sub_sport="<real type>"` (Garmin's FIT taxonomy uses "training" as a
  generic container for all three), so grouping by the raw `sport` column showed "Training"
  instead of "Yoga" — `_sport_breakdown` now groups by `_display_sport(sport, sub_sport)`, a
  duplicated port of `frontend/src/yearStats.ts::displaySport`'s identical `GENERIC_CONTAINER_
  SPORTS` substitution (same precedent `sharing.py::_display_sport` already established). A third
  bar chart, sleep hours per day (`_sleep_hours_by_day`, `sleep_session.total_sleep_s` grouped by
  `local_date` — `func.max`, not sum, since the table's own uniqueness is `(athlete_id,
  local_date, source)` and more than one source could in principle report the same night),
  follows the identical omit-when-empty/pixel-width-bar convention as running/steps above.
  **Running distance vs. the week before, and a per-run table**: the "Running this week" bar
  chart's own caption now also names the week-before comparison -- `_running_distance_total_m`
  sums running distance over the Mon–Sun immediately before the week the bar chart covers,
  rendered as two absolute figures side by side (`"38.4 km vs 32.1 km the week before"`), not a
  bare delta -- the same "show the number it's relative to, not just a delta" preference
  `WeekView.tsx`'s own `priorWeekMeta` already established for total distance. `None` (the whole
  comparison omitted) when there were no runs at all the week before. A new "Runs this week"
  section (`_week_runs`, `RunLine`) lists every individual running *activity* in the week, not a
  per-day sum -- two runs on the same day are two rows -- with Day/Distance/Pace/Duration
  columns; the week's own farthest distance, fastest pace (lowest seconds/km), and longest
  duration are each bolded and accent-colored in their own column (`_highlight_cell`, a plain
  inline `<span>` rather than a row background, so it needs no separate "highlighted row"
  color decision) -- a tie bolds every tied run, not just the first, since "the week's fastest
  run" genuinely describes all of them equally. An activity missing either distance or duration
  is skipped from this table (never a fabricated pace), and the whole section is omitted, not
  shown empty, on a week with no qualifying runs.
  **Future races**: a further section listing every `planned_race` after today, however far out
  on the calendar (`_future_races`, unbounded — unlike `coming_races`, which stays scoped to just
  the coming Mon–Sun week) — a quick-glance date/days-until/name/goal-pace line per race (`Sun 06
  Dec 2026 (84d): 🏁 California International Marathon`, then `4:00 goal (5:41 /km)` on its own
  line) rather than the coming week's own full target-vs-predicted comparison, since a race that
  far out has no current prediction worth showing. `_clock_hm` renders a marathon-style goal
  ("4:00:00") as "4:00" (drops a trailing `:00` seconds component only when there's an hours
  part — a 5K/10K goal like "22:30"/"45:00" keeps its own real seconds precision unchanged), and
  the goal pace is a plain `target_duration_s / (distance_m / 1000)` division, `None` (the whole
  goal line omitted) whenever the race has no target time set.
  **Coming week: day-by-day weather + the athlete's own notes**: the "Coming week" section was
  originally one row per *scheduled workout*, so a day with nothing planned was invisible.
  `_coming_day_rows` now renders one row per day of the coming Mon–Sun, always all seven, so each
  day's own forecast (`weather_forecast.py`, reused as-is -- same un-cached, un-archived,
  athlete's-own-timezone fetch the Week view uses) has somewhere to show even with no workout that
  day: an emoji (`weather_code.py::weather_code_info`, the same non-frontend-display reuse
  `weather_titles.py` already established) plus min-max temperature, then that day's workout(s)
  underneath (a day can hold more than one, per the multi-workout-per-day revision above).
  `_coming_week_forecast` sizes its own `days` request to reach `coming_end` from `today` (the
  Sunday the job fires on) rather than a hardcoded constant, and returns `[]` -- never fabricated
  -- when the athlete has no home location set, same convention `GET /weather/forecast` itself
  uses; the day-by-day table then falls back to the original single "Nothing scheduled yet." row
  only when the whole week is bare (no workouts anywhere in it *and* no forecast at all), so an
  athlete who hasn't planned anything and hasn't set a home location doesn't get seven identical
  empty rows. A "Notes for the coming week" section, placed right before this table (the same
  "read before any of it" ordering `WeekView.tsx`'s own Notes card already uses above its day
  columns), surfaces the athlete's own `note` rows for the coming week
  (`entity_type="week"`, `entity_id` = that week's own Monday -- the exact same row the calendar's
  own week-notes panel reads and writes) -- omitted entirely, not shown empty, when the athlete
  hasn't written one.
- **Races on the calendar (`planned_race`, `planned_races.py`)**: a dated event with a distance
  and an optional target finish time — deliberately its own table, not a `planned_workout` sport
  tier, since a race has no step model and is never pushed to Garmin. Own id-keyed table (any
  number per athlete per date), mirroring `/planned-workouts`'s post-multi-per-day route shape:
  `GET /planned-races?start_date=&end_date=` (range), `GET /planned-races/by-date/{local_date}`,
  `POST /planned-races`, `GET/PUT/DELETE /planned-races/{race_id}`. Target-vs-predicted finish
  time reuses `performance_daily_rollup`'s own race predictions
  (`predicted_duration_s_for_distance` matches `distance_m` against `vdot.RACE_DISTANCES_M`,
  small float tolerance) rather than a second prediction path — a custom distance simply gets no
  prediction, honest rather than extrapolated. `days_until`/`predicted_duration_s` are computed
  fresh on every read, never stored, since both go stale immediately (the countdown daily, the
  prediction the moment a new performance rollup runs). Frontend: `PlannedRaceForm.tsx` (Day
  view's "Race" card, Month view's expanded-day card), code-split via `React.lazy` for the same
  2MB-precache-limit reason `ExerciseStepEditor.tsx::preloadExerciseCatalog` already documents; a
  trophy-iconed chip in the Month grid and Week view (read-only there). Also surfaces in the iCal
  feed (`calendar_feed.py::_build_race_event`) and the weekly email's "Races this week" section
  (`email_reports.py`) — both read the same `planned_race` row the calendar already fetches, no
  new query shape.
- **Distance goals, per year or month (`goal`, `goals.py`, `GoalButton.tsx`)**: a target distance
  for a whole calendar year or month, optionally scoped to one sport (`sport = null` means every
  sport combined) — one goal per `(athlete_id, period_type, period_start)` (`uq_goal_identity`);
  setting a new sport on an existing period's goal replaces it rather than adding a second one.
  `PUT /goals` upserts on that identity; `GET /goals?period_type=&period_start=` returns
  `{available: false}` with no other fields when nothing is set for that period, never a
  fabricated zero-progress row. `goals.py::compute_progress` (request-time, not rollup-backed —
  the same "bounded, occasional diagnostic lookup" exception `vo2max_analysis.py`/
  `pace_hr_zones.py` already establish, and a goal's own period is at most a year, cheap to sum
  fresh) builds a day-by-day cumulative-distance line from `activity.distance_m` (gap-filled to 0
  on a day with no matching activity, so `daily` always has exactly one entry per calendar day
  since the period started) plus a straight-line "target as of today" figure
  (`target_per_day_m * days_elapsed`) styled after a reference SPI/Strava goal widget the athlete
  pointed at — `ahead_behind_m = current_distance_m - target_distance_as_of_today_m`, positive
  meaning ahead of pace. The button/popup (`GoalButton.tsx`, rendered next to `MonthView.tsx`/
  `YearView.tsx`'s own `<h1>`) is deliberately the *only* place the chart itself
  (`GoalProgressChart.tsx`) ever renders — never inline on the calendar page — with a summary
  tile row (current distance, ahead/behind pace) reading the same two API fields the chart's own
  "today" tooltip point does, so the two can never drift from each other. `GoalForm.tsx` enters
  the target in the athlete's own Personalize distance unit (km or miles) and converts to metres
  at submit time (SI-in-storage). **Revision: the chart's tooltip shows the real ahead/behind-goal
  difference at *any* hovered point, not just today** — originally it just listed each series' own
  raw value (`Target: X` / `Actual: Y`) with no computed difference, unlike the reference widget.
  `actualData` now precomputes each day's own interpolated target by reusing the identity that
  index `i` in `progress.daily` already equals `days_elapsed`, the exact quantity
  `target_distance_as_of_today_m`/`ahead_behind_m` are built from, so the chart's own per-day
  target can never drift from the summary tile above it. Today's own point additionally gets the
  reference widget's richer "the N km you ran today puts you M km ahead/behind" sentence.
- **Race Readiness (`race_readiness.py`, Insights tab)**: has the athlete actually run enough
  *volume* for their next scheduled race, not just "are they fit" — a materially different
  question from the existing VDOT-based race prediction above (`predicted_duration_s_for_distance`),
  which only says what the athlete could run today at their current fitness, nothing about
  whether they've put in the specific weekly mileage/long runs a race of this distance actually
  calls for. Targets weekly running distance and a long-run distance from four (distance, target)
  anchor points (5k/10k/half/marathon, log-linear interpolated in between, clamped — never
  extrapolated — outside that range, same "honest rather than extrapolated" posture
  `predict_race_time_s`'s own search bounds already establish) — deliberately set at the
  recreational/intermediate end of published training plans (Hal Higdon Novice/Intermediate,
  Daniels' Running Formula's easier plans), not an advanced/competitive baseline (Pfitzinger,
  Hansons Advanced), which would read as "not ready" for the common recreational case this app
  is built for; unlike VDOT, there's no single physiological equation here, only coaching
  judgement this app states plainly as its own policy constants. Compliance against each target
  is **recency-weighted**, mirroring `fitness_daily_rollup`'s own Coggan/Banister CTL(42d)/
  ATL(7d) EWMA philosophy: weekly distance looks back 182 days with a 28-day half-life; the long
  run (a week's own single longest run, this app's stand-in for a tagged "long run" concept it
  doesn't otherwise have) looks back 70 days with a shorter 14-day half-life, since a taper's
  most recent long run matters far more than one from two months out. Each week is credited up
  to (never past) 100% of target, same capping instinct `email_reports.py::_bar`/`_bar_rows`
  already apply elsewhere. The two compliance fractions combine into one readiness percentage
  weighted 60% weekly distance / 40% long run (overall volume as the primary driver, per the same
  literature the targets come from, with the long run an important but secondary specificity
  factor) — an explicit, adjustable app policy, not a claimed universal formula. The VDOT-based
  prediction is reused as-is for the "prognosis" and shown *alongside* readiness, never blended
  into it — a volume-adequacy fraction and a fitness-derived time have different physiological
  bases, and combining them into one new number would overclaim precision this app has no
  grounds for; `None` for a non-standard race distance, same as `planned_race`'s own prediction.
  Deliberately request-time, not rollup-backed (the same "bounded, occasional diagnostic lookup"
  exception `vo2max_analysis.py`/`pace_hr_zones.py` already establish) — which race this
  even applies to can change day to day (a nearer race gets added, an old one passes), so there's
  no stable rollup-row identity to accumulate against. `GET /performance/race-readiness`
  defaults to the athlete's own nearest upcoming running race (`race_id` targets a specific one);
  `available: false` — never fabricated — with no upcoming race. The week-by-week evolution
  ("graph the shape over time") is a genuine backtest: the same weighted-compliance calculation
  re-run with `as_of` shifted back to each historical week, using only data available up to that
  date, computed from two queries fetched once (not one round trip per history point) and
  bucketed/weighted in Python. `weekly_distance_series`/`long_run_series` (`WeekValue`,
  `_dense_weekly_series()`) expose the actual realized numbers behind the two compliance
  percentages, not just the recency-weighted fraction derived from them — one entry per
  Monday-start week over each series' own window (182/70 days respectively), `0.0` never omitted
  for a week with nothing recorded, the same never-fabricated convention every other field here
  already follows. Frontend: `RaceReadinessChart.tsx`, a new Insights tab reusing
  `TrendChart` directly (its `history` is already pre-bucketed weekly server-side, unlike
  `Vo2maxChart.tsx`'s own raw daily series, so it skips `TrendControls`/`trendWindow.ts`
  entirely) alongside a stat-tile row (readiness/weekly distance/long run/prognosis, the latter
  two now also showing their own target as `meta` text) — plus two dedicated
  `RaceVolumeBarChart.tsx` bar charts (one bar per week from `weekly_distance_series`/
  `long_run_series`, a dashed `ReferenceLine` at the target, same "bars against a threshold"
  idiom as `EddingtonBarChart.tsx` and the same dashed-line styling `TrendChart.tsx`'s own zero
  reference line uses, just at `y=target`), added after the first version's stat tiles/percentage
  trend alone didn't surface the actual target/realized numbers explicitly enough.
- **Weather: full conditions judgement from one endpoint, no second Open-Meteo call
  (`weather.py`, `GET /activities/{id}/weather`)**: originally just enough for a header badge
  (temperature/humidity range, a representative weather code/feels-like/wind at the activity's
  own start) — extended so an AI coaching agent reading this endpoint daily can judge conditions
  in bpm/pace terms without making its own second call to `api.open-meteo.com`. Three additions,
  all following the pre-existing window-aggregate/single-representative-value conventions exactly:
  dew point, shortwave radiation, and cloud cover (`dew_point_min_c`/`max_c`,
  `solar_radiation_max_wm2`/`mean_wm2`, `cloud_cover_min_pct`/`max_pct` — relative humidity alone
  doesn't say how much heat strain the air actually causes, dew point does; >800 W/m² sustained
  shortwave radiation is severe direct-sun load a min/max on air temperature can't show); a full
  window range on apparent temperature (`apparent_temperature_min_c`/`max_c`, alongside the
  pre-existing single start-of-run `feels_like_c`) since apparent temperature sitting notably
  below air temperature — dry air/wind doing real evaporative-cooling work — is invisible in one
  start-of-run value; and `sunrise_utc`/`sunset_utc` (the daily entry matching the activity's own
  start date, stored as `value_text` since `activity_metric.value_num` has no datetime concept of
  its own) plus a request-time-derived `sunset_during_run` boolean (never a stored synthetic
  metric — a pure function of `sunset_utc` vs. the activity's own start/end). The new
  `hourly[]` array (one entry per hourly bucket in the activity's window: UTC timestamp, air
  temp, apparent temp, dew point, relative humidity, shortwave radiation, cloud cover, wind speed/
  direction) is the field that actually replaces a consumer's own second Open-Meteo call — a full
  run-window conditions table renderable straight from this one response. Deliberately **not**
  stored in `activity_metric` at all (that table is scalar-only, `value_num`/`value_text` one row
  per metric key — an hourly series doesn't fit it, and per-hour synthetic metric keys would
  pollute `metric_definition` with hundreds of junk rows); instead re-derived at request time from
  the same raw Open-Meteo response already archived verbatim on first fetch
  (`weather.py::read_archived_open_meteo_response`, a free gzip-decompress + JSON parse of bytes
  already on disk, picking the *newest* archived blob when more than one exists for an activity —
  no vendor call, exactly what "raw first, always" exists to enable). All new scalar keys live in
  `weather.py::_OPTIONAL_METRIC_KEYS`, never `_ALL_METRIC_KEYS` (the five-key cache-hit
  requirement `_read_cached` checks) — adding a key to the wrong tuple is the exact bug that would
  make every already-cached activity fail the cache-hit check and re-fetch from Open-Meteo on
  every single view, forever, since the new key would never exist on old rows. An already-cached
  activity's archived response genuinely lacks the new Open-Meteo variables on disk, though, so
  getting real values onto it needs `weather_backfill.py::backfill_weather_fields` (CLI: `sync
  backfill-weather-fields`), which force-refreshes it via `get_or_fetch_activity_weather
  (force_refresh=True)` — idempotent and cheap to re-run by checking whether an activity's own
  archived response already has `weather_backfill.py::_NEW_FIELD_MARKER`'s own key in its
  `hourly` block (a structural "was this fetched under the newer request" marker, independent of
  whether any particular hour's reading came back non-null) before ever calling Open-Meteo for it
  again. **`precipitation_mm`** (added later still) is a window **sum**, not a min/max range like
  every field above — "how much rain fell during the run" is a total, the same way a runner would
  describe it; `0.0` is a real, meaningful reading (no rain) and stays distinct from `None`
  (Open-Meteo's response lacks the `precipitation` array entirely, or every overlapping hour's
  reading is null) — summing an empty list would silently collapse those two very different cases
  into the same `0.0`, so the window list is checked for emptiness first, same guard the
  solar-radiation mean already uses. `_NEW_FIELD_MARKER` moved from `dew_point_2m` to
  `precipitation` when this field was added (a response carrying `precipitation` was necessarily
  fetched under a request that already included `dew_point_2m` too, since both land in the same
  joint `hourly=` param list) — moving the marker forward like this means re-running the backfill
  command after a field is added does one more real pass over every activity, even ones an
  earlier pass already backfilled for the prior marker, the correct (if slightly redundant)
  behavior since there's no cheaper way to know which activities are missing only the newest
  field without checking for it directly.
- **Weather forecast for the Week view, deliberately un-cached
  (`weather_forecast.py`, `GET /weather/forecast`)**: `weather.py` above is entirely
  past-activity weather, keyed to that one activity's own GPS start point — there was no concept
  of an athlete's own default/current location anywhere in this schema until this feature added
  one (`athlete.home_lat`/`home_lon`, nullable, settable via `GET/PUT /settings/profile`, a
  manual lat/lon entry or the Settings page's own "Use current location" browser-geolocation
  button). Calls Open-Meteo's *forecast* API (`api.open-meteo.com/v1/forecast`), a distinct
  endpoint from the historical archive API `weather.py` uses, with its own hard-capped
  `forecast_days` range of 0-16 confirmed live (requesting more raises an error response rather
  than silently truncating) — `MAX_FORECAST_DAYS = 16`. Deliberately **not** archived raw and
  **not** cached in `activity_metric`, unlike every other vendor fetch in this codebase: raw-
  first exists so a permanent record can be re-derived from an archive without recontacting a
  vendor, and a forecast has no such permanent-record concept, since it's superseded by reality
  as the date approaches — archiving it would only accumulate useless bytes with zero
  re-derivation benefit. This is the same "bounded, occasional live lookup" exception
  `vo2max_analysis.py`/`pace_hr_zones.py` already establish for a request-time-only
  computation, not a new precedent. `available: false` (never a fabricated forecast) when the
  athlete hasn't set a home location or the fetch fails, matching `ActivityWeatherOut`/
  `ActivityLocationOut`'s own convention. Frontend: `WeekView.tsx` fetches the whole week's
  forecast once in the parent component (`useWeatherForecast`, one call, not per-day — unlike
  `WeekDayPlannedWorkouts`/`WeekDayRaces`, which exist specifically to work around Rules-of-Hooks
  for genuinely per-date endpoints), keyed by `local_date` and passed down to each
  `WeekDayColumn`; a day with a matching forecast entry (today through however many days
  Open-Meteo actually returned, capped at 16) shows a weather icon (`weatherCodeInfo()`, reused
  as-is) plus min/max temperature on its own row directly under that day's date label — a past
  day or one beyond the forecast horizon simply has no matching entry and renders nothing.
  The Open-Meteo `timezone` param is the athlete's own `athlete.timezone` (a real IANA name,
  previously CLI-only/`sync athlete create`'s own default, read only by `calendar_feed.py`'s
  VTIMEZONE, now also self-service via `GET/PUT /settings/profile` and a "Use my browser's
  timezone" convenience button), never a hardcoded UTC — confirmed live that Open-Meteo's `daily`
  entries are dates in the *requested* timezone, so a UTC request for an athlete west of
  Greenwich returns "today" as already tomorrow locally for several hours a day, misaligning the
  forecast's own day boundaries against the Week view's local-date grouping by exactly one day.
  **Revision: the same richer conditions `weather.py` gathers for a past run, for the next
  `UPCOMING_DETAIL_DAYS` (3) upcoming days** (`fetch_upcoming_conditions`/`ForecastDayDetail`,
  `WeatherForecastOut.upcoming`) — dew point, shortwave radiation, cloud cover, a full
  apparent-temperature range, precipitation, sunrise/sunset, and an hour-by-hour trajectory, so a
  coaching agent reading this endpoint can judge tomorrow's conditions in the same bpm/pace terms
  it already judges a past run in, without a second Open-Meteo call. Deliberately a **second,
  independent** Open-Meteo request from the coarse `days` forecast above, not an extension of it
  — `forecast_days` controls both the `daily` and `hourly` ranges together in one request, and the
  Week view's own simple icon+temperature columns need up to 16 days while the rich hourly detail
  is only fetched for the near-term handful of days it stays meaningfully accurate for; combining
  them would mean fetching 16 days of mostly-unused hourly data, or capping the coarse forecast at
  3 days and breaking the Week view. The two fetches can succeed/fail independently — `upcoming`
  is `[]` (never fabricated) whenever its own request fails or returns nothing usable, regardless
  of whether `available`/`days` above succeeded, and vice versa. One further departure from
  `weather.py`'s own per-activity shape: there's no single "activity start" hour to anchor a
  representative wind/feels-like reading against the way `feels_like_c`/`wind_speed_mps`/
  `wind_direction_deg` do for a run, so `ForecastDayDetail` has no scalar equivalents for those
  three fields at all — `hourly` carries per-hour wind/apparent-temperature instead, letting a
  consumer pick whichever hour matches their own planned time, honest about what a day-level
  forecast actually is rather than fabricating one representative hour. Every datetime field
  (`local_date`, `sunrise_local`/`sunset_local`, each `ForecastHourlyPoint.time_local`) is the
  athlete's own local time, not UTC — confirmed live, same as the coarse forecast's own `daily`
  dates — with an explicit `_local` suffix (vs. `weather.py`'s own `_utc` fields) rather than
  leaving the distinction implicit. Backend-only for now, matching `ActivityWeatherOut.hourly[]`'s
  own precedent of a rich field with zero frontend rendering — no Week view UI change.
- **Settings-page operational actions**: `api/routers/settings.py` adds the web
  counterparts of four CLI-only commands — Garmin login/status, `sync import garmin-connect`
  ("sync now"), `sync rebuild`, and `sync import garmin-export`/`strava-export` (bulk .zip
  upload, the first `UploadFile` endpoint in this codebase). No job-queue infrastructure exists
  or was added — sync/rebuild/import all run via FastAPI's `BackgroundTasks` (the one existing
  precedent, `activities.py::get_activity_location`) and are polled through one generic
  `GET /settings/jobs/latest?source=...`, reading the same `ingest_run` table every sync/import
  entrypoint already writes (a new `source="rebuild"` value, written by
  `rebuild.py::rebuild_database_tracked`, since the plain CLI-only `rebuild_database` itself
  stays untouched). A bulk-export upload is streamed to a per-upload-unique temp path, not the
  CLI's own fixed `extract_root` — that fixed path would collide across two concurrent web
  uploads (never a concern for the CLI's single-operator use).
- **Personalize settings — week start day, time format, starting page, distance units**
  (`GET/PUT /settings/personalize`, Settings → Personalize): four pure display preferences,
  never read by any backend computation (unlike Profile's own birthdate/height/home-location
  fields, which feed formula fallbacks) — a deliberate second endpoint from Profile, same table,
  different concern, mirroring this app's own Profile-vs-Password split. `week_start_day`
  (`"monday"|"sunday"`, default `"monday"`) and `time_format` (`"24h"|"12h"`, default `"24h"`)
  and `default_view` (`"week"|"month"|"day"|"activities"`, default `"week"`) are new `athlete`
  columns; `unit_preference` (`"metric"|"imperial"`, default `"metric"`) reuses a column that
  already existed on the table (seed-time only, never previously read anywhere in the app) rather
  than adding a redundant one. Closed enums via Pydantic `Literal`, not a free-text field + manual
  validator like `AthleteProfileIn` uses for its own open-ended strings.
  **The reported bug** — a scheduled workout's time-of-day control showed AM/PM instead of 24h —
  turned out to have no HTML-only fix: a native `<input type="time">`'s stored *value* is always
  24h `"HH:MM"` per spec, but its *displayed* picker follows the browser/OS locale, and neither
  Firefox nor Safari respect the `lang` attribute override for this. `TimeOfDayField.tsx` (new
  component) replaces every native time input in the app (4 found — 3 in `ScheduleWorkoutForm.tsx`,
  1 in `PlannedRaceForm.tsx`) with hour/minute number steppers (+ an AM/PM toggle only in 12h
  mode) that always store/emit the same 24h string but render per the setting rather than the
  browser — 24h by default fixes the bug outright, cross-browser, with no locale dependency.
  **`PersonalizeContext.tsx`** (new, this app's first React Context) exposes the athlete's own
  settings app-wide via `usePersonalize()`, avoiding prop-drilling through the 4+ levels of view
  components that need one or more of these four values; a `DEFAULTS` constant is returned
  synchronously before the query resolves, so no consumer needs its own loading-state branch.
  **`formatDistance.ts`/`formatTime.ts`** (new) are the shared unit-aware formatters this app
  never had before this feature (confirmed by a full-codebase search: every screen did its own
  ad hoc `distance_m / 1000` + `.toFixed()` + a literal `"km"` suffix) — `formatDistanceValue`/
  `formatPaceValue`/`kmhToDisplaySpeed`/`displayDistanceToMeters` (the last for a form's own
  round-trip entry, e.g. `GoalForm.tsx`/`PlannedRaceForm.tsx`'s custom-distance field) and
  `formatClock`/`formatHHMM`/`formatTimeOfDay`, each with a `useDistanceFormat()`/`useTimeFormat()`
  hook pre-bound to the athlete's own current setting. Pace composes with the *existing*
  `formatMinPerKm` (`runningStats.ts`) for its "M:SS" part rather than reimplementing it, so that
  function and its own ~15 other callers needed no changes.
  **Week start day is a frontend display preference only** — every backend weekly concept
  (`rollups.py`'s Monday-keyed `period_rollup`, a week-`note`'s own Monday-keyed `entity_id`,
  `race_readiness.py`, `email_reports.py`, `sharing.py`'s recap/share-image generation) stays
  Monday-anchored regardless of this setting; only the frontend's own calendar-grid rendering and
  client-side weekly aggregates (`dateUtils.ts::startOfWeek`/`weekdayLabels`, generalizing the
  previously Monday-only `mondayOf`/`monthGridWeeks`/`WEEKDAY_LABELS`;
  `runningStats.ts::weekdayIndex`; `yearStats.ts::busiestWeekStart`; `RunningStats.tsx`'s own
  calendar-heatmap columns) respect it. `WeekView.tsx`'s own "Week stats" card previously read a
  Monday-keyed `period_rollup` row (`useCalendarWeeks`) for its totals — genuinely incompatible
  with a Sunday-start display, since that backend row simply doesn't exist for a non-Monday week
  boundary. Replaced with `dateUtils.ts::sumDayRollups`, summing the same per-day `DayRollupOut`
  rows the view already fetches for whichever 7-day range is actually showing — numerically
  identical to the old value for the Monday default (same underlying daily data), but correct for
  any week start. `MonthView.tsx`'s per-row "Week" column had the identical problem and gets the
  identical fix, additionally widening its own day-rollup fetch to the grid's full padded range
  (not just the calendar month) so an edge row's total still includes whichever adjacent-month
  days are real parts of that week.
  **Distance units reach real number changes, not just relabeling, in one place**:
  `eddington.ts::computeYearlyEddington`/`computeEddingtonBars` take a `unit` param and convert
  before computing — the Eddington number is genuinely defined in terms of a real distance unit
  (VeloViewer and others offer the same km-vs-mile choice), so a mile-preferring athlete gets
  their real mile-based number, not a km-computed one just relabeled.
  **Deliberately left on km internally** (flagged, not silently incomplete): `RunningStats.tsx`'s
  own bar/scatter/heatmap chart data, `ActivityCharts.tsx`'s per-second pace/speed/GAP stream
  panels, and `SplitsTable.tsx`'s per-km split table — real per-sample chart pipelines or a
  backend-fixed bucket identity (a "1km split," `ActivityFastestTable.tsx`'s own "Fastest N km
  runs" bucket matching `routers/activities.py::get_activity_context`'s `km_floor_m` exactly),
  not a simple display-text swap; converting them would mean touching chart axes/color scales or
  the underlying backend bucket semantics itself, a materially bigger job than this pass's own
  scope. `workoutSyntax.ts`/`workoutSteps.ts`/`splits.ts` (the running workout text-syntax parser
  and per-km splits computation) are similarly untouched — a parser for a km-denominated
  mini-language, not a display concern.
- **Staleness is a first-class signal, not an afterthought.** `perseverer/staleness.py` checks
  (a) whether `garmin_connect` has succeeded recently — escalating from "warning" to "critical"
  past `PERSEVERER_GARMIN_STALE_ESCALATE_DAYS` (default 7) — and (b) whether
  `athlete.last_full_export_at` is fresh enough (`PERSEVERER_EXPORT_FRESHNESS_DAYS`, default
  90). Both fire a generic JSON webhook (`PERSEVERER_STALENESS_WEBHOOK_URL`) if configured.
  The worker container (`worker/main.py`, APScheduler) runs `garmin_connect` sync + this check
  daily (default 04:15, jittered, resolved against `PERSEVERER_SCHEDULE_TIMEZONE` -- an IANA
  name, default UTC, not the container's own system clock, so a wall-clock schedule like
  "20:55 Pacific" stays correct across DST instead of drifting with a fixed UTC offset);
  `garmin_export` is never scheduled, it's a manual CLI action.
- **Backup + restore automation (Phase 9)**: `perseverer/backup.py`'s `create_backup` snapshots
  the SQLite database via `VACUUM INTO` and rsyncs it plus the raw archive and Parquet trees to
  a second host over SSH (`PERSEVERER_BACKUP_HOST`/`USER`/`PATH`, all-or-nothing optional — skips
  with a log line when unset, same contract as the Eufy credentials), on its own daily worker
  schedule (`PERSEVERER_BACKUP_SCHEDULE_HOUR`/`MINUTE`, default 03:30 UTC) separate from the
  Garmin sync's. `destination`/`source` are a plain string (`user@host:path` or a bare local
  path) throughout — rsync treats both identically, which is what lets the CI
  restore-from-backup job exercise the exact same code against a local temp dir instead of
  needing a real SSH host. `sync backup restore` is deliberately CLI-only, never a Settings-page
  button — unlike `rebuild` (additive, replays the raw archive), a restore overwrites live data
  with an older snapshot, matching `sync auth login`'s own precedent for dangerous,
  human-initiated actions. See `docs/adr/0014-phase-9-backup-hardening.md`.
- **Login brute-force lockout (Phase 9)**: `auth/lockout.py`, DB-backed (not in-memory — `api`
  runs 2 uvicorn workers that don't share process memory, but do share the one SQLite file),
  keyed on username (not source IP, since only the trusted reverse-proxy IP is ever visible
  server-side). `MAX_FAILED_ATTEMPTS` (5) failures in `LOCKOUT_WINDOW` (15 min) locks a username
  out, returning the same generic 401 a wrong password would; `is_locked_out()` runs
  unconditionally before the credential check so a locked-out response costs the same amount of
  work as a wrong-password one, preserving `/auth/login`'s existing constant-time-comparison
  discipline (ADR 0008) rather than reopening that same timing side-channel.
- **Container hardening (Phase 9)**: `api`/`worker` Quadlet units run with
  `ReadOnly=true`/`ReadOnlyTmpfs=true`/`NoNewPrivileges=true` — verified every real write path
  each container has funnels through `/data` first (the bulk-upload endpoint's temp path,
  DuckDB's `temp_directory`, both explicitly pointed under `/data`). `frontend` is deliberately
  NOT hardened this way yet — its entrypoint rewrites `config.js` in place under
  `/usr/share/nginx/html` at every container start (the mechanism that makes the API base URL
  runtime-configurable, ADR 0008), which isn't one of Podman's auto-tmpfs'd paths and shares a
  directory with the real static assets, so read-only support there needs an nginx.conf change
  not yet made. See ADR 0014 decisions 6-7 (including a real historical Quadlet bug where
  `ReadOnly=` silently forced `--read-only-tmpfs=false`, verified fixed in current Podman via its
  own docs before relying on the default).
- **Never drop a field, concretely**: the activity FIT parser (`fit/parser.py`), health FIT
  parser (`health/fit_parser.py`), and health JSON parser (`health/json_parser.py`) all
  register every field they see into `metric_definition`, even ones they don't materialize a
  value for (a field on a known message/object they don't map to a column, or a field of an
  entirely unrecognized message type/nested JSON structure). See each module's docstring for
  exactly which fields get values stored where versus cataloged-only.
- Multi-tenant from day one: every data table carries `athlete_id` except `athlete` and
  `metric_definition` (shared catalogs, not personal data) — enforced by a schema test
  (`tests/db/test_schema.py`), not just convention. A second athlete is a real, exercised path,
  not just schema-level theory: `worker/main.py`'s daily jobs (`run_daily_sync`,
  `run_daily_workout_push`) loop over every row in `athlete` rather than one hardcoded id, each
  athlete gets their own Garmin token-store directory (`config.py::garmin_tokenstore_dir_for`,
  `<data_dir>/garmin_tokens/<athlete_id>/`) and their own optional Eufy credentials
  (`athlete_eufy_config`, one row per athlete — see `adapters/eufy.py::
  resolve_eufy_credentials`, which falls back to the original global `PERSEVERER_EUFY_*` env vars
  only for `DEFAULT_ATHLETE_ID` so that original setup needs no migration), and every ingestion/
  backfill CLI command takes a `--athlete-id` override (mirroring `sync rebuild`'s own
  pre-existing option). `sync athlete create --display-name ...` is what actually provisions a
  new row. See docs/DEPLOY.md's "Provisioning a second athlete" for the operator sequence.

## Commands

```bash
# Python (uv-managed)
uv sync                      # install deps (installed via `pip install uv` if uv isn't on PATH)
uv run pytest                # test suite
uv run ruff check .          # lint
uv run mypy                  # strict type check

# Database (PERSEVERER_DATA_DIR in .env controls where — defaults to ./data for host tooling)
uv run alembic upgrade head              # create/migrate schema, seeds the one athlete row
uv run alembic revision --autogenerate -m "..."   # after changing db/schema.py

# Ingestion (the `sync` console script — see cli.py)
uv run sync import fit-folder <path>     # one-shot import of every .fit (+ daily_summary/hydration .json) in <path>
uv run sync import garmin-export <path>  # backfill from a Garmin export archive (dir or .zip)
uv run sync import garmin-connect        # on-demand incremental sync (--days to override window)
uv run sync import eufy                  # body-composition sync (always full history, see adapters/eufy.py)
uv run sync watch fit-folder <path>      # continuously poll <path> (--interval seconds, default 30)
uv run sync auth login                   # interactive Garmin login (MFA prompt) — run this yourself
uv run sync auth status                  # token store presence + age
uv run sync report counts                # per-source activity counts + unmatched (single-source)
uv run sync rebuild                      # wipe derived tables, replay the entire raw archive
uv run sync athlete set-password         # interactive: set an athlete's username/password
uv run sync athlete create-key           # generate a per-athlete API key (printed once)
uv run sync backup create                # VACUUM INTO snapshot + rsync to PERSEVERER_BACKUP_*
uv run sync backup restore <path>        # CLI-only, human-initiated -- overwrites live data

# Frontend
cd frontend && npm install
npm run typecheck            # tsc --noEmit
npm run build                # tsc --noEmit && vite build
npm run dev                  # Vite dev server w/ HMR — needs PERSEVERER_CORS_ALLOWED_ORIGINS
                              # in .env to include its origin (default http://localhost:5173)

# Full stack (Windows/Podman Desktop — compose.override.yml auto-merges)
podman compose up --build
curl http://localhost:8008/api/v1/healthz
# Every route except /healthz, /version, and /auth/login needs X-API-Key or
# Authorization: Bearer <jwt> — presenting neither fails closed (503) only when nothing is
# configured at all, never open. See docs/adr/0008-phase-5-frontend.md.
curl -H "X-API-Key: $PERSEVERER_API_KEY" http://localhost:8008/api/v1/calendar?start_date=2025-01-01&end_date=2025-01-31
curl -X POST http://localhost:8008/api/v1/auth/login -H "Content-Type: application/json" -d '{"username":"...","password":"..."}'

# bercy deploy — rootless Podman + systemd Quadlet units (quadlet/), no Compose; never build
# on bercy, see docs/DEPLOY.md
podman quadlet install quadlet/perseverer-api.container quadlet/perseverer-worker.container \
  quadlet/perseverer-frontend.container
systemctl --user enable --now perseverer-api perseverer-worker perseverer-frontend
```

## Platform constraints that shape every decision

- **Historical: DS1019+ / Celeron J3455 (Goldmont) had no AVX/AVX2, only up to SSE4.2.**
  bercy's i5-6260U has full AVX2, so this no longer binds the deploy target — but the
  x86-64-v2 CFLAGS cap in the Dockerfiles and CI's AVX-masked (QEMU `Westmere` CPU model)
  import smoke test are still in place as of this writing (not yet removed; see
  `docs/DEPLOY.md`'s environments table). Harmless to keep, and still useful if the NAS is ever
  pressed back into service for something else — a deliberate "cost nothing, don't rip out
  opportunistically" call, not an oversight.
- **32GB RAM on bercy, not meaningfully budget-constrained** — unlike the DS1019+'s 8GB shared
  with DSM, which is what originally forced the ~1.5GB whole-stack budget and 2-worker uvicorn
  cap. Those specific numbers haven't been revisited post-move; SQLite (not Postgres) remains
  the storage choice regardless — that's a raw-first/provenance-model decision this project is
  built around at this point, not something the old RAM ceiling alone justified.
- **Never build on bercy.** Images are built on Windows or in CI, pushed to GHCR, pulled by
  bercy's Quadlet units (`AutoUpdate=registry` + `podman-auto-update.timer`).
- **Precomputed rollups.** Originally mandatory because the Celeron couldn't aggregate a decade
  of activities per request; bercy's i5 likely could, but the pattern stays because it's good
  design regardless of hardware — every dashboard/calendar/recap view reads a `*_rollup` table
  (`day_rollup`, `health_metric_daily_rollup`, `rollups.py`) refreshed on ingest, never scans at
  request time. Every ingest entry point (`fit_folder`, `garmin_export`, `garmin_connect`,
  `rebuild`) accumulates which `local_date`s it touched and calls `refresh_daily_rollup` once
  per distinct date after its loop — bounded by dates touched, not files processed. A future
  adapter must honor this same contract. See `docs/adr/0006-phase-3-read-api-and-rollups.md`.
- **Windows dev, Linux prod.** LF enforced via `.gitattributes`. `pathlib` everywhere. The
  `fit_folder` watcher polls (no reliance on inotify — SMB/rsync-written files don't reliably
  fire inotify events inside a container).

## When a vendor library breaks

It will. `python-garminconnect` has already been rebuilt once (garth → curl_cffi) as of this
writing. The contract: **the fix stays inside one adapter file.** If you find yourself
changing the schema, the API contract, or the frontend because a vendor changed a JSON key,
stop — that's a sign the abstraction leaked, not a sign the schema was wrong.

**Verify a vendor library's API by introspecting the installed package, not from memory.**
`docs/adr/0003-phase-2-garmin-adapters.md` decision 1-2 is the example: introspecting
`garminconnect`'s actual `login()` source (not recalling its shape) surfaced a real
credential-fallback behavior that directly shaped the auth-safety design. Training data on
fast-moving vendor libraries is exactly what this project's brief warns is stale.

## Docs

- `docs/DEPLOY.md` — Windows → bercy handoff runbook.
- `docs/DATA_DICTIONARY.md` — grows every phase; the source of truth for what every stored
  field means and where it came from.
- `docs/API.md` — the REST API reference for third-party integration: every endpoint, param,
  and response shape, generated from the live OpenAPI schema and cross-checked against router
  source. A polished HTML twin of the same content is served by the frontend container itself
  at `/api-docs.html` (`frontend/public/api-docs.html`) — update both when an endpoint changes.
- `docs/adr/` — one ADR per phase, written before implementation starts.
- `CLAUDE.md` — a one-line `@AGENTS.md` import, not a second copy of this content. This file
  (`AGENTS.md`) is the real, canonical instructions file, migrated from `CLAUDE.md` so tools
  that follow the cross-tool `AGENTS.md` convention (Codex, Cursor, Gemini CLI, GitHub Copilot)
  read it directly with no extra setup; the import keeps Claude Code working unchanged, since it
  only reads `AGENTS.md` on its own when no `CLAUDE.md` exists anywhere on the path. Edit this
  file, never `CLAUDE.md` — the import line is the only thing that file should ever contain.
