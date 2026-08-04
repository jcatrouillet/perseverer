# Data Dictionary

The source of truth for what every stored field means, its unit, and where it came from.
Grows every phase — updated at the end of each phase alongside `CLAUDE.md`, per the project's
"ways of working" rule.

## Conventions

- All physical quantities stored in SI units: metres, seconds, m/s, kg, °C, W, bpm. Conversion
  to imperial/display units happens at the presentation layer only, never in storage.
- **All datetime columns are naive Python `datetime` values that are implicitly UTC** — never
  timezone-aware. This is deliberate: SQLite/SQLAlchemy do not actually round-trip `tzinfo`
  through `DateTime(timezone=True)` (a value written as aware UTC comes back naive on read),
  so declaring `timezone=True` would advertise a guarantee the backend can't keep. See
  `docs/adr/0002-phase-1-schema-and-ingestion.md` decision 10. `activity.utc_offset_s` carries
  the local UTC offset in seconds separately, so "what time was it there" stays recoverable;
  `tz_name` (IANA zone name) is reserved but not yet populated — FIT files don't carry it
  directly, and deriving it from GPS coordinates is a deferred enhancement.
- Every `metric_definition` row records `first_seen_at` and `first_seen_source` — provenance
  isn't just per-value, it's per-metric-existing-at-all.
- Position (lat/lon) is stored as decimal degrees, converted from FIT's native semicircle
  integer format (`degrees = semicircles * (180 / 2**31)`) at parse time.

## Tables

### Bronze

- **`raw_object`** — one row per archived blob (gzip-compressed at `storage_path`,
  content-addressed by `sha256`, unique per `(athlete_id, sha256)`). Also mirrored as a JSON
  sidecar next to the blob on disk (`<sha256>.json`) — the sidecar, not this row, is the
  actual durable catalog; `sync rebuild` restores this table from sidecars first if the
  database was deleted entirely. `source_locator` is a file path (file-based adapters like
  `fit_folder`/`garmin_export`) or an activity reference (`garmin_connect`, e.g.
  `activity/<id>`). `kind` is adapter-defined: `fit` (any `.fit` file ingested via the unified
  dispatch since Phase 2's health extension — could turn out to be an activity or a health
  file, see `ingest_dispatch.py`), `fit_activity` (the Phase 1 kind, kept for FIT files
  archived before that change), `daily_summary_json`/`hydration_json` (`fit_folder`'s two
  recognized Garmin Connect-shaped health JSON filename patterns), `garmin_connect_json` (the
  Connect API's activity summary — archived for every activity but not cross-referenced from
  `activity_source_link`, see below), `garmin_export_health_json` (a real GDPR export's
  `DI-Connect-Wellness`/`Metrics`/`Aggregator` files — archived raw *and* parsed, see below and
  `docs/adr/0005-phase-2-garmin-export-real-data.md`), `garmin_export_json`/`garmin_export_csv`/
  `garmin_export_other` (everything else in an export archive that isn't a `.fit` file or
  recognized health JSON — archived raw, not parsed; see
  `docs/adr/0003-phase-2-garmin-adapters.md` decision 4).

### Core

- **`athlete`** — single row today (multi-tenancy scaffolding for a possible future).
  `last_full_export_at` is set by `garmin_export` on successful completion — the "days since
  last full Garmin export" health signal `sporthealth.staleness` nags on past 90 days.
  `username`/`password_hash`/`api_key_hash`/`api_key_created_at` (Phase 5) are nullable —
  an athlete may have neither, either, or both credential types; provisioned via
  `sync athlete set-password`/`create-key`, never a self-service UI. See
  `docs/adr/0008-phase-5-frontend.md`.
- **`device`** — one row per distinct `(manufacturer, product, serial_number)` seen in a FIT
  file's `file_id` message. `product` prefers the SDK's friendly name (e.g. `"fr955"`) over
  the raw numeric product code.
- **`activity`** — one row per real-world activity (post-merge; see `activity_source_link`).
  `id` is a ULID, not an autoincrement int, so it's sortable and safe to expose externally
  later without leaking row counts. `primary_source` records which adapter's data currently
  populates the core fields (field-level provenance/override precedence is a later-phase
  concern). `local_date` (added Phase 3) is the UTC calendar date of `start_time_utc` — *not*
  offset-adjusted, deliberately matching `health_observation`/`sleep_session`'s own
  `local_date` convention so `rollups.refresh_daily_rollup` can join across all three by that
  string directly (see `docs/adr/0006-phase-3-read-api-and-rollups.md` decision 2).
- **`activity_source_link`** — links one `activity` to every raw file/record that contributes
  to it. `external_id` is the adapter's idempotency key: for `fit_folder`, derived from the FIT
  file's device serial + start time (falling back to the file's sha256), deliberately never the
  filename, since real device folders don't use descriptive names; for `garmin_export`, the
  filename-embedded Garmin activity ID when present — either `<activityId>_ACTIVITY.fit`
  (Phase 1's manually-organized test data) or `<email>_<activityId>.fit` (a real GDPR export's
  own naming, confirmed only nested inside `DI-Connect-Uploaded-Files/UploadedFiles_*.zip` and
  a couple of single-file backup zips — see ADR 0005), tried in that order, falling back to
  `fit_folder`'s device+timestamp/sha256 heuristic when neither matches (e.g. the fixed-name
  device/training backup FIT files); for `garmin_connect`, the Connect API's own `activityId`
  directly. `raw_object_id` always points at the FIT that was actually parsed — for
  `garmin_connect`, which archives a JSON summary too, that JSON gets its own `raw_object` row
  but isn't linked from here (see decision 3 in ADR 0003).
- **`activity_metric`** — open-ended per-activity facts with no dedicated column, keyed
  `fit.<message>.<field>` (e.g. `fit.session.total_grit`, `fit.lap.27` for an unnamed FIT
  field number). This is what makes a new watch's extra fields show up without a migration.
- **`activity_stream`** — one row per activity referencing its full-resolution Parquet file
  (`channels` is a JSON list of whichever of `heart_rate`, `cadence`, `power`, `temperature`,
  `speed_mps`, `altitude_m`, `respiration_rate`, `distance_m`, `lat`, `lon` the FIT record
  messages actually carried — not every activity has every channel).
- **`lap`**, **`split`** — structured enough to earn real columns rather than living in
  `activity_metric`. `split` is populated from FIT `split_mesgs` when present (auto-laps,
  intervals) — not exclusively swim `length` messages as originally sketched.
- **`route_geom`** — `encoded_polyline`/`simplified_polyline` are identical for now (real
  Douglas-Peucker simplification is deferred to Phase 7, the map explorer). Falls back to
  session-level start/end/bbox fields when an activity has no per-record GPS (e.g. indoor).

### Health

Defined in Phase 1, populated starting with Phase 2's health/wellness ingestion extension —
see `docs/adr/0004-phase-2-health-ingestion.md`.

- **`health_observation`** — one row per daily/instant/interval health fact, keyed
  `metric_key` + `observed_at_utc` + `source`. `aggregation` is `instant | interval | daily`.
  Three `metric_key` naming families: `fit.<message>.<field>`-style keys for FIT-sourced facts
  that have no dedicated column (e.g. `hrv.status`, `sleep.deep_sleep_score` — every
  `sleep_assessment_mesgs` score maps generically to `sleep.<field>`, ADR 0004 decision 3);
  `garmin.daily_summary.<key>` / `garmin.hydration.<key>` for scalar fields flattened from the
  Garmin Connect-shaped `daily_summary_*.json` / `hydration_*.json` files (nested dict/list
  values in those files are archived but not flattened — decision 5); and
  `garmin.export.<report_kind>.<field>` for scalar fields flattened from a real GDPR export's
  `DI-Connect-Wellness`/`Metrics`/`Aggregator` JSON (`report_kind` mechanically derived from the
  filename — `sleepData`, `UDSFile`, `HydrationLogFile`, `TrainingReadinessDTO`,
  `EnduranceScore`, and ~20 more, all through one generic parser rather than bespoke code per
  kind — see ADR 0005 decisions 3-4). `nap` is a single `aggregation="interval"` row per nap
  (`interval_start`/`interval_end`, `value_text` = feedback) rather than a dedicated table
  (ADR 0004 decision 4).
- **`health_stream`** — Parquet-backed intraday time series, one row per `(metric_key,
  year_month)`: `heart_rate` (from `monitoring_mesgs`, `timestamp_16`-corrected — decision 1),
  `stress_level`, `respiration_rate`, `spo2`, `hrv`. Unlike `activity_stream`, a month's file is
  built up incrementally across many daily source files via `write_health_stream`'s
  merge-by-timestamp (decision 10) — re-ingesting a day is idempotent, not a duplicate.
- **`sleep_session`** — one row per night, `start_time_utc`/`end_time_utc` derived from
  `sleep_level_mesgs`' stage-change timestamps (not from Garmin's own sleep-duration field,
  which can differ slightly by excluding brief wake periods — see decision 3).
  `total_sleep_s`/`sleep_score` come from the session span and
  `sleep_assessment_mesgs.overall_sleep_score` respectively.
- **`sleep_stage`** — consecutive `sleep_level_mesgs` rows paired into `light | deep | rem |
  awake` intervals against `sleep_session`.

Daily steps/distance/calories are deliberately **not** reconstructed from `monitoring_mesgs`'
compressed cycle fields — `daily_summary_*.json` already has Garmin's own server-computed
totals for exactly those fields (decision 2).

### Rollups (Phase 3)

Precomputed, derived caches — never written to directly by adapters, only by
`rollups.refresh_daily_rollup`, called once per distinct `local_date` an ingest run touched.
This is what makes the platform constraint in CLAUDE.md real: "every dashboard/calendar/recap
view reads a `*_rollup` table refreshed on ingest, never scans at request time." See
`docs/adr/0006-phase-3-read-api-and-rollups.md`.

- **`day_rollup`** — one row per `(athlete_id, local_date)`: activity count/duration/
  distance/elevation/calories (summed across every activity that day) plus
  `sleep_total_s`/`sleep_score` (the longest session if multiple sources exist for one night).
  Fixed columns, since `activity`/`sleep_session` both have fixed shapes to roll up.
- **`health_metric_daily_rollup`** — one row per `(athlete_id, local_date, metric_key)`:
  `value_sum`/`value_avg`/`value_min`/`value_max`/`value_last` (the value_num of the latest
  `observed_at_utc` that day) + `n_observations`. Kept EAV-shaped rather than a wide table,
  because `health_observation` itself is EAV with source-dependent metric_key namespaces (the
  same physiological fact has a different key per source — `resting_heart_rate` vs.
  `garmin.daily_summary.restingHeartRate` vs. `garmin.export.UDSFile.restingHeartRate`); a wide
  rollup table would need a hardcoded metric_key→column map that breaks on every new source
  (decision 1). Storing all five aggregates means the API picks whichever one a given metric
  needs at read time, with zero code changes as new metric_keys appear.

Both tables are wiped and recomputed like every other entry in `rebuild.py`'s
`_REBUILDABLE_TABLES` — a rollup is a cache, not raw data, so "never destructive" doesn't apply.

### Notes (Phase 3)

- **`note`** — the write path CLAUDE.md's mission statement calls for ("a REST/JSON API an AI
  agent can write notes through"). One polymorphic table: `entity_type` (`"activity"` |
  `"day"`), `entity_id` (an activity ULID or an ISO `local_date`). A new `entity_type` is a
  data-only addition, not a schema change. See ADR 0006 decision 7.

### Registries (exempt from athlete-scoping — shared catalogs, not personal data)

- **`metric_definition`** — the metric catalog. `metric_key` is the natural primary key.
  `category` is `activity | health | device | unknown`; `unknown` means a field was seen but
  its meaning hasn't been promoted/documented yet (`is_promoted`). Comprehensive by
  construction: every field the FIT parser sees, named or not, gets an entry here — see the
  parser's module docstring for exactly which are materialized as values versus cataloged only.

### Ops

- **`ingest_run`** — one row per adapter invocation (`sync import fit-folder ...`, `sync import
  garmin-export ...`, the daily scheduled `garmin_connect` sync, or `sync import
  garmin-connect` on demand), tracking `items_seen`/`items_new`/`errors` (JSON) and, for
  `garmin_connect`, `watermark_from`/`watermark_to` (the rolling re-fetch window actually used).
  This is what `sporthealth.staleness.check_garmin_connect_staleness` reads to decide whether
  to fire the staleness webhook.
- **`merge_decision`** — one row per activity-ingest attempt, logging whether it matched an
  existing activity or became a new one, with the full `MergeDecision.reasons`/`inputs` for
  audit. Written even for `fit_folder` alone (catches duplicate files within one source), not
  just once a second source exists. With `garmin_export`/`garmin_connect` now live, this is
  also where a `fit_folder` activity and its `garmin_export`/`garmin_connect` re-sync
  reconcile into one activity — see `sync report counts` for a summary view.

## Metric registry

Populated automatically by `sporthealth.metrics.registry.get_or_register_metric`, called from
the activity FIT parser's, health FIT/JSON parsers', and GDPR-export JSON parser's ingest
paths for every field they encounter. As of the Phase 1 acceptance run (776 real activity FIT
files), 824 distinct metric keys were cataloged; after the Phase 2 health extension against
1808 real monitoring FIT/JSON files, 1156; after also running a real full GDPR export archive
(172MB, ~23,000 files across 6 nested zips plus ~24 report-kind JSON files under
`DI-Connect-Wellness`/`Metrics`/`Aggregator` — 0 errors, fully idempotent on re-run, 1250 total
activities with 776 correctly matched across `fit_folder`+`garmin_export` and 474 newly
discovered, 288,939 `health_observation` rows, 1480 `sleep_session` rows going back years
further than the local monitoring folder), 1415 distinct metric keys are cataloged in total.
Browse the live catalog via `select * from metric_definition` — there is no separate
promoted-metrics document yet (Phase 3's `/metrics` endpoint and the frontend's metric
registry browser, Phase 5+, are the intended long-term ways to browse this).

## Read API (Phase 3)

`GET /api/v1/activities`, `/activities/{id}`, `/activities/{id}/stream` (DuckDB-backed,
downsampled to a `low`/`medium`/`high` tier — see `stream_query.py`), `/health/observations`,
`/sleep`, `/calendar` (rollup-backed, the only one guaranteed not to scan), and
`POST`/`GET /notes`. Every route except `/healthz`/`/version`/`/auth/login` requires either an
`X-API-Key` header or an `Authorization: Bearer <jwt>` header (Phase 5 broadened this from a
single shared key — see below); presenting nothing at all fails closed (503) only when neither
`SPORTHEALTH_API_KEY` nor `SPORTHEALTH_JWT_SECRET` is configured, never silently open.
`SPORTHEALTH_CORS_ALLOWED_ORIGINS` (comma-separated) enables `CORSMiddleware` when set; unset
means no CORS middleware at all. See `docs/adr/0006-phase-3-read-api-and-rollups.md`.

## MCP server (Phase 4)

`/mcp` (Streamable HTTP transport, same `api` container/process as the REST API, not a
separate service) exposes eight tools — one per read endpoint above plus `create_note`/
`list_notes` — so an AI agent can query and annotate the platform as first-class MCP tool
calls instead of raw HTTP. Gated by the same `X-API-Key` as every REST route (a raw ASGI
wrapper, since `Mount`-ed sub-apps bypass FastAPI's own `Depends`). Each tool calls its REST
endpoint in-process, reusing 100% of the REST layer's logic rather than a second
implementation. `get_activity_stream` always requests the `low` tier regardless of what's
asked, since full-resolution stream data doesn't belong in an agent's context window. See
`docs/adr/0007-phase-4-mcp-server.md`.

## Per-athlete auth + frontend (Phase 5)

`POST /api/v1/auth/login` (unauthenticated, like `/healthz`) verifies `athlete.username`/
`password_hash` and issues an HS256 JWT signed with `SPORTHEALTH_JWT_SECRET`
(`SPORTHEALTH_JWT_EXPIRY_DAYS`, default 30). `require_api_key` (despite the name, now the
shared auth dependency for every protected route) resolves the authenticated `athlete_id` from
any of three credentials: the legacy shared `SPORTHEALTH_API_KEY` (resolves to
`DEFAULT_ATHLETE_ID` — existing scripts and the Phase 4 MCP server need no changes), a
per-athlete `X-API-Key` (hash-matched against `athlete.api_key_hash`), or a JWT bearer token.
Every router query is scoped to the resolved `athlete_id`, not a hardcoded default. The
frontend (`frontend/src/`) is a Vite/React SPA: `wouter` for routing, `@tanstack/react-query`
for data fetching, a hand-rolled SVG chart for the one stream-chart need (no charting library).
Its API base URL is runtime-configured via `frontend/public/config.js`, regenerated at
container start from `SPORTHEALTH_API_BASE_URL` — never baked into the Vite build. See
`docs/adr/0008-phase-5-frontend.md`.
