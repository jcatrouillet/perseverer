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
  the local UTC offset in seconds separately, so "what time was it there" stays recoverable.
  FIT files carry their own `local_timestamp` field (`fit/parser.py::_derive_utc_offset_s`
  diffs it against `timestamp`) — occasionally corrupt on real devices (confirmed: 14 real
  indoor-cycling activities with no GPS fix came back with an offset off by roughly a billion
  seconds), so an implausible result (`abs(offset) > 16h`) falls back to 0 rather than silently
  shifting `local_date` by decades. GPX/TCX carry no local-time field at all — every timestamp
  is UTC Zulu — so their offset is derived from the activity's own first recorded GPS point via
  `timezonefinder` + `zoneinfo` (`timezone_lookup.py`), which also resolves the true IANA
  `tz_name` (correctly handling DST for that specific date); this only produced a byte-for-byte
  UTC-as-local assumption before, silently shifting some activities onto the wrong calendar day
  for any non-UTC athlete (confirmed against a real Strava activity: recorded 00:03 UTC,
  actually 2022-08-25 local in `America/Los_Angeles`, previously stored as 2022-08-26).
  `tz_name` on FIT-sourced activities is still unpopulated — FIT doesn't carry a zone name
  directly, only the numeric offset.
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
  concern). `local_date` (added Phase 3) is **offset-adjusted** — `(start_time_utc +
  utc_offset_s).date()`, not the raw UTC calendar date — as of Phase 6 (see
  `docs/adr/0009-phase-6-calendar-rollups-fitness-health.md` decision 8;
  `adapters/fit_folder.py::_local_date`). `start_time_utc` itself is untouched, still the raw
  naive-UTC instant; only this derived column is adjusted. **Known inconsistency**:
  `health_observation`/`sleep_session.local_date` are still the raw UTC calendar date (see their
  own entries below) — a `day_rollup` row's activity data and health data can reference
  boundaries up to `utc_offset_s` apart for a non-UTC athlete. `local_date` originally matched
  `health_observation`/`sleep_session`'s UTC-date convention exactly (ADR 0006 decision 2); that
  symmetry no longer holds for `activity` after this fix.
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
  kind — see ADR 0005 decisions 3-4). `garmin.daily_sleep.<field>` is the fourth family:
  scalar fields flattened from `garmin_connect.py`'s own live `get_sleep_data()` fetch (added
  after this adapter turned out to never fetch sleep at all — only `fit_folder`/`garmin_export`
  ever produced `sleep_session` rows, both from monitoring FIT files bundled in an export
  archive, which is why real sleep data silently stopped the moment the last export backfill's
  own data ran out even though the daily incremental sync kept succeeding). Same
  `sleepData`-vs-`daily_sleep` split as `garmin.export.*`/`garmin.daily_summary.*` above:
  historical backfill and live sync are genuinely different Garmin API responses with
  different field names for the same reading (e.g. `averageRespiration` vs
  `averageRespirationValue`), aliased together at the `LOGICAL_METRICS` layer
  (`api/routers/health.py`), not reconciled at ingest time. A comprehensive audit ("does every
  metric bulk import produces also flow through the live incremental sync?") found and closed
  the same never-fetched-live gap for five more families, each following the identical
  `garmin.export.<report_kind>.*`-vs-`garmin.daily_*.*` split as sleep above — different Garmin
  API responses for the same underlying reading, aliased at the `LOGICAL_METRICS` layer rather
  than reconciled at ingest time: `garmin.daily_hrv.<field>` (`get_hrv_data`, weekly/last-night
  HRV average and status); `garmin.daily_training_readiness.<field>` (`get_training_readiness`
  — a JSON *array* of intraday recalculations, newest first; only `data[0]`, the current
  reading, is kept); `garmin.daily_vo2max.<field>` / `garmin.daily_heat_altitude.<field>` /
  `garmin.daily_training_status.<field>` (all three from one `get_training_status` call, which
  nests exactly the data behind three separate GDPR-export report kinds —
  `MetricsMaxMetData`/`MetricsHeatAltitudeAcclimation`/`TrainingHistory` — under
  `mostRecentVO2Max.generic`/`mostRecentVO2Max.heatAltitudeAcclimation`/
  `mostRecentTrainingStatus.latestTrainingStatusData`, the last keyed by device ID with the
  first device's data taken since this athlete has only ever had one recording device); and
  `garmin.daily_race_predictions.<field>` (`get_race_predictions`, the one exception to every
  other live fetch's one-request-per-day shape — it accepts a date range and returns one record
  per day in a *single* request per sync run). `garmin.hydration.<field>` gained a second
  provenance the same way `garmin.daily_summary.*` already had two (`daily_summary_json` vs
  `garmin_connect_daily_summary_json`): `get_hydration_data`'s live JSON shape matches the
  *existing* `parse_hydration_json` exactly, so no new parser was needed, just a new
  `garmin_connect_daily_hydration_json` raw-object kind. Two GDPR-export report kinds were
  deliberately left export-only, not wired to a live fetch: `healthStatusData` (only
  `createTimestampUTC`/`updateTimestampUTC`/`outliersCount`, the last almost always `0` — Garmin
  internal data-quality metadata with no real athlete-facing value) and `AbnormalHrEvents` (only
  2 real events across ~4 years of this athlete's data — too rare to justify a dedicated fetch).
  `nap` is a single
  `aggregation="interval"` row per nap
  (`interval_start`/`interval_end`, `value_text` = feedback) rather than a dedicated table
  (ADR 0004 decision 4). `local_date` here (and on `sleep_session` below) is still the raw UTC
  calendar date of the observation's timestamp, unlike `activity.local_date` (offset-adjusted as
  of Phase 6, ADR 0009 decision 8) — a known, documented inconsistency, not an oversight.
- **`health_stream`** — Parquet-backed intraday time series, one row per `(metric_key,
  year_month)`: `heart_rate` (from `monitoring_mesgs`, `timestamp_16`-corrected — decision 1),
  `stress_level`, `respiration_rate`, `spo2`, `hrv`. Unlike `activity_stream`, a month's file is
  built up incrementally across many daily source files via `write_health_stream`'s
  merge-by-timestamp (decision 10) — re-ingesting a day is idempotent, not a duplicate.
- **`sleep_session`** — one row per night. From a FIT file (`fit_folder`/`garmin_export`),
  `start_time_utc`/`end_time_utc` are derived from `sleep_level_mesgs`' stage-change timestamps
  (not from Garmin's own sleep-duration field, which can differ slightly by excluding brief wake
  periods — see decision 3), and `sleep_score` comes from
  `sleep_assessment_mesgs.overall_sleep_score`. From `garmin_connect`'s own live
  `get_sleep_data()` fetch (`health/json_parser.py::parse_daily_sleep_json`), start/end come
  from `dailySleepDTO`'s own epoch-ms `sleepStart/EndTimestampGMT`, and `sleep_score` from
  `sleepScores.overall.value` — a different response shape, not a second implementation of the
  same one. `total_sleep_s` comes from the session span for FIT, from `sleepTimeSeconds` (or the
  span, as a fallback) for the live fetch.
- **`sleep_stage`** — consecutive stage-change rows paired into `light | deep | rem | awake`
  intervals against `sleep_session`: `sleep_level_mesgs` rows for FIT-sourced sessions,
  `sleepLevels[].activityLevel` (0/1/2/3, confirmed by summing each value's own segment
  durations against `dailySleepDTO`'s deep/light/rem/awakeSleepSeconds totals) for
  `garmin_connect`'s live fetch.

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

- **`period_rollup`** / **`health_metric_period_rollup`** (Phase 6) — the same two shapes one
  grain coarser, discriminated by `period_type` (`"week"` | `"month"`) rather than four separate
  tables. Computed as a rollup OF `day_rollup`/`health_metric_daily_rollup` (sum-of-sums,
  weighted `value_avg`), not of raw tables — see `rollups.py::refresh_period_rollup` and
  `docs/adr/0009-phase-6-calendar-rollups-fitness-health.md`. Week starts Monday (confirmed
  against the user's Garmin Connect account, for exact reconciliation).
- **`fitness_daily_rollup`** (Phase 6) — one row per `(athlete_id, local_date)`:
  `training_load` (the deduplicated daily sum of `fit.session.training_load_peak`), and the
  derived `ctl`/`atl`/`tsb` from an independently-computed Coggan/Banister EWMA — see
  `fitness.py::refresh_fitness_rollup`. Garmin's own exports have no CTL/ATL/TSB triplet at
  all, so this is genuinely independent, not a mirror of a Garmin-provided value.

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
  reconcile into one activity — see `sync report counts` for a summary view. `GET
  /activities/{id}/sources` (Phase 8) surfaces the `matched`-decision rows for one activity.
- **`insight`** (Phase 8) — the rules-based insight engine's output (`insights/engine.py`), a
  full delete-and-reinsert per athlete per refresh, not an append-only log. `kind` (`effort` |
  `streak` | `pb` | `load` | `health`) × `window` (`30d`/`90d`/`180d`/`year`/`365d`, or
  `current` for the streak/load/health rules that describe "right now" rather than a lookback)
  × `subject_key` (a short deterministic string, e.g. `"distance:run"`, unique together with
  `kind`+`window`+`athlete_id` — the refresh's actual idempotency key) identify each row;
  `detail` is a JSON blob of rule-specific context. See
  `docs/adr/0012-phase-8-strava-merge-insights.md` for the full dimension/window table and the
  load/health rules' thresholds.

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

## Calendar grid, Fitness & Form, health dashboard (Phase 6)

`GET /api/v1/calendar/weeks`, `/calendar/months` (rollup-backed, same shape as `/calendar` one
grain coarser — see `period_rollup`/`health_metric_period_rollup` above), `GET /fitness`
(reads `fitness_daily_rollup` — an independently-computed CTL/ATL/TSB, not a Garmin-sourced
value), and `GET /health/dashboard` (merges each logical metric's several raw `metric_key`
aliases into one series via a hardcoded `LOGICAL_METRICS` table in
`api/routers/health.py`, verified field-by-field against the real `metric_definition` catalog —
see ADR 0009). All follow the same per-endpoint `Depends(require_api_key)` pattern as every
other Phase 5+ route. The frontend gained a real year/month/week calendar grid (replacing
Phase 5's flat day list), a Fitness & Form page (`FitnessChart.tsx`, extending
`StreamChart.tsx`'s hand-rolled-SVG pattern), and a Health page (core daily summary / sleep /
HRV-SpO2-stress sections) — plus Vitest + React Testing Library, the frontend's first automated
test coverage. `sync rebuild`'s `garmin_export_health_json` replay gap (open since Phase 3) was
fixed in this phase, since Fitness & Form and the health dashboard both lean heavily on that
data. See `docs/adr/0009-phase-6-calendar-rollups-fitness-health.md`.

## strava_export, merge visibility/split, insight engine (Phase 8)

`strava_export` (`adapters/strava_export.py`) registers a bounded, deliberately small set of
new `activity_metric` keys for the CSV columns it materializes beyond the core `activity`
fields: `strava.relative_effort`, `strava.perceived_exertion`, `strava.training_load`. GPX files
carrying a `gpx.creator` value (the exporting tool/device) register that as an extra metric too.
Every other `activities.csv` column not explicitly materialized is still fully preserved — just
not promoted to a typed metric — inside the raw-archived `strava_export_csv_row` object itself
(one per CSV row, positional `[header, value]` pairs so the CSV's own duplicate-column-name
quirk survives verbatim). New `raw_object.kind` values: `strava_export_gz` (the literal
gzip-compressed vendor bytes, archived before decompression), `strava_export_gpx`,
`strava_export_tcx`, `strava_export_csv_row`, `strava_export_manual_entry` (the ~0.6% of rows
with no backing file), `strava_export_other` (anything else, archived raw but not parsed).

`GET /activities/{id}/sources` and `POST /activities/{id}/sources/{link_id}/split` are new
`api/routers/activities.py` endpoints — see the `merge_decision` bullet above and
`docs/adr/0012-phase-8-strava-merge-insights.md` decision 3 for the split endpoint's
non-destructive design.

`GET /api/v1/insights` (optional `kind`/`window` query filters) is a plain read against the new
`insight` table (see the Ops section above) — no request-time computation, same rollup mandate
as every other Phase 3+ read endpoint. `sync refresh-insights` is a manual, out-of-band trigger;
every ingest entry point (plus `garmin_connect`'s own daily-scheduled sync, unconditionally)
already calls the same `insights.engine.refresh_insights` automatically. The frontend gained an
`InsightsPage` (grouped by kind, filtered to one window at a time — see ADR 0012 decision 4 for
why `garmin_connect`'s existing daily cadence is what keeps a "last 30 days"-style window
correct even with zero new activities). See `docs/adr/0012-phase-8-strava-merge-insights.md`.

## Activity view/map/export UX pass, Strava data completeness fix

Three new `strava.session.*` `activity_metric` keys, registered by `adapters/strava_export.py`'s
CSV-totals overlay for GPX/TCX-sourced activities (which have no FIT session message to read these
from): `strava.session.avg_heart_rate`, `strava.session.max_heart_rate`,
`strava.session.total_descent` (from `activities.csv`'s own `Average Heart Rate`/`Max Heart
Rate`/`Elevation Loss` columns). Source-honest naming, not `fit.session.*` — three read sites
(`api/routers/activities.py`, `insights/engine.py`, `ActivityStatsGrid.tsx`) coalesce both key
namespaces via the same alias-priority pattern `api/routers/health.py::LOGICAL_METRICS` already
established, `fit.session.*` preferred when both are present. GPX/TCX streams also gained a
per-point `speed_mps` channel (haversine-derived `distance_m` for GPX; consecutive-delta speed for
TCX, GPX/TCX had no speed channel at all before this) — see `docs/adr/
0013-activity-view-map-export-strava-completeness.md` decision 1.

`GET /activities/{id}/context` gained a `fastest` field: the same same-sport, ±15%-distance-band
comparison pool `percentile_rank` already drew on, re-sorted pace-ascending and capped at 30 (the
"fastest 30 for this distance" table on the activity detail page).

`sync rebuild`'s replay dispatch gained `strava_export_gpx`/`strava_export_tcx` branches and
detects/replays a file-less manual-entry row from its own `strava_export_csv_row` raw object
(previously silently dropped both ways — see ADR 0013 decision 2), and `insight` was added to
`rebuild.py`'s `_REBUILDABLE_TABLES` wipe list (previously missing, causing a real FK violation
on any rebuild against a database with existing insight rows — see ADR 0013 decision 3).

See `docs/adr/0013-activity-view-map-export-strava-completeness.md`.

## Running performance index (VDOT)

New `activity_metric` key `sporthealth.performance.vdot` (`source="sporthealth"`, distinguishing
it from anything a vendor reported) — a Daniels-Gilbert VDOT score computed for every
`sport == "running"` activity from its `distance_m`/`moving_duration_s`, GAP-adjusted (grade-
adjusted pace) when the activity's Parquet stream has `distance_m`+`altitude_m` channels
(`vdot.py`'s `compute_gap_factor`, a distance-weighted variant of the same Minetti energy-cost
model `frontend/src/gap.ts` already used for the per-activity GAP chart — deliberately weighted by
distance rather than time so a recorded device pause can't corrupt the average). Null (no row
written) for non-running activities or a running activity whose duration is short enough that the
underlying %VO2max curve would exceed 100% — the model's own domain limit, not an arbitrary cutoff
(see `vdot.py`'s docstring).

`performance.py::refresh_vdot` is a full delete-and-reinsert per athlete per run, same precedent as
`fitness.py::refresh_fitness_rollup`/`insights/engine.py::refresh_insights` (both called alongside
it at every ingest entry point) — this is what keeps VDOT correct after a sport correction moves an
activity into or out of "running", without needing to track which activities changed.
`garmin_connect.py`'s daily-scheduled sync gates the call on `touched_dates` (unlike
fitness/insights' unconditional calls there): VDOT has no rolling-window dependency on "today", so
a rest day with zero new activities has nothing to recompute and skips the Parquet reads.

Exposed as `vdot` on both `GET /activities` (list) and `GET /activities/{id}` (detail) — same
single-key EAV subquery pattern as `training_load`. Frontend: a "VDOT" stat tile in
`ActivityStatsGrid.tsx`'s Training effect section (per-run value), and a "Best VDOT" stat tile in
`RunningStats.tsx` (`runningStats.ts::bestVdot`, the highest value among the activities already
fetched for that page's period) — deliberately the *best* value in the period, not a raw per-day
trend: an easy/recovery run's VDOT reads low purely from intensity, not fitness, so a day-to-day
chart would look like fitness constantly craters on easy days and spikes on hard ones.

## Live daily wellness sync via garmin_connect

Until this change, `garmin_connect.py`'s daily-scheduled sync only ever downloaded **activity**
FIT files — every `garmin.daily_summary.*` metric (resting/max HR, steps, calories, floors, SpO2,
stress) came exclusively from a one-off manual `sync import garmin-export` (GDPR archive) backfill
or a `daily_summary_*.json` file dropped into a watched `fit_folder`, never from continuous
automatic syncing. Confirmed as a real, ongoing gap against the live database, not a display bug:
every `garmin.daily_summary.*`/`garmin.export.UDSFile.*` observation stopped dead on the date of
the athlete's last manual export, while activity syncing kept working fine.

Fixed by teaching `GarminConnectAdapter.fetch_and_ingest_daily_wellness` to call `Garmin.get_stats
(cdate)` once per calendar day in the same rolling window already used for activities (verified
against the installed `garminconnect` package: this returns the exact same JSON shape
`parse_daily_summary_json` already parses for `fit_folder`'s own `daily_summary_*.json` files, so
no new parser was needed). Archived under a new raw_object kind, `garmin_connect_daily_summary_
json` (added to `rebuild.py`'s replay dispatch alongside the pre-existing `daily_summary_json`
branch — same parser, different provenance). Re-fetches the whole rolling window every run rather
than tracking "new" days, matching `fitness.py`'s own full-recompute precedent: `ingest_health_
batch` already upserts idempotently, so a day with current data is a cheap no-op and a day that
failed on a previous run self-heals on the next one. Same safety contract as the activity loop:
a 429 aborts the run immediately via `GarminRateLimitAborted`, no retry.

**Not covered**: HRV (`hrv.last_night_average`) comes from a FIT-only path (`health/fit_parser.py`,
monitoring FIT messages) with no JSON equivalent parsed anywhere in this codebase — `get_hrv_data()`
returns a different, currently-unparsed shape, deliberately out of scope here. It still requires a
monitoring-FIT source (i.e. a manual export) as before.

## Eufy Life body-composition sync

Garmin has no body-composition data in this project at all (only a per-activity
`fit.user_profile.weight` used solely for MET-minute math) — a genuine gap, not an oversight, since
Garmin's own `weight_scale` FIT message was never parsed. Body composition (weight, body fat,
muscle/bone mass, water %, BMR, visceral fat, metabolic age, protein ratio, BMI) instead comes from
a Eufy smart scale, via `adapters/eufy.py::sync_eufy` and `health/eufy_parser.py::
parse_eufy_scale_reading`, ported from the sibling `eufy-health-sync` project's own working
login/fetch logic (`POST /v1/user/v2/email/login`, `GET /v1/device/last_device_data`).

Confirmed live against the real API (not assumed): the endpoint's `limit` query param is silently
ignored — every call returns the account's **entire** reading history in one response (539 records,
2020-11-12 onward, for the account this was verified against). There is accordingly no separate
backfill-vs-incremental mode; every run (scheduled or `sync import eufy`) re-fetches and
re-processes full history, relying on `archive_raw_bytes`' content-addressing and `ingest_health_
batch`'s idempotent upsert to make a repeat run of already-seen readings a cheap no-op — same
"full recompute, self-healing" precedent as `fitness.py`/the live wellness sync above.

Each record's `scale_data` sub-object carries ~24 fields; the sibling project's own extraction only
uses 9 of them. `parse_eufy_scale_reading` generically flattens **every** scalar `scale_data` field
into an `eufy.scale.<field>` observation (`aggregation="instant"` — a weigh-in is a point-in-time
reading, not a pre-aggregated daily summary, unlike Garmin's JSON parsers), matching this project's
"never drop an unknown field" mandate rather than the sibling script's narrower list. Remaining
outer-record scalar fields (excluding `id`/`device_id`/`user_id`/`customer_id`/`group_id`, which are
identifiers, not health facts) become `eufy.reading.<field>` observations; `product_code` is kept as
`value_text`.

Unit handling is deliberately conservative: only `weight` has a confirmed conversion (raw hectograms
÷ 10 → kg, the same factor the sibling project already uses in production, independently
cross-validated here against two other fields in the same real sample — `muscle_mass`/`weight` ≈
`muscle`%, `bone_mass`/`weight` ≈ `bone`%). `body_fat`/`muscle`/`bone`/`water`/`protein_ratio` get
`unit="%"` (already 0–100-shaped, safely inferable). Every other field (`bmi`, `bmr`, `muscle_mass`,
`bone_mass`, `visceral_fat`, `body_age`, `impedance`, etc.) is stored exactly as reported, unit
`None` — e.g. `fat_free_weight`/`body_fat_mass` read identically in the live probe, which doesn't
cleanly resolve either interpretation, so neither is guessed at.

Credentials (`SPORTHEALTH_EUFY_EMAIL`/`_PASSWORD`/`_DEVICE_ID`/`_CUSTOMER_ID`, all optional) are
deliberately **not** held to `garmin_connect.py`'s stricter token-store-only/never-auto-login model:
there's no evidence Eufy's API shares Garmin's SSO 429-lockout fragility, and the sibling project's
own plain-env-var pattern has run this exact login flow safely, daily, unattended, for months.
`sync_eufy()` just skips (logs, doesn't raise) when any credential is unset. `worker/main.py`'s
`run_daily_sync()` calls it in its own `try`/`except`, separate from the Garmin sync and staleness
check, so a Eufy-side failure (bad credentials, an API change) never blocks either of those.

`GET /health/dashboard`'s `LOGICAL_METRICS` promotes ten of the raw `eufy.scale.*` fields to
human-meaningful dashboard names (`weight_kg`, `bmi`, `body_fat_pct`, `muscle_mass_kg`,
`bone_mass_kg`, `water_pct`, `bmr_kcal`, `visceral_fat`, `metabolic_age`, `protein_ratio_pct`) —
each a single-alias entry, since Eufy is the only source for any of them. The remaining ~14 raw
fields (`impedance`, `mode`, `head_size`, etc.) are still fully stored/queryable via
`GET /health/observations`, just not promoted to the dashboard — the same "catalog broadly, surface
a curated subset" split Garmin's own much larger raw field set already uses. Surfaced in the
frontend everywhere Garmin's own `CORE_METRICS`/`HRV_SPO2_STRESS_METRICS` groups already appear.
`HealthPage.tsx` exports the ten metrics grouped into four sets by comparable real-world magnitude
-- `BODY_COMPOSITION_MASS_METRICS` (weight, muscle mass), `_PERCENT_METRICS` (body fat, water,
protein ratio), `_INDEX_METRICS` (BMI, bone mass, visceral fat, metabolic age), and
`_ENERGY_METRICS` (BMR alone) -- each its own `HealthTrendChart` in `YearView`/`MonthView`/
`AllTimeView`, rather than one shared-axis chart or a plain average tile: BMR's ~1500 vs. bone
mass's ~3 would otherwise flatten the small-magnitude series to a near-zero line. Plus a dedicated
"Weight" trend chart in `WeekWellnessCharts.tsx` and weight/body-fat tiles in `DayViewPage.tsx`
(both of which maintain their own independent hardcoded metric lists rather than importing the
shared export).

Raw archive kind: `eufy_scale_reading_json` (`source="eufy"`, one raw object per reading, external
ID = the Eufy-assigned record ID) — added to `rebuild.py`'s replay dispatch alongside every other
health JSON kind, reusing `parse_eufy_scale_reading` with no separate parser needed.

**Outlier filtering.** A Eufy smart scale shared with other people produces readings that are real,
correctly-parsed values with no way to distinguish them from the athlete's own at ingest time (same
device, same `user_id` in Eufy's own raw JSON). Confirmed against real anomalies of increasing
severity: an isolated 51.9kg reading amid ~79kg days; a single day with three raw readings (one
real ~81.3kg, two duplicates from someone else ~48.1kg) whose naive daily average would already be
wrong before any day-level check ran; and a multi-week stretch (2025-09 through 2025-11) where the
other person's ~45-48kg readings actually **outnumbered** the athlete's own ~80kg ones locally --
which defeats any filter that compares a reading to its nearby peers, however wide the window, since
the "wrong" cluster is the local majority there, not a minority.

`api/routers/health.py::_body_composition_daily` filters at the display layer only (the raw archive
and `health_observation` rows stay untouched) using a **sequential baseline anchored to the last
accepted reading**, not a symmetric neighbor comparison: it walks a metric's entire history in
chronological order, accepting a reading only if it's within 15% of the last value that was itself
accepted, and updating the baseline only on acceptance. A whole run of bad readings gets rejected
together, however many of them cluster near each other or how long the run is, because they're
never compared to each other -- only to the last trusted value. The very first-ever reading for a
metric has nothing to compare against and is always accepted. This bypasses
`health_metric_daily_rollup` (the generic precomputed rollup every other logical metric reads)
entirely for these ten metrics, reading straight from `health_observation` instead, since the
rollup's own naive per-day average is exactly what gets contaminated by a bad same-day reading.

**Chart time axis.** `HealthTrendChart.tsx` plots on a true numeric time scale (`XAxis
type="number" scale="time"`, keyed on `MergedTrendPoint.ts` -- epoch ms from `local_date`,
computed in `healthStats.ts::mergeTrendSeries`) rather than Recharts' default evenly-spaced
category axis. Body composition readings are sparse and irregular (days or weeks apart), so a
category axis -- which spaces every plotted point evenly by index regardless of the actual gap --
would make a month-long silence between two readings look identical to two consecutive days. Every
chart built on `HealthTrendChart` (HRV/SpO2/Stress, respiration, body composition) inherits this,
though only the sparse body-composition charts make the difference visible.
