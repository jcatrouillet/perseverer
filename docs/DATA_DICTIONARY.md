# Data Dictionary

The source of truth for what every stored field means, its unit, and where it came from.
Grows every phase — updated at the end of each phase alongside `AGENTS.md`, per the project's
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

- **`athlete`** — one row per athlete (multiple real athletes are supported end-to-end: their
  own login, Garmin/Eufy credentials, and scheduled-workout calendar — see docs/DEPLOY.md's
  "Provisioning a second athlete"). `last_full_export_at` is set by `garmin_export` on
  successful completion — the "days since last full Garmin export" health signal
  `perseverer.staleness` nags on past 90 days. `username`/`password_hash`/`api_key_hash`/
  `api_key_created_at` (Phase 5) are nullable — an athlete may have neither, either, or both
  credential types; the initial username/password is CLI-only (`sync athlete set-password`/
  `create-key`, never a self-service signup) but an already-logged-in athlete can change their
  own password via `PUT /settings/password` (verifies the current password first, same
  lockout-protected check `POST /auth/login` uses). See `docs/adr/0008-phase-5-frontend.md`.
  `birthdate` (ISO date string, like every other `local_date`-shaped column in this schema)/
  `height_cm`/`sex` ("male"|"female", nullable, validated at the API layer) are optional,
  settable via `GET/PUT /settings/profile` — used ONLY as inputs to formula-based fallbacks
  elsewhere (max HR, see the Insights section below; BMR, see the Eufy section below) when there
  isn't enough empirical/device data yet. They never override or get reconciled against real
  data once it exists. `email` (also via `GET/PUT /settings/profile`) is currently inert — stored
  for a future feature, no consumer reads it yet.
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
  symmetry no longer holds for `activity` after this fix. `carbohydrates_g`/`sodium_mg` are the
  athlete's own logged fueling intake during the activity — unlike every other column here,
  neither has a vendor source at all (confirmed by introspecting both the FIT profile and the
  Garmin Connect API directly), so they're `null` until set via `PATCH .../fueling` and the
  durable record actually lives in `activity_sport_override` below, reapplied on every rebuild.
- **`activity_sport_override`** — the athlete's own after-the-fact corrections: `sport`/
  `sub_sport`, `is_race`, `name`, and `carbohydrates_g`/`sodium_mg`, each settable independently
  without disturbing the others. Keyed by `(athlete_id, start_time_utc)`, not `activity.id` (a
  fresh ULID minted on every `sync rebuild`), so a correction survives a full wipe-and-replay —
  `apply_sport_overrides` reapplies every recorded row onto `activity` at the end of `sync
  rebuild`, and immediately once when a correction is first set. Deliberately not one of
  `rebuild.py`'s `_REBUILDABLE_TABLES`. See `sport_override.py`'s own docstring for why
  carbohydrates_g/sodium_mg are pure user input rather than a correction of anything derived.
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
  `garmin.daily_race_predictions.<field>` (`get_race_predictions`, one of two live fetches that
  accept a date range and return one record per day in a *single* request per sync run, not
  once per day — the other was originally `get_body_battery`/`garmin.daily_body_battery.<field>`,
  Phase 9, added because no daily-summary or GDPR-export field carries body battery as a real
  per-minute series, only 8 sparse named checkpoints; live verification later found this
  endpoint itself only returns ~6 sparse checkpoints/day, so it's no longer this project's live
  body-battery source). `garmin.daily_body_battery.level` (`HealthStreamPoint`s in
  `health_stream` below, not `health_observation` rows) is now sourced from `get_stress_data`
  instead — a *per-date* fetch (like sleep/HRV above, not a range call: this endpoint only takes
  a single date) whose response carries a genuinely dense, ~3-minute-cadence
  `bodyBatteryValuesArray` — `[timestamp_ms, status, level, an undocumented 4th column]` rows,
  read via the response's own `bodyBatteryValueDescriptorsDTOList` (plural "Descriptors", a
  different key name than the old endpoint's singular one) column-index map rather than a
  hardcoded position, tolerating the extra trailing column. The old
  `get_body_battery`/`parse_daily_body_battery_json` fetch/parser and raw JSON kind
  (`garmin_connect_daily_body_battery_json`) stay in place unchanged, purely so already-archived
  raw bytes of that shape still replay on `sync rebuild` (raw-first/never-destructive) —
  `rebuild.py` has a branch per raw JSON kind. `get_stress_data`'s response also carries a real
  intraday *stress* series (`stressValuesArray`) and daily stress scalars
  (`avgStressLevel`/`maxStressLevel`), neither parsed into observations yet — cataloged via
  `unrecognized_field_keys`, not dropped, pending a future stress feature. `garmin.hydration.
  <field>` gained a second
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
- `garmin.daily_lactate_threshold.<field>` (`speed`/`heart_rate`/`power`) — a third live *range*
  fetch (`get_lactate_threshold(latest=False, aggregation="daily")`, alongside race predictions
  and body battery above), added for the Fitness & Form tab's Lactate threshold chart. One
  observation per day Garmin actually recomputed the value on (confirmed live: sparse, not one
  row per calendar day in range — `speed`/`heart_rate` updated once across a real 12-day test
  window, `power` updated on six of those days). `heart_rate`/`power` are plain bpm/W; `speed`
  is stored exactly as Garmin returns it (raw-first) even though it is **not** plain m/s —
  multiplying by 10 first is what turns it into a real, physiologically-plausible pace (confirmed
  against this athlete's own real numbers, not vendor docs: the corrected pace sits between their
  real VO2max-interval and easy-run paces every month tested). That correction is applied by the
  frontend at the point of use (`FitnessPage.tsx`), the same "store raw, convert on read"
  contract this project's cadence field already established.
- `apple_health.blood_pressure_systolic`/`_diastolic` (see the Apple Health section below) were
  promoted to `LOGICAL_METRICS` (as `blood_pressure_systolic`/`blood_pressure_diastolic`) for the
  Health tab's Blood pressure chart — previously queryable only via `GET /health/observations`,
  now also on `GET /health/dashboard` like every other health metric. No new data, just a new
  alias entry; the underlying `health_observation` rows are unchanged.
- **`health_stream`** — Parquet-backed intraday time series, one row per `(metric_key,
  year_month)`: `heart_rate` (from `monitoring_mesgs`, `timestamp_16`-corrected — decision 1),
  `stress_level`, `respiration_rate`, `spo2`, `hrv`, and (Phase 9) `garmin.daily_body_battery.
  level` — the first `health_stream` producer that isn't a FIT parser (every other one comes
  from `health/fit_parser.py`; this one from `health/json_parser.py`, originally
  `parse_daily_body_battery_json`, now `parse_daily_stress_json` — see above), and the first
  metric exposed to the frontend at all — `GET /health/stream` (`api/routers/health.py`), read
  directly with pyarrow (no DuckDB downsampling tier needed for one day's worth of readings),
  powers the day view's Body Battery chart. Unlike `activity_stream`, a month's file is built up
  incrementally across many daily
  source files via `write_health_stream`'s merge-by-timestamp (decision 10) — re-ingesting a day
  is idempotent, not a duplicate. That merge normalizes every timestamp to naive before
  comparing (a real bug, found building the body-battery feature: pyarrow round-trips a
  `tz("UTC")` column back as tz-aware Python `datetime`s on read, while `HealthStreamPoint`
  producers disagree with each other — `fit_parser.py`'s are tz-aware, `json_parser.py`'s are
  naive per this project's own naive-implicitly-UTC convention — so a second write to an
  already-populated month's file raised `TypeError: can't compare offset-naive and
  offset-aware datetimes` before this fix, on every metric, not just body battery).
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
This is what makes the platform constraint in AGENTS.md real: "every dashboard/calendar/recap
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
  `training_load` (the deduplicated daily sum of per-activity load), and the derived
  `ctl`/`atl`/`tsb` from an independently-computed Coggan/Banister EWMA — see
  `fitness.py::refresh_fitness_rollup`. Garmin's own exports have no CTL/ATL/TSB triplet at
  all, so this is genuinely independent, not a mirror of a Garmin-provided value. Per-activity
  load prefers `perseverer.performance.running_tss` (see "Running TSS" below) when one exists,
  falling back to `fit.session.training_load_peak` otherwise — see that section for why.

### Notes (Phase 3)

- **`note`** — the write path AGENTS.md's mission statement calls for ("a REST/JSON API an AI
  agent can write notes through"). One polymorphic table: `entity_type` (`"activity"` |
  `"day"` | `"week"`), `entity_id` (an activity ULID, an ISO `local_date`, or — for `"week"` —
  that week's own Monday `local_date`, matching `rollups.py`'s Monday-start week convention).
  A new `entity_type` is a data-only addition, not a schema change. See ADR 0006 decision 7.
  Surfaced in the frontend as a "Notes" card on `DayViewPage.tsx` (`entity_type="day"`) and,
  positioned above the day-by-day column strip so it's visible regardless of whether the week
  has happened yet, on `WeekView.tsx` (`entity_type="week"`) — a week-level note deliberately
  isn't gated behind that week having any recorded activities.

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
  This is what `perseverer.staleness.check_garmin_connect_staleness` reads to decide whether
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
- **`auth_login_attempt`** (Phase 9) — one row per `POST /auth/login` attempt (`username`,
  `attempted_at`, `success`), backing the login brute-force lockout (`auth/lockout.py`):
  `MAX_FAILED_ATTEMPTS` (5) failures for one username within `LOCKOUT_WINDOW` (15 minutes)
  locks that username out, returning the exact same 401 a wrong password would. Exempt from
  athlete-scoping — a failed attempt against a nonexistent username has no athlete row to
  attach to, and must still be counted. Opportunistically
  pruned of rows older than 24h on every insert, not a separate scheduled job. See
  `docs/adr/0014-phase-9-backup-hardening.md`.
- **`planned_workout`**/**`planned_workout_step`** (scheduled workouts) — a *future*,
  athlete-authored workout on the calendar, pushed to the Garmin watch, and its unexpanded
  steps. Any number of `planned_workout` rows per athlete per `local_date` — originally capped
  at one via a `UniqueConstraint` (v1), lifted once the athlete asked to schedule more than one
  workout on the same day; every row is addressed by its own id
  (`api/routers/planned_workouts.py`: `POST /planned-workouts` to create, `GET/PUT/DELETE
  /planned-workouts/{workout_id}` and `POST /planned-workouts/{workout_id}/push` per row, `GET
  /planned-workouts/by-date/{local_date}` for every workout on one date). Garmin's own
  `schedule_workout()` is itself date-granular with no concept of ordering within a day —
  `scheduled_time` is what the UI sorts multiple same-day workouts by. Three sport tiers: **running**'s `source_text` is the athlete's own
  typed workout-syntax text, kept verbatim and re-parsed into `planned_workout_step` rows on
  every save (`workout_syntax.py`), with `estimated_duration_s` derived from that parse;
  **yoga/bouldering** (`planned_workouts.py::PLACEHOLDER_SPORTS`)'s `source_text` (if any) is
  just freeform notes, never parsed — no `planned_workout_step` rows at all, and
  `estimated_duration_s` is set directly from the athlete's own `duration_minutes` input instead;
  **hiit/strength_training** (`planned_workouts.py::EXERCISE_SPORTS`) get real, named Garmin
  exercises picked from a bundled catalog (`garminconnect.exercises`, 1,527 exercises/47
  categories) via the frontend's own picker (`ExerciseStepEditor.tsx`) — steps arrive
  already-structured, never parsed from text (there's no natural text syntax for naming a
  specific Garmin exercise), and `estimated_duration_s` is computed from them (a rough assumed
  seconds/rep for a reps-based step, real seconds otherwise). `scheduled_time` ("HH:MM",
  nullable) is orthogonal to all three tiers and stored either way — it's Perseverer's own
  calendar display metadata only, since Garmin's `schedule_workout()` has no time-of-day API at
  all. `push_status` (`draft`/`pushed`/`push_failed`) + `push_error` + `garmin_workout_id` +
  `garmin_scheduled_at` track the push lifecycle `activity_workout` (the retrospective,
  FIT-parsed workout-plan table from Phase 1, one-to-one with a completed `activity_id`) has no
  concept of. `planned_workout_step` mirrors `activity_workout_step`'s own unexpanded-repeat-block
  shape (a `repeat_until_steps_cmplt` row describing `[repeat_from_step..step_index-1] x
  repeat_count`, not pre-flattened) so `workoutSteps.ts`'s expand/group helpers work across both
  tables unmodified, but widens `target_type` to `"pace"`/`"heart_rate"` (an absolute range, or
  `target_hr_zone` resolved against the athlete's own `athlete_hr_zone_config` at push time) plus
  an independent `cadence_low`/`cadence_high` — a recorded step's `target_type` is speed-only.
  Four columns are hiit/strength_training-only: `duration_reps` (a rep-counted set — distinct
  from `repeat_count`, which is "do this whole block N times" rather than "this one step is N
  reps"), `exercise_category`/`exercise_name` (the exact `(category, exercise)` pair from
  `garminconnect.exercises` — `exercise_name` is `""`, not `null`, when the step names just the
  category with no specific variant, matching Garmin's own catalog convention, so it round-trips
  distinctly from "no exercise at all", which a rest step legitimately has), and `weight_kg`
  (converted to grams — Garmin's own wire unit — only at push time, never stored that way). See
  `docs/adr/0015-scheduled-workouts.md`.

## Metric registry

Populated automatically by `perseverer.metrics.registry.get_or_register_metric`, called from
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
`PERSEVERER_API_KEY` nor `PERSEVERER_JWT_SECRET` is configured, never silently open.
`PERSEVERER_CORS_ALLOWED_ORIGINS` (comma-separated) enables `CORSMiddleware` when set; unset
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
`password_hash` and issues an HS256 JWT signed with `PERSEVERER_JWT_SECRET`
(`PERSEVERER_JWT_EXPIRY_DAYS`, default 30). `require_api_key` (despite the name, now the
shared auth dependency for every protected route) resolves the authenticated `athlete_id` from
any of three credentials: the legacy shared `PERSEVERER_API_KEY` (resolves to
`DEFAULT_ATHLETE_ID` — existing scripts and the Phase 4 MCP server need no changes), a
per-athlete `X-API-Key` (hash-matched against `athlete.api_key_hash`), or a JWT bearer token.
Every router query is scoped to the resolved `athlete_id`, not a hardcoded default. The
frontend (`frontend/src/`) is a Vite/React SPA: `wouter` for routing, `@tanstack/react-query`
for data fetching, a hand-rolled SVG chart for the one stream-chart need (no charting library).
Its API base URL is runtime-configured via `frontend/public/config.js`, regenerated at
container start from `PERSEVERER_API_BASE_URL` — never baked into the Vite build. See
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

New `activity_metric` key `perseverer.performance.vdot` (`source="perseverer"`, distinguishing
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

## Independently-computed race predictions, threshold pace/HR, and max HR

New table `performance_daily_rollup` (`performance_rollup.py::refresh_performance_rollup`), one
row per calendar day per athlete, populated from the earliest day the athlete has a
`perseverer.performance.vdot` reading (see above) through today. Deliberately independent of
Garmin's own `garmin.daily_race_predictions.*` and `garmin.daily_lactate_threshold.*` fields —
both stay ingested and displayed unchanged, shown alongside rather than reconciled against, same
posture as `fitness_daily_rollup` vs. Garmin's own Training Readiness/Status. Also independent of
`athlete_running_load_config`'s manually-configured threshold pace (which still feeds
`running_load.py`'s rTSS calculation) — the computed value here is a separate, display-only
Insights figure.

Columns:

| Column | Meaning | Basis |
|---|---|---|
| `rolling_vdot` | 42-day trailing **maximum** of `perseverer.performance.vdot` | Judgment call: reuses this app's own CTL lookback length, but as a hard-window max (not an EWMA) because an easy run's VDOT reads low from intensity, not fitness — the same reasoning `runningStats.ts::bestVdot` already applies. Labeled explicitly as a different mechanism from CTL's EWMA so the two 42-day windows are never confused. Under the Daniels-Gilbert model this figure already *is* the athlete's VO2max estimate (ml/kg/min), not an intermediate value needing a separate conversion — surfaced directly as "VO2max" on its own Insights tab (`Vo2maxChart.tsx`), explicitly labeled as this project's own independently-computed estimate, never Garmin's. `GET /performance/vo2max-analysis` (`vo2max_analysis.py`) is a request-time factor analysis over the same window — which run set the current value (a maximum, so exactly one), every other qualifying run in the window, when the driving run ages out, and plain-text staleness/gap diagnostics — deliberately not rollup-backed, since the window is tiny (a handful of runs), the same "bounded, occasional lookup" exception `/activities/needs-trim` already establishes. |
| `max_hr_bpm` / `max_hr_source` | 365-day trailing **maximum** of `{fit,strava}.session.max_heart_rate`, priority-merged, **all sports** (`source="empirical"`); else, if `athlete.birthdate` is configured, the Tanaka formula `208 - 0.7×age` (`source="formula_fallback"`); else null | Judgment call, own choice: a genuine max-HR effort is rare week-to-week, so a shorter window would flicker based on incidental recent effort; all sports because a max-HR effort from cycling/hiit is physiologically just as real as one from running, and restricting to running would silently discard it. Empirical own-data max HR stays the PRIMARY source, never overridden by a formula once real data exists — [Validity of the Maximal Heart Rate Prediction Models among Runners and Cyclists](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10146295/) found measured vs. predicted HRmax differs significantly for 9 of 13 formulas across 4,043 runners; [The 220-Age Formula Is Wrong](https://marathonhandbook.com/calculate-maximum-heart-rate/) puts the average error at 10-15bpm, with even the best alternative formulas still ~10.8bpm off. A brand-new athlete has zero empirical max HR for weeks, though, so `max_hr_bpm` falls back to Tanaka (`athlete_age.py::age_years_as_of`, chosen over the cruder 220-age for its lower documented error) for just that gap — `max_hr_source` records which path fired, same idea as `threshold_hr_source` below. This in turn unblocks `threshold_hr_bpm`'s own existing 88%-of-max-HR fallback (below) for a new athlete too, since that fallback needs a non-null `max_hr_bpm` to compute from. |
| `threshold_pace_s_per_km` | Anaerobic/lactate threshold ("LT2"/"VT2") pace (s/km) at 88% of vVO2max, solved from `rolling_vdot` | Literature: [Jack Daniels VDOT Calculator: Paces & Race Times](https://www.brenoamelo.com/blog/jack-daniels-vdot-explained) cites threshold pace as 86-92% of vVO2max; 88% is a representative point within that range (judgment call), cross-checked (not shifted) against two newer running-specific studies -- Fathi, Shahidi & Alhusaen Aga (2025, *Int J Exercise Science* 18(5):1381-1392, n=12 trained runners) measured RCP/VT2 at 89.6±3.8% VO2max; Esteve-Lanao, Sellés-Pérez, Arévalo-Chico & Cejuela (2026, *Sports* 14(1):29, n=1,411 endurance runners) measured VT2 at 83.8-87.4% VO2peak depending on performance level -- 0.88 sits between the two rather than moved toward either alone, since real downstream consumers (`hr_zones.py`, `running_load.py`) already calibrate against it. Closed-form: solves `VO2(v) = 0.88 × VDOT` as a quadratic in velocity `v` (m/min) using `vdot.py`'s own `VO2(v) = -4.60 + 0.182258v + 0.000104v²` — `a > 0`, `c < 0` for any realistic VDOT guarantees exactly one positive root (`compute_threshold_pace_s_per_km`, now parameterized by fraction so the aerobic threshold below reuses the identical function). Hand-verified: VDOT=50 → v≈235.1 m/min → pace≈4:15/km, matching published Daniels tables. |
| `aerobic_threshold_pace_s_per_km` / `aerobic_threshold_hr_bpm` / `aerobic_threshold_hr_source` | Aerobic threshold ("LT1"/"VT1", always a slower pace/lower HR than the anaerobic threshold above), same `compute_threshold_pace_s_per_km` function at `vdot.AEROBIC_THRESHOLD_VO2MAX_FRACTION = 0.73`; HR via the same empirical-median-or-fallback shape as `threshold_hr_bpm` below, own fallback fraction `AEROBIC_THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR = 0.851` | New, not previously computed. Literature: the same two studies cited above measured VT1 specifically -- Fathi et al. 2025 at 73.2±4.1% VO2max; Esteve-Lanao et al. 2026 at 67.5-73.4% VO2peak (also 85.1±4.6% of HRpeak for VT1's own heart rate) -- 0.73/0.851 sit within a fraction of a percent of both, an unusually tight agreement between an independent small gas-exchange study and a much larger multi-site one. `performance_rollup.py::compute_threshold_hr` (exported, shared with the anaerobic computation below) computes both thresholds' HR so they can't silently drift onto different logic; the anaerobic HR fallback (0.88, an older/more generic citation) is deliberately left unchanged in this same pass -- Esteve-Lanao's own 93.5% VT2 figure would be a real, materially different number, but revising an already-relied-upon HR-zone fallback is a separate decision from adding a brand-new one. |
| `threshold_hr_bpm` / `threshold_hr_source` | Empirical median HR among runs within ±5% of that day's own `threshold_pace_s_per_km` (by `perseverer.performance.avg_gap_speed_mps`) over the trailing 365 days, min. 3 qualifying runs (`source="empirical"`); else 88% of that day's `max_hr_bpm` (`source="fallback"`) | Literature: [How to Calculate Lactate Threshold: 3 Tests That Work](https://runnersconnect.net/how-to-calculate-your-lactate-threshold/) cites LT HR as commonly 85-92% of max HR for well-trained runners, and describes a practitioner method that derives LT pace from a recent race then reads the real HR sustained at that pace from training data — the basis for this empirical-first approach. ±5% tolerance and the 3-run minimum, the median (not mean), and 88% as the fallback fraction (a representative point within the cited 85-92% range) are judgment calls: median resists a single outlier (a cold-start HR spike, a strap dropout); the tolerance band is wide enough to catch real threshold-session pace variance while excluding easy runs and intervals. `threshold_hr_source` records which path fired, per this project's raw-first/provenance discipline — same reason `fitness_daily_rollup` stores `training_load` alongside CTL/ATL rather than just the derived output. |
| `predicted_5k_s`, `predicted_10k_s`, `predicted_half_marathon_s`, `predicted_marathon_s` | Predicted race time (seconds) at `rolling_vdot`, one column per distance | Literature: [How Accurate Are Race Calculators? A Riegel Formula Guide](https://runnersconnect.net/race-calculators/) reports Riegel's simpler power-law formula underestimates marathon time by 10+ minutes for half of runners when extrapolating from a much shorter race, while VDOT stays roughly 1% accurate 10k→half-marathon and 2-2.5% accurate 5k→marathon for trained runners — the basis for extending this codebase's existing VDOT model (`vdot.py`) rather than adding a second formula. Computed by `predict_race_time_s`: bisection search over duration, to 1-second tolerance, until `compute_vdot(distance, T) == rolling_vdot` — valid because `compute_vdot(distance, T)` is monotonically decreasing in `T` (verified numerically across all four target distances over an 11-400 minute range, not just assumed from the model's shape). Per-distance search bounds run from a just-sub-elite pace to a generous slow ceiling; a `rolling_vdot` outside what's achievable within those bounds returns `None` rather than extrapolating. Round-trip accuracy (`compute_vdot(distance, predict_race_time_s(distance, vdot))`) verified within 0.05 VDOT of the input across tested values. |

`refreshed_at` records when the row was (re)computed, same convention as `fitness_daily_rollup`.
Full delete-and-reinsert per athlete per run (`refresh_performance_rollup`), single forward
day-by-day pass — same precedent as `fitness.py::refresh_fitness_rollup`, cheap even at ~1,400+
days. Wired unconditionally alongside `refresh_fitness_rollup` in `garmin_connect.py`'s daily sync
(both have rolling-window dependencies on "today", unlike VDOT itself, so both must keep advancing
through rest days), and inside the `touched_dates` guard at every other ingest entry point
(`fit_folder.py`, `garmin_export.py`, `strava_export.py`, `rebuild.py`), plus the
sport-correction/merge cascade and threshold-pace-config save (`api/routers/activities.py`,
`api/routers/settings.py`).

Exposed via `GET /performance` (`api/routers/performance.py`, mirroring `GET /fitness` exactly).
Frontend: two new Insights tabs — `RacePredictionsChart.tsx` (four separate single-series charts,
one per distance, since the ~10x time-range spread between 5k and marathon would flatten the
shorter distances to near-invisibility on one shared axis and `TrendChart` only supports two
y-axes) and `ThresholdAnalysisChart.tsx` (renamed from `ThresholdMaxHrChart.tsx` once the aerobic
threshold and its own factor-analysis panel expanded the tab past "threshold pace + max HR" --
two combined metrics: one "Threshold pace" chart with both the anaerobic and aerobic pace series
on a shared axis (both seconds/km, directly comparable — aerobic is always the slower/larger
value), and one "Threshold & max HR" chart with all three HR series (max, anaerobic threshold,
aerobic threshold) on a shared axis (all bpm, aerobic < anaerobic < max by construction) — by
explicit request, after an initial version shipped these as four separate single/paired-series
charts; pairing pace with HR would still need two separate axes, so those stay apart) — both
reuse the
`MetricExplorer`/`TrendControls`/`trendWindow.ts` wiring `FitnessPage.tsx` established. A third
tab, `Vo2maxChart.tsx`, charts `rolling_vdot` directly as VO2max; since it's the only metric on
that tab (unlike the two above), it skips `MetricExplorer`'s list+detail shell entirely rather
than show a picker with nothing to pick between, while still reusing the same
`TrendControls`/`trendWindow.ts` week/month/year/all-time/custom navigation. Paired on the same
tab with `Vo2maxFactorAnalysis.tsx` (`GET /performance/vo2max-analysis`), which found and fixed a
real bug along the way: `activity_metric`'s existing indexes both lead with `activity_id`
(`ix_activity_metric_activity_key`) or fall back to the uniqueness constraint's own
`athlete_id, activity_id, metric_key` ordering, neither of which serves "every row for this
athlete carrying one specific `metric_key`, across every activity" — exactly what this
endpoint's window queries need. Confirmed live via `EXPLAIN QUERY PLAN` and direct timing: 33s
cold on real ~1,400-day history (a full per-athlete scan across every metric ever recorded) down
to 0.002s after adding `ix_activity_metric_athlete_key` (`athlete_id`, `metric_key`) — the same
missing-index failure mode, and the same verify-first methodology, as `ix_sleep_stage_session`.
**Revision**: the "Threshold Analysis" tab this section originally described (`ThresholdAnalysisChart.tsx`/`ThresholdFactorAnalysis.tsx`, `GET /performance/threshold-analysis`,
`threshold_analysis.py`) was removed entirely and replaced by a complete 5-zone Pace/HR table --
see `pace_hr_zones.py`'s own module docstring and `GET /performance/pace-hr-zones` in
`docs/API.md` for the full model (recovery/basic endurance/aerobic threshold/lactate threshold/
VO2max, each a real pace + HR range built from the athlete's entire running history, not a
point-in-time snapshot, plus the actual qualifying runs behind each range). The rollup fields
this paragraph's own threshold-pace/HR values came from (`performance_daily_rollup.
threshold_pace_s_per_km`/`aerobic_threshold_pace_s_per_km`/HR fields, described earlier in this
section) are unaffected -- `PerformanceCurveChart.tsx`'s reference lines still read them via
`GET /performance`/`GET /performance/curve`.
**Revision**: the athlete reported the zone table's paces as unrealistically fast; the real cause,
confirmed against this app's own history, was that `profile_vdot` picked the single highest VDOT
across *any* running activity ever recorded, and that maximum came from a 12.6-minute training
segment, not a race -- the Daniels VDOT formula is calibrated against genuine race efforts, so a
short, all-out training burst can post a VDOT well above what the athlete could hold for a real
race distance. `_all_time_best_vdot` (`pace_hr_zones.py`) now prefers an activity marked as a race
(`activity.is_race`, the same field `garmin_activity_summary.py`'s eventTypeId heuristic and the
athlete's own `PATCH /activities/{id}/race` correction populate) and only falls back to the
original unfiltered all-time-best training run when no race is marked at all --
`profile_vdot_source` (`"race"`/`"training_run"`/`null`, `PaceHrZonesOut`) records which path
fired, with an explicit caveat in `missing` when it's the fallback. `profile_max_hr_bpm` is
untouched -- a genuine max-HR effort doesn't need a race context to be real the way a
race-calibrated pace formula does.
**Revision**: the Zone 1/Zone 2 boundary itself was still too fast even after the race-VDOT fix
above -- its original "90% of the aerobic threshold" figure (a generic ventilatory-threshold-zone
convention, not tied to this app's own model) only moved the Recovery zone's upper pace bound to
5:55/km, short of the athlete's own real recovery-run pace. Replaced with
`ZONE1_2_VO2MAX_FRACTION = 0.59`/`ZONE1_2_HR_FRACTION = 0.65` -- the floor of Jack Daniels' own
published "Easy" pace range (59-74% VO2max, ~65-78% HRmax), the same Daniels-Gilbert model this
app's VDOT is already built on. Zone 2 (Basic Endurance) now runs almost exactly Daniels' own E
pace range end to end, since the aerobic threshold (0.73) already sits right at Daniels' own 0.74
E-pace ceiling; this moved the Recovery boundary from 5:55/km to 6:26/km on the real data.
**Revision**: the whole feature reached too far into the athlete's past to begin with -- the
athlete's own all-time-best race was from 2023, so the original all-time, unbounded lookback kept
the entire table anchored to a nearly-2-year-old data point regardless of how their fitness had
since changed, a bigger source of staleness than either fix above. Replaced with three windows
(`pace_hr_zones.py`'s own "How far back" docstring section): `TRAINING_RUN_WINDOW_DAYS = 365` for
training runs (frequent, so a year is already plenty and keeps the profile current) and
`RACE_WINDOW_DAYS = 730` for races (rare -- this athlete averages roughly one every five months, so
a one-year window would often come up empty and silently defeat the race-preference fix above) and
for `profile_max_hr_bpm`'s own window. The per-zone empirical-HR evidence pool is capped to the
same one-year training-run window too, not just the profile VDOT.

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

## Whole-activity average Grade Adjusted Pace, and the activity-comparisons endpoint

New `activity_metric` key `perseverer.performance.avg_gap_speed_mps` (`source="perseverer"`,
m/s — SI storage per AGENTS.md principle 6) — a single whole-activity average grade-adjusted
speed, computed for every `sport == "running"` activity whose Parquet stream has both
`altitude_m` and `distance_m` channels (`gap.py::compute_avg_gap_speed_mps`). The Minetti
energy-cost-of-running polynomial is duplicated from `frontend/src/gap.ts` (same cross-module
duplication precedent as `weatherCode.ts`/`weather_code.py`), but the aggregation itself is new:
each stream interval's grade-adjusted "equivalent flat time" (`dt_s * FLAT_COST/cost(grade)`) is
summed and divided into total real distance, i.e. a distance-weighted average — chosen over a
per-point unsmoothed series (too noisy for a single summary stat) and over a cruder net-
elevation/total-distance approximation (would read as flat for a loop/out-and-back regardless of
how hilly the middle was, exactly the routes most likely to match on "same start location"
below). `gap.py::refresh_avg_gap` is a full delete-and-reinsert per athlete per run, same
precedent as `performance.py::refresh_vdot`/`pace_bands.py::refresh_pace_bands` (called alongside
both at every ingest entry point, and available standalone via `sync backfill-avg-gap`).

**Per-lap GAP, served by the API instead of computed in the browser**: `GET /activities/{id}`'s
own `laps` array carries an `avg_gap_speed_mps` per lap (`LapOut.avg_gap_speed_mps`), computed
fresh per request (not stored) by `gap.py::compute_lap_gap_speeds_mps` — it reuses
`compute_avg_gap_speed_mps` unchanged, just called once per lap on a slice of the same Parquet
stream (read via DuckDB's `epoch(timestamp_utc)`, matching `transport_mix.py`'s own request-time
Parquet-read convention, so there's no `datetime`-tzinfo mismatch to reconcile against
`lap.start_time_utc`). A lap's own boundary is the next lap's `start_time_utc`, or the stream's
last sample for the final lap. This replaced a client-side computation
(`frontend/src/gap.ts::computeLapGapsMinPerKm`, since removed) that approximated each lap's grade
from a single net-elevation-change-over-the-whole-lap, rather than this function's real distance-
weighted average over every sample in the lap — so the API's own number is more accurate than
what the frontend used to show, not just relocated. The frontend's Intervals table
(`ActivityDetailPage.tsx`) now reads this field directly instead of computing its own, and any
headless/scheduled caller can read the same per-interval GAP without rendering a page.

`GET /activities/{id}/comparisons` (`api/routers/activities.py::get_activity_comparisons`) is a
new endpoint for the activity detail page's "Similar runs from here" section: the 10 most recent
*other* same-sport activities within ±15% of this one's own distance (`_COMPARISON_DISTANCE_
BAND_FRACTION`, the same tolerance `.../context` already uses) **and** starting within 300m of
this one's own `route_geom.start_lat`/`start_lng` (`_COMPARISON_START_RADIUS_M` — calibrated
against the real archive: for a sampled activity's own location, the same-place cluster
saturates by ~200-300m and a real gap opens before the next distinct location, first appearing
between roughly 500m and 1000m away). Each matched row carries VDOT, average GAP speed, average
HR (`AVG_HR_METRIC_KEYS` alias-merge, same as `.../context`), and average cadence
(`fit.session.avg_running_cadence`, doubled server-side to strides/minute — FIT's own field is a
single-foot rate, same doubling convention `insights/engine.py`/`ActivityStatsGrid.tsx` already
use for the identical field). Empty `rows` (with `matched_count: 0`), never a fabricated
comparison, whenever the activity has no distance, no recorded GPS start point (e.g. a treadmill
run), or genuinely no match yet.

Frontend: `ActivityComparisonTable.tsx`, rendered on `ActivityDetailPage.tsx` immediately after
the Charts section, gated on `isRunningSport` like the existing per-activity insights panel. The
current activity is shown as its own highlighted first row (its GAP/cadence read from the
already-loaded `ActivityDetail.metrics` array, not a second request) so its own numbers sit
directly alongside the 10 comparison runs.

## Running TSS: a pace-calibrated training-load input for Fitness & Form

Investigation into why Perseverer's CTL/Form read consistently 1.8x-3x higher than
intervals.icu's for the same real training found the cause: `fitness_daily_rollup`'s only input
was Garmin's own `fit.session.training_load_peak` (Firstbeat's proprietary EPOC/HR-based number),
which was never calibrated to the "100 = one hour at threshold pace" Coggan TSS convention every
other tool in the sport (TrainingPeaks, intervals.icu) uses — the EWMA math itself was already
correct. `running_load.py` closes that gap for running specifically:

- **`athlete_running_load_config`** — one row per athlete (upsert, mirrors
  `athlete_hr_zone_config`'s own shape/contract exactly): `threshold_pace_sec_per_km`, the
  athlete's own configured threshold pace. Null (the default) means "not configured yet".
- New `activity_metric` key **`perseverer.performance.running_tss`** (`source="perseverer"`) —
  one value per `sport == "running"` activity, computed by `running_load.py::compute_running_tss`
  from the existing `perseverer.performance.avg_gap_speed_mps` metric (`gap.py::refresh_avg_gap`
  — reused, not recomputed: no new Parquet/stream access) and the activity's own
  `moving_duration_s`, using the standard rTSS formula `duration_hours * IF^2 * 100` where
  `IF = avg_gap_speed_mps / threshold_speed_mps`. Full delete-and-reinsert per athlete per run,
  same precedent as `refresh_vdot`/`refresh_pace_bands`/`refresh_avg_gap` (all four now run in
  that dependency order at every ingest entry point, since this one consumes avg-GAP's output).
  With no configured threshold pace, this is a no-op — `fitness.py` then falls back to
  `training_load_peak` for every activity, byte-identical to before this feature existed.
- `fitness.py::refresh_fitness_rollup` prefers a `running_tss` row per activity when one exists,
  falling back to `training_load_peak` otherwise (any non-running sport, or a running activity
  predating threshold-pace configuration) — see that module's own docstring.
- `GET`/`PUT /settings/running-load` (mirrors `/settings/hr-zones`) lets the athlete set their
  threshold pace from the frontend Settings page ("Running training load" card, `mm:ss`/km
  input). Unlike the hr-zones PUT, this one also immediately re-runs `refresh_running_tss` →
  `refresh_fitness_rollup` → `refresh_insights` before returning, so saving a threshold pace
  updates CTL/TSB right away rather than waiting for the next sync.

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

Credentials are deliberately **not** held to `garmin_connect.py`'s stricter token-store-only/
never-auto-login model: there's no evidence Eufy's API shares Garmin's SSO 429-lockout fragility,
and the sibling project's own plain-env-var pattern has run this exact login flow safely, daily,
unattended, for months. Storage is now per-athlete: **`athlete_eufy_config`** (one row per
athlete, upsert — same shape/contract as `athlete_hr_zone_config`) holds `email`/`password`/
`device_id`/`customer_id`, set via `sync athlete set-eufy-credentials --athlete-id <id>` (CLI) or
`POST /settings/eufy/login` (the Settings page's own Eufy card) — the web path verifies the
credential against Eufy's own login endpoint before saving, same "don't persist something we
haven't confirmed works" posture `POST /settings/garmin/login` already has; `device_id`/
`customer_id` can't be verified this way (only exercised by a real sync), so they're stored as
given either way. `adapters/eufy.py::resolve_eufy_credentials` reads that row first; when none
exists and the
athlete is `DEFAULT_ATHLETE_ID`, it falls back to the original global env vars
(`PERSEVERER_EUFY_EMAIL`/`_PASSWORD`/`_DEVICE_ID`/`_CUSTOMER_ID`, all optional) — so the original
single-athlete deployment needed no migration when this became per-athlete; any other athlete
with no row simply has no fallback. `sync_eufy()` itself just skips (logs, doesn't raise) when
any resolved credential is missing. `worker/main.py`'s `run_daily_sync()` resolves and calls it
once per athlete in `athlete`, in its own `try`/`except`, separate from that athlete's Garmin
sync and staleness check, so one athlete's Eufy-side failure (bad credentials, an API change)
never blocks either of those or another athlete's own sync.

`GET /health/dashboard`'s `LOGICAL_METRICS` promotes ten of the raw `eufy.scale.*` fields to
human-meaningful dashboard names (`weight_kg`, `bmi`, `body_fat_pct`, `muscle_mass_kg`,
`bone_mass_kg`, `water_pct`, `bmr_kcal`, `visceral_fat`, `metabolic_age`, `protein_ratio_pct`) —
single-alias entries, since Eufy is the only source for any of them, except `weight_kg`/`bmi`/
`body_fat_pct`, which each carry a second `apple_health.*` alias for the pre-Eufy era (see the
Apple Health export section below).

`bmr_kcal` additionally gets a formula-computed FALLBACK for a day that has a resolved
`weight_kg` but no real Eufy `bmr` reading (e.g. any athlete without a Eufy scale at all, like a
newly-provisioned second athlete) — Mifflin-St Jeor (`bmr.py::compute_bmr_kcal`, the standard
`10×weight_kg + 6.25×height_cm - 5×age + (5 if male else -161)` equation), using that day's own
weight plus the athlete's configured `birthdate`/`height_cm`/`sex`. Only fires when all three
profile fields are set; a real Eufy reading for a given day always wins and is never overwritten.
Marked with a synthetic `source_metric_key = "computed.mifflin_st_jeor"` and `n_observations = 0`
(the existing per-day `source_metric_key`/`n_observations` fields already carry exactly this kind
of provenance for a real reading — no schema change needed to signal "this one is computed, not
observed"). `metabolic_age` gets no such fallback — it's a Eufy-proprietary population-comparison
figure, not a standard formula. The remaining ~14 raw
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

## Apple Health export import (blood pressure, pre-Eufy body composition)

`adapters/apple_health_export.py::import_apple_health_export` — a one-time backfill from an Apple
Health "export.xml" archive (Settings > [Name] > Export All Health Data on iOS), zero network
calls, same category as `garmin_export`/`strava_export`. Confirmed against a real export (not
assumed): blood pressure (122 systolic + 122 diastolic `Record`s, 2020-02-07 through
2025-06-15, unit always `mmHg`) and body mass (790 `Record`s, back to 2011-01-31, unit always
`kg`) exist in the file, alongside ~74 other record types (nutrition, mindfulness, ECG,
Apple-Watch-era vitals, 996 real clinical/medical records from a connected health-records
account) this adapter deliberately does not parse.

**Scope.** Blood pressure is imported in full, unconditionally — nothing else in this project has
any BP source. Body mass, BMI (`HKQuantityTypeIdentifierBodyMassIndex`), and body-fat percentage
(`HKQuantityTypeIdentifierBodyFatPercentage`) are imported only for records dated strictly before
`weight_before`, an ISO date the CLI auto-detects as the athlete's earliest `eufy.scale.weight`
observation (`adapters/apple_health_export.py::detect_weight_cutoff_from_eufy`) unless overridden
— this fills the real gap Eufy can't (Eufy only has data from when that scale was bought), rather
than duplicating or contesting what Eufy already covers for the overlapping era. A record whose
`sourceName == "eufy Life"` is excluded even when its own date falls before the cutoff: that's
Apple's own sync of the exact same first Eufy reading landing on the other side of a UTC/
local-date boundary (confirmed: one such record is dated 2020-11-11 in Apple Health, one day
before the Eufy adapter's own earliest raw fetch of 2020-11-12 UTC), not a distinct data point.

**Metric keys**: `apple_health.body_mass` (kg), `apple_health.body_mass_index` (unitless),
`apple_health.body_fat_percentage` (%), `apple_health.blood_pressure_systolic` /
`_diastolic` (mmHg) — `aggregation="instant"`, same convention as `eufy.scale.*`. Blood pressure
systolic/diastolic are stored as two independent observations sharing one `observed_at_utc`
(Apple's `startDate`) rather than as an explicitly paired reading — no need to parse the wrapping
`Correlation` XML element at all, since `health_observation` already stores multi-field
one-timestamp readings this way for every Eufy scale step-on.

**Unit normalization**: `BodyMass` — passthrough if `unit="kg"` (100% of this real export), `×
0.45359237` if `unit="lb"` (Apple Health's schema allows this depending on device locale; not
exercised by any real export seen so far, kept as a defensive path). `BodyMassIndex` —
passthrough, `unit=None`. `BodyFatPercentage` — Apple's own HealthKit convention stores this as a
**0–1 fraction** despite carrying `unit="%"` (confirmed: `value="0.211"` for a ~21% reading) — `×
100` to match `eufy.scale.body_fat`'s already-established true-percent convention, or the two
sources would visibly disagree in scale on the same chart.

**Raw archiving: whole-file, not per-record.** Every other adapter in this codebase archives raw
bytes at *file* granularity — one `raw_object` per FIT file, per Garmin JSON report, per Eufy API
reading — with a generic parser pulling many observations out of that one archived blob. A real
`export.xml` can run to gigabytes with millions of XML elements, so this adapter follows the same
file-granularity precedent rather than introducing per-record archiving (no precedent anywhere in
this codebase, and would mean millions of tiny raw_object rows for a one-time import): the
**entire file** is archived once as a single `raw_object` (`kind="apple_health_export_xml"`,
`source="apple_health_export"`), then streamed through
`health/apple_health_parser.py::parse_apple_health_export_xml` — the only `ET.iterparse`-based
parser in this codebase (`gpx/parser.py`/`tcx/parser.py` both use non-streaming `ET.fromstring`,
fine for their own much smaller files) — extracting the ~5 record types above into one
`HealthBatch`, fed through the same `ingest_health_batch` every other health source uses. Because
the whole file is archived, extending this parser later to pull more record types (VO2max,
mindfulness sessions, etc.) out of the same export never requires the user to re-supply the
original file — a code change plus `sync rebuild` alone would pick it up.
`rebuild.py`'s `apple_health_export_xml` replay branch re-derives `weight_before` fresh via
`detect_weight_cutoff_from_eufy` at replay time (rather than persisting it as extra raw_object
metadata) — the same "re-derive over store" preference `garmin_export_health_json`'s own replay
branch already uses for `report_kind`. This only works because `eufy_scale_reading_json` raw
objects always replay earlier in `fetched_at` order than any `apple_health_export_xml` row could
(Eufy predates any Apple Health import by construction), so Eufy's own data is already in
`health_observation` by the time this branch runs.

**Dashboard wiring.** `GET /health/dashboard`'s `weight_kg`/`bmi`/`body_fat_pct` logical metrics
(`api/routers/health.py::LOGICAL_METRICS`) each carry a second alias —
`["eufy.scale.weight", "apple_health.body_mass"]` and so on — so the existing weight/BMI/body-fat
charts extend back through the pre-Eufy era with zero frontend changes. These three metrics route
through `_body_composition_daily`'s sequential outlier-rejection walk (see above), which now
queries `metric_key IN (aliases)` instead of a single key and tracks each accepted day's own
`source_metric_key` — since the two sources never share a date by construction, the same
last-accepted-value algorithm keeps working correctly straight across the 2020-11 source
transition with no special-casing. Blood pressure has **no dashboard chart yet** — deliberately
deferred (a two-series systolic/diastolic chart is real net-new frontend work); queryable via
`GET /health/observations?metric_key=apple_health.blood_pressure_systolic` (or `_diastolic`) only,
for now.

**Deliberately out of scope, not lost**: 996 real clinical/medical records (labs, medications,
diagnoses, conditions, allergies, immunizations — synced from a connected health-records account,
a different and more sensitive category of data than fitness/wellness metrics), ECG waveforms (a
different data shape entirely — ~3-minute ADC traces, not a scalar metric), nutrition logs,
mindfulness sessions, and Apple-Watch-era HR/VO2max/HRV/respiration (Garmin already covers that
same period for this athlete). Workout GPX routes in the export overlap existing Garmin/Strava
activities for 2020–2022 and are not imported as activities.

## Bouldering per-route data (reverse-engineered, undocumented FIT fields)

Bouldering activities (`sport="rock_climbing"`, `sub_sport="bouldering"`) encode one route per
attempt as a pair of `split_mesgs` rows -- a `climb_active` split (the attempt itself) followed
by a `climb_rest` split -- using field numbers the installed `garmin_fit_sdk`'s own profile has
no name for at all (confirmed directly: `Profile["messages"][312]["fields"]` has no entry for
any of them). Cracked, not looked up: decoded two real bouldering FIT files against the
athlete's own logged route sequences (19 routes in one, 8 in the other) and paired each
`climb_active` split, in order, against the logged grade+status for that route -- both matched
on all 27 real routes with zero exceptions.

- `70` -- V-scale grade, offset by +1 in the raw FIT value (V0=1, V1=2, ... -- `fit/parser.py`
  de-offsets it before storage). A second, unsourced field list found afterward on the internet
  claimed a wider "0-29, up to Font 9b" range for this same field; consistent with what's
  verified here (only V0-V4 ever appears in the two files checked) but unconfirmed beyond that.
- `71` -- result: `2` = "attempt", `3` = "completed". The same unsourced list independently
  claimed this exact mapping, corroborating this project's own find. Any other raw value is
  stored as a literal `"unknown_<n>"` string rather than dropped or guessed at.
- `15`/`16` -- average/max heart rate for the split. Not from the original crack -- separately
  confirmed before trusting it: `avg <= max` held on all 55 real splits across both files (never
  once violated), and both fell in a plausible bpm range against the athlete's own recorded
  resting heart rate in the same file. Unlike grade/result, these are real on *both*
  `climb_active` and `climb_rest` splits (a rest interval still has a heart rate).
- The unsourced list's claim for field `11` ("Temperature???", its own uncertainty markers) does
  **not** hold up against real data: it's a hard-constant `31` across two entire files recorded
  on different days -- a real sensor reading wouldn't stay bit-for-bit identical across separate
  sessions. Left unmapped. Most of that same list's other claimed field numbers (`0`, `9`, `13`,
  `26`, `27`, `28`, `32`, `33`, `34`, `72`, `73`) never appear in either real file at all --
  likely describes a different FIT message (e.g. Garmin's `climb_pro`, used for outdoor/via-
  ferrata climbing) rather than indoor bouldering's own `split_mesgs` encoding.

New `split` table columns: `climb_grade` (integer, nullable), `climb_result` (string, nullable),
`climb_avg_hr`/`climb_max_hr` (float, nullable) -- all four only ever populated for a bouldering
activity, `climb_grade`/`climb_result` only on a `climb_active` row. `fit/parser.py::
_climb_fields` is the one place that reads the raw field numbers; `ParsedSplit` carries the
decoded values through to `adapters/fit_folder.py::insert_new_activity`, same insertion path
every other source (garmin_export, garmin_connect, fit_folder) already shares for splits -- no
per-adapter special-casing needed. Exposed on `SplitOut` (`GET /activities/{id}`), same as every
other split field.

Frontend: `boulderingRoutes.ts` turns the flat `SplitOut[]` into one row per route (filtering to
`climb_active` splits with a grade present) and formats grade/result for display;
`BoulderingRoutesTable.tsx` renders it as a new "Routes" section on the activity detail page,
right after Intervals and before Charts -- self-gating (renders nothing) for any activity with
no climb splits, so no explicit sport check is needed at the page level.

## Backup + restore automation, login lockout, dependency scanning (Phase 9)

No new athlete-facing data, but two new tables (see the Ops section above for
`auth_login_attempt`) and one new file-level artifact worth recording here since it isn't a
database table at all: `<data_dir>/backups/perseverer-<timestamp>.db`, a `VACUUM INTO` snapshot
of the live SQLite database, rsync'd (along with the raw archive and Parquet trees, unchanged)
to a second host over SSH. `src/perseverer/backup.py` is the one module that owns this; see
`docs/adr/0014-phase-9-backup-hardening.md` and `docs/DEPLOY.md`'s Backups section for the full
mechanism, scheduling, and the CI restore-from-backup test. The LLM narrative layer originally
scoped for this phase (a rewrite of `insight` rows into prose, cached in DB) was deliberately
deferred -- no new tables or fields exist for it yet, see the ADR's own "Deliberately out of
scope" section for the reasoning and the reference design kept for whenever it's picked back up.

## Scheduled workouts: author on the calendar, push to the Garmin watch (running first)

New tables `planned_workout`/`planned_workout_step` (see the Core section above) plus one new
adapter method that writes to a third-party account rather than only reading from it --
`GarminConnectAdapter.push_planned_workout` (`adapters/garmin_connect.py`), which uploads a real
Garmin `RunningWorkout` (`planned_workouts.py::build_running_workout`) and schedules it on the
athlete's calendar via `schedule_workout()`. The athlete authors a workout as free text
(`Warmup 10m`, `4x` repeat blocks, `5:00-5:20/km Pace`, `Z2 HR`, trailing `170-180spm` cadence --
a real subset of intervals.icu's own workout-builder syntax), parsed by
`workout_syntax.py`/`workoutSyntax.ts` -- one authoritative Python parse on save, one TS twin for
an instant client-side preview, both exercised against the same shared JSON fixture table
(`tests/fixtures/workout_syntax_cases.json`) rather than trusted to agree by inspection.

Push is automatic for anything due within `PERSEVERER_PLANNED_WORKOUT_PUSH_WINDOW_DAYS` (default
7) days, via `worker/main.py::run_daily_workout_push`, its own daily schedule right after the
Garmin sync; `POST /planned-workouts/{date}/push` covers a manual "push now" regardless of date.
Calendar UI: a new "Planned workout" section (`ScheduleWorkoutForm.tsx`) in both
`MonthView.tsx`'s expanded-day card and `DayViewPage.tsx` (`/day/:date`, the page
`DateNavigator`'s day picker actually links to) plus a month-grid day indicator;
`ActivityDetailPage.tsx` gets a "Copy workout" button (`CopyWorkoutButton.tsx`) that writes a
recorded activity's steps, converted back
to syntax text, into a localStorage clipboard (`workoutClipboard.ts`) any calendar day's "Paste"
action can read; `StepBuilderModal.tsx` is a GUI wizard alternative to typing the syntax by hand,
generating text and inserting it at the textarea cursor rather than maintaining separate state.
See `docs/adr/0015-scheduled-workouts.md` for the full design and the vendor-API facts (verified
directly against the installed `garminconnect` package's own source) this was built against.

**Yoga/bouldering placeholders** (added right after, per the user's own follow-up scoping): no
structured syntax at all -- `planned_workouts.py::PLACEHOLDER_SPORTS` skips
`workout_syntax.py` parsing entirely, taking a `duration_minutes` and a display-only
`scheduled_time` ("HH:MM", new `planned_workout` column) directly instead. Pushed via
`build_placeholder_workout` -- a single no-target step spanning the whole duration, wrapped in a
plain `BaseWorkout` (yoga gets a real Garmin sport type; bouldering -- really a rock-climbing
sub-discipline, same taxonomy this app already uses for *recorded* activities via
`garmin_activity_summary.py`'s `sport="rock_climbing"`/`sub_sport="bouldering"` pair -- has no
slot in the Workout Builder's own separate sport list at all, confirmed live against
`GET /workout-service/workout/types`, so it falls back to `SportType.OTHER`). This surfaced two
real bugs, both fixed:
`GarminConnectAdapter.push_planned_workout` called the sport-specific `upload_running_workout`
(which rejects anything but a real `RunningWorkout`, confirmed by reading the check inside the
installed package) -- switched to the generic `upload_workout(workout.to_dict())`; and
`GET /planned-workouts/{date}` unconditionally re-parsed `source_text` for parse errors
regardless of sport, producing bogus errors on yoga/bouldering's freeform notes -- gated to
`sport == "running"` only. See `docs/adr/0015-scheduled-workouts.md` decision 9.

**`planned_workout_step.comment` column** (added later): a freeform note on **one specific step**,
stored verbatim, never parsed further or pushed to Garmin. Two authoring paths, per sport tier:

- **running**: an inline trailing `# comment text` on that step's own `source_text` line --
  `workout_syntax.py`'s grammar extension (`_split_comment`, applied before the rest of the line
  is tokenized): only the first `#` starts the comment, further `#` characters are just comment
  text. Works on a standalone `<N>x` repeat-marker line too, attaching to that block's own
  summarizing `planned_workout_step` row. A line that's *only* a comment (nothing left once the
  `#...` part is removed) is not a supported feature -- it falls through to the ordinary "missing
  duration" error, the same as any other content-free line, since a comment is only ever an
  annotation on a real step. Mirrored token-for-token in `workoutSyntax.ts` for the live preview.
- **hiit/strength_training**: typed directly into a small input on that exercise/rest row in
  `ExerciseStepEditor.tsx` (`ExerciseEntry.comment`), or on a "set"/`ExerciseGroup`'s own head row
  (`ExerciseGroup.comment`, which maps to its `repeat_until_steps_cmplt` marker row -- **lost** if
  the set's repeat count stays at 1, since no marker row is ever emitted then; a known, accepted
  limitation, not special-cased).

Yoga/bouldering get neither -- they already have an equivalent via their own freeform
`source_text` ("Notes"). `save_planned_workout`'s step-insert loop stores it as a plain
passthrough (`getattr(s, "comment", None)`, the same pattern every other type-specific field
there already uses), so both `workout_syntax.ParsedStep` (running) and `planned_workouts.
PlannedStepLike` (hiit/strength) feed it identically; `POST /planned-workouts/recurring` applies
whatever `source_text`/`steps` already carry to every created occurrence, comments included.

**`planned_workout.comment` column** (added later still): a second, separate general note for
the *whole* workout, read before any step -- distinct from the per-step column above. Running/
hiit/strength_training only (yoga/bouldering excluded, same "already has `source_text` as
freeform notes" reasoning). Stored verbatim, never parsed, but it *is* pushed: `push_planned_
workout` sets it as the built `BaseWorkout`'s own `description` field -- every workout class
(`RunningWorkout`, `StrengthWorkout`, etc.) subclasses `garminconnect.workout.BaseWorkout`, which
already has a real `description: str | None` field (confirmed by introspecting the installed
package), so this needs no synthetic step. `save_planned_workout(..., comment=...)` is a plain
parameter alongside `scheduled_time`, threaded through `PlannedWorkoutIn`/`Out`/
`RecurringWorkoutIn` the same way. Also surfaces in the iCal feed (`calendar_feed.py`, prepended
ahead of the existing per-sport description) and the weekly summary email's coming-workouts
section (`email_reports.py`).

**Compliance, sport by sport (Week view)**: a "Compliance" card on `WeekView.tsx`, one stat tile
per sport with at least one scheduled workout in the viewed week -- `{Sport} compliance`, the
percentage of that sport's scheduled workouts counted as done (e.g. "Running compliance: 67%",
meta "2 of 3 done"). Count-based, not distance- or load-weighted: a plain count is the one signal
every sport tier carries identically, since `planned_workout_stats.py`'s distance/load estimate
exists only for running (`estimated_distance_m`/`estimated_load` are always `null` for
yoga/bouldering/hiit/strength_training). Only workouts with `local_date <= today` count toward
either the numerator or denominator -- a workout later in the same week that hasn't happened yet
would otherwise drag down a week that's still in progress; a fully future week (every scheduled
date past today) correctly produces no compliance entries at all, and the whole card is omitted
(not shown empty) in that case. `GET /planned-workouts?start_date=&end_date=`
(`PlannedWorkoutListItemOut`, the same summary-list endpoint the Month view's own per-day grid
indicator already uses) carries two fields for this: `completed_at` and `matched_activity_id` --
the one bulk-range call the Week view needs, rather than the per-day `usePlannedWorkoutsForDate`
hook its own `WeekDayPlannedWorkouts` sub-component already calls seven times for full per-day
workout detail.

"Done" is `completed_at != null OR matched_activity_id != null` -- two independent signals, not
one. `completed_at` stays exactly what it always was: the athlete's own manual "I did this"
marker, set/cleared only via `POST .../complete`/`.../uncomplete`, with no link to any `activity`
row stored on the `planned_workout` row itself (`db/schema.py::planned_workout`'s own docstring).
`matched_activity_id` (`planned_workouts.py::matching_activity_id`, backed by
`activities_by_local_date` -- one query for the whole requested date range, matched in Python
against every planned workout in it, the same "fetch once" precedent `race_readiness.py`'s own
weekly queries already establish) is a second, read-time-only, **never persisted** signal added
after the first version's manual-only design turned out to be real friction: a workout Garmin had
already recorded still showed as not-done until the athlete separately clicked "Mark as done."
`activity_matches_planned_sport` decides whether a same-day recorded activity's own
`(sport, sub_sport)` plausibly satisfies a planned workout's sport tier -- not a literal
`sport == sport` check, since several tiers are recorded under FIT's generic `"training"`
container sport with the real discipline only in `sub_sport` (confirmed against
`garmin_activity_summary.py`'s own `GARMIN_ACTIVITY_TYPE_MAP`: yoga -> `(training, yoga)`,
strength_training -> `(training, strength_training)`, bouldering -> `(rock_climbing, bouldering)`)
-- running instead reuses `merge/engine.py`'s own `sport_family()` so a trail/treadmill/track run
still satisfies a plain "running" plan, and hiit/strength_training each accept either shape (a
literal top-level sport, or the `"training"` container with a matching `sub_sport`), since real
activities of both shapes exist in this project's own data. Never written back to
`completed_at`, and never overrides what the athlete explicitly set there -- a manual "not done"
still reads as not-done for the toggle itself (see the frontend note below), even while the
broader "is this done" question the Compliance card asks says otherwise. The Day/Month view's own
"Done" badge (`ScheduleWorkoutForm.tsx`) reflects the same combined signal, labeled plain "Done"
when `completed_at` is set and "Done (via Garmin)" (with an explanatory tooltip) when only the
match is present -- but the "Mark as done"/"Mark as not done" toggle button itself still only
ever reads `completed_at` alone: basing its own label on the combined signal would let a
matched-only workout's button read "Mark as not done" while actually being unable to clear the
match itself, a promise the click couldn't keep.

**Revision: the `lap` duration keyword** -- a running step's duration may be the keyword `lap`
instead of a time or distance, ending it on Garmin's own `ConditionType.LAP_BUTTON` (the watch
advances only when the athlete physically presses the lap button) rather than any GPS-derived
threshold. The athlete's own concrete failure: four downhill long runs are out-and-backs whose
turnaround is a real landmark (a dam), not an exact distance, and GPS on that trail isn't accurate
enough -- authored as `5km` steps, the watch would swap from the climb's HR target to the
descent's pace target while still climbing, exactly what the climb's own HR cap exists to prevent.
`lap` may be followed by an ordinary duration token (`lap 5km`, `lap 40m`) kept purely as a
calendar-side estimate (`duration_time_s`/`duration_distance_m`, no schema change -- both existing
estimate helpers already read those fields without branching on `duration_type`) and **never**
forwarded to Garmin as an end condition (`endConditionValue` stays `None` for a `lap_button` step)
-- if it were, the step would advance at 5 km and the whole feature would be pointless.
`LAP_BUTTON_CONDITION_ID` is read via `getattr`, not a plain attribute: the installed
`garminconnect` renumbers `ConditionType` between releases (an older release had no `LAP_BUTTON`
member at all and numbered `DISTANCE`/`HEART_RATE` differently), so a future bump that drops or
renames the member degrades to the known wire id instead of raising `AttributeError` mid-push. A
real gap this left behind, caught only once the athlete tried to use the feature: `StepBuilderModal.tsx`
(the "Add step" GUI wizard) never got a matching "Lap button" option in its duration-type
dropdown, so the keyword could only be authored by typing the syntax by hand until that was fixed.
See `docs/adr/0015-scheduled-workouts.md` decision 13 for the full reasoning.

## Calendar feed: publishing planned_workout to Google Calendar

`calendar_feed.py` builds a public iCalendar (RFC 5545) feed of the athlete's own `planned_workout`
calendar, so it can be subscribed to from Google Calendar (Settings > Add calendar > From URL) or
any other .ics-reading client. Deliberately a parallel mechanism to `share_link`
(`sharing.py`/`api/routers/share.py`), not a third `target_type` grafted onto it -- see
AGENTS.md's own bullet for the full reasoning. Two new nullable columns on `athlete`:
`calendar_feed_token_hash`, `calendar_feed_created_at` -- mirroring `athlete.api_key_hash`/
`api_key_created_at`'s exact shape (one standing secret, replace-on-rotate, `NULL` = not
published), not a growing history of tokens.

**Token mechanics**: `secrets.token_urlsafe(32)` + SHA-256 hex digest, same crypto
`auth/api_keys.py` already uses for the per-athlete API key, duplicated as two small local
functions in `calendar_feed.py` rather than imported -- that module is semantically scoped to
REST API-key auth, a different domain from a calendar-feed secret, matching this codebase's own
precedent of duplicating small helpers rather than cross-importing unrelated modules. The raw
token is shown exactly once, at publish/rotate time (`POST /settings/calendar-feed`) -- never
recoverable again; losing it means rotating to a fresh link.

**Feed content**: every `planned_workout` row for the athlete, all dates (no lower bound) -- the
table only exists since ADR 0015, so it's already small. One `VEVENT` per row:

- `UID`: `f"planned-workout-{row.id}@perseverer"`. Safe to use the raw autoincrement `id` directly
  because `planned_workout`/`planned_workout_step` are **not** in `rebuild.py::
  _REBUILDABLE_TABLES` (pure user input, never replayed from the raw archive) -- their `id`s are
  permanently stable across any `sync rebuild`, unlike a rebuildable table's own autoincrement ids.
- **Timed vs. all-day**: if `scheduled_time` is set, a timed event using the athlete's own stored
  `athlete.timezone` (a real IANA name, via `zoneinfo.ZoneInfo`) for `DTSTART`, with `DTEND` =
  start + `estimated_duration_s` (defaulting to **60 minutes** if duration is unknown -- a labeled
  judgment call, not derived from anywhere). If `scheduled_time` is unset (`scheduled_time` is
  genuinely optional per ADR 0015 -- Garmin's own push has no time-of-day API either), an honest
  all-day `VALUE=DATE` event spanning just `local_date`, rather than guessing a time.
  `Calendar.add_missing_timezones()` (icalendar >=7.3.0) is called before serializing, generating
  a real `VTIMEZONE` block with correct DST rules for any `TZID` actually used -- verified
  empirically against the installed version (not assumed) that without this call, a timed event's
  `DTSTART`/`DTEND` would carry a bare `TZID=` parameter with no accompanying zone definition,
  under-specified per RFC 5545 even though major clients tolerate a well-known IANA `TZID` alone
  in practice.
- **`SUMMARY`**: `"{Sport label}"`, or `"{Sport label}: {name}"` if the athlete set one. Sport
  labels come from a small local dict in `calendar_feed.py` (no existing backend sport-label
  mapping to reuse -- the frontend's icon/tone system, `metricStyle.ts`, is frontend-only).
- **`DESCRIPTION`**, per sport tier: running/yoga/bouldering already have a human-readable
  `source_text` (the athlete's own workout-syntax text or freeform notes respectively -- see
  `planned_workouts.py::save_planned_workout`'s own docstring), used verbatim. hiit/
  strength_training never has `source_text` at all ("steps arrive already-structured, never
  parsed from text" -- ADR 0015), so `_render_exercise_description` is a small purpose-built
  renderer: one line per real exercise/rest step (`"{Exercise name} — {reps or duration} @
  {weight}kg"`), with an `"Nx:"` header (matching running's own `<N>x` repeat-block wording)
  inserted ahead of any block a `repeat_until_steps_cmplt` marker covers. Deliberately not a reuse
  of `workout_syntax.py::steps_to_source_text` -- that function is shaped for *recorded* activity
  steps (`RecordedStepLike`: pace-only "speed" target, no exercise/reps/weight fields at all), a
  different domain from `planned_workout_step`'s own reps/exercise/weight columns. Each step's own
  `comment` (added later -- see the Scheduled workouts section above), when set, is appended to
  that step's own line; a group's own comment is embedded in its `"Nx:"` header
  (`"Nx: {comment}"` vs. the bare `"Nx:"`). Running/yoga/bouldering need no code of their own for
  this at all -- an inline `#` comment the athlete typed is already part of `source_text`'s raw
  text, flowing straight through to `DESCRIPTION` verbatim.

**Routes**: `GET/POST/DELETE /settings/calendar-feed` (authenticated, `api/routers/settings.py`,
same file/pattern as the existing hr-zones/running-load config endpoints) publish/rotate/
unpublish -- `POST` always mints a fresh token whether this is the first publish or a rotation,
since "give me a current link" is the only operation that ever makes sense here. The public feed
itself is a separate, unauthenticated route: `GET /share/calendar/{token}.ics`
(`api/routers/calendar_feed.py`, mounted with `prefix="/share"` in `api/main.py`) -- reuses the
existing `location /share/` nginx prefix rule (`docker/nginx.conf`) with zero infra change, same
"public by omission" convention `share.py` already established (a route is public purely by never
taking `Depends(require_api_key)`).

Frontend: `CalendarFeedCard.tsx` (Settings page), modeled on `RebuildCard.tsx`'s status-query-
plus-mutation shape -- button label swaps "Publish calendar" / "Rotate link" based on current
status, revealing the fresh URL inline (via the same `share-button__url-row` markup
`ShareButton.tsx` already uses for activity/period shares) only immediately after a publish/
rotate, matching this codebase's "raw secret shown once, never re-shown" posture for every other
hashed token.

## Weekly / monthly email reports (email_reports.py)

Opt-in training digests emailed to an athlete's own `athlete.email`: **weekly** (Sunday 18:00
local — the Mon–Sun week that just ended, activity totals + a per-sport breakdown, plus the
coming Mon–Sun week's planned workouts) and **monthly** (month's last day 18:00 — that calendar
month's totals, no planned-workout section). Two `APScheduler` jobs in `worker/main.py`
(`run_weekly_email_report` / `run_monthly_email_report`), resolved against
`PERSEVERER_SCHEDULE_TIMEZONE` like the sync/backup/push jobs.

- **`athlete_email_report_config`** — one row per athlete (upsert, mirrors
  `athlete_hr_zone_config`'s shape/contract exactly): `weekly_enabled` / `monthly_enabled`
  Booleans, both default `False`. No row, or a row with both `False`, means "no emails" — the
  default. This table holds *only* the two switches: the SMTP relay is deployment-global
  (`PERSEVERER_SMTP_*`, `config.py`), and the recipient is the athlete's own `athlete.email`
  (Settings → Profile).
- **Totals** come straight from `period_rollup` (the sanctioned aggregate — already consistent
  with the calendar grid). The **per-sport split** is one extra bounded `activity` query over
  the period's date range (`period_rollup` doesn't store it). **Coming-week running workouts**
  are enriched with the same distance/duration/load estimate the calendar UI shows
  (`planned_workout_stats.estimate_workout` over a fresh `workout_syntax.parse_workout_syntax`
  of `source_text`).
- **Delivery** (`email_delivery.py`): stdlib `smtplib` + `EmailMessage`, `multipart/alternative`
  (email-safe inline-styled HTML + a plaintext part). `PERSEVERER_SMTP_SECURITY` picks the wire
  mode — `starttls` (port 587, mail submission), `ssl` (port 465, implicit TLS), or `none` (a
  local unauthenticated relay). All of host/username/password/from must be set or the jobs and
  the "send test email" button log-and-skip (same graceful-degradation contract as Eufy/backup).
- **A send reads the local DB**, whose newest Garmin data is from that morning's 04:15 sync — so
  the send day's own activities may not be counted yet; the email footer says as much.
- **Routes** (`api/routers/settings.py`): `GET/PUT /settings/email-reports` (the two switches +
  read-only context: `smtp_configured`, `recipient_email`), `POST /settings/email-reports/test`
  (sends the current weekly report immediately — 400 if SMTP or the Profile email isn't set, 502
  on send failure). Frontend: `EmailReportsCard.tsx` (Settings → External tools).
- **Weekly-only additions: a running-distance bar chart, average pace, and a steps bar chart** —
  all Mon–Sun of the week that just ended, the monthly report untouched. `running_distance_by_day`
  and `steps_by_day` are `DailyMetricPoint` lists (one entry per calendar day, `value: None` —
  never a fabricated 0 — when nothing was recorded that day); `avg_running_pace_s_per_km` is the
  week's total running distance over total moving duration, exact `sport == "running"` match (not
  `sport_family()`, same precedent as every other running-specific stat in this app). Steps come
  from `health_metric_daily_rollup` via the same alias list/priority as `api/routers/health.py::
  LOGICAL_METRICS["steps"]` (`garmin.daily_summary.totalSteps` before `garmin.export.UDSFile.
  totalSteps`), duplicated as `_STEPS_ALIASES` rather than imported — this module doesn't depend
  on the API layer, same precedent `insights/engine.py::_RESTING_HR_ALIASES` already established.
  Both bar charts are entirely omitted (not rendered as an all-empty chart) when there's nothing
  to show that week. Rendered as one row per weekday with a horizontal bar (`_bar_rows`/`_bar`) —
  a real, confirmed-live rendering trap: a percentage-width `<div>`, and even a percentage-width
  nested `<table width="100%">`, both collapse to 0px rendered width when their containing `<td>`
  has no width of its own for the outer table's auto layout to resolve a percentage against (a
  `&nbsp;`-only cell gives that algorithm no real content to size from). Only literal **pixel**
  widths (`_BAR_TRACK_PX`, applied to both the containing `<td>` and the nested bar table) render
  correctly — this class of bug is invisible to a plain string assertion on the rendered HTML
  (both broken versions still had "sensible-looking" percentage markup); it only surfaced by
  actually rendering the HTML and reading `getBoundingClientRect()`, the same way this codebase's
  own `.time-in-zone__fill` bug (share pages) was originally caught.
- **The "By sport" breakdown's own real bug this pass fixed**: a recorded yoga/strength/
  breathwork session is stored `sport="training"`/`sub_sport="<real type>"` (Garmin's FIT taxonomy
  uses "training" as a generic container for all three), so grouping by the raw `sport` column
  showed "Training" instead of "Yoga" in both the weekly and monthly report. `_sport_breakdown`
  now groups by `_display_sport(sport, sub_sport)` — a duplicated port of `frontend/src/
  yearStats.ts::displaySport`'s identical `GENERIC_CONTAINER_SPORTS` substitution, same precedent
  `sharing.py::_display_sport` already established for the same reason (module-private, tiny,
  duplicated rather than cross-imported).
- **A third weekly-only bar chart: sleep hours per day** (`_sleep_hours_by_day`) — Mon–Sun of the
  week just ended, same omit-when-empty / pixel-width-bar convention as running/steps above.
  Reads `sleep_session.total_sleep_s` (hours, converted at the point of use), grouped by
  `local_date` with `func.max`, not `sum` — `sleep_session` is unique on `(athlete_id, local_date,
  source)`, so more than one source could in principle report the same night, and summing would
  double-count it. A real overlap has never actually been observed in practice (same posture the
  frontend's own `HealthPage.tsx::sleepToDailyPoints` already takes — it doesn't merge sources at
  all), `func.max` is just the safe choice if one ever does.
- **Running distance vs. the week before** (`_running_distance_total_m`) — a second sum over the
  Mon–Sun immediately before the week the report covers, folded into the "Running this week" bar
  chart's own caption as two absolute figures (`"38.4 km vs 32.1 km the week before"`), not a bare
  delta — the same preference `WeekView.tsx`'s own `priorWeekMeta` already established for total
  distance (a delta with nothing to compare against was found less useful than the number it's
  relative to). `None` — the whole comparison line omitted — when there were no runs at all the
  week before, never a fabricated `0 km`.
- **A per-run table: `RunLine`/`_week_runs`** — one row per individual running *activity* in the
  week that just ended, not a per-day sum like the bar chart above (two runs on the same day are
  two rows here). Columns: Day, Distance, Pace, Duration. The week's own farthest distance,
  fastest pace (the lowest seconds/km, i.e. best), and longest duration are each bolded and
  accent-colored in their own column (`_highlight_cell`, a plain inline `<span>`, not a row
  background — no separate "highlighted row" color decision needed); a tie bolds every tied run,
  since "the week's fastest run" genuinely describes all of them equally when more than one ties.
  An activity missing either distance or duration is skipped (never a fabricated pace); the whole
  section is omitted, not shown empty, on a week with no qualifying runs.
- **Future races** (`_future_races`) — every `planned_race` after today, however far out on the
  calendar, unlike `coming_races` above (which stays scoped to just the coming Mon–Sun week and
  its own full target-vs-predicted comparison). Rendered as a quick-glance line per race: date
  (weekday, zero-padded day, abbreviated month, year), days-until in parens, the race name, and —
  only when a target time is set — a "goal" line with the implied pace (`target_duration_s /
  (distance_m / 1000)`, plain division, no prediction involved since a race this far out has no
  current prediction worth showing). `_clock_hm` renders the goal time as "4:00" for a round
  marathon-style goal (`_clock`'s own "4:00:00" with the trailing `:00` seconds dropped, but only
  when there's an hours component) while leaving a 5K/10K goal like "22:30"/"45:00" untouched,
  since those already carry real seconds precision worth keeping.
- **Coming week: per-day weather + the athlete's own notes** — the "Coming week" table used to be
  one row per *scheduled workout*, so a day with nothing planned had no row at all.
  `_coming_day_rows` renders one row per day of the coming Mon–Sun instead, always all seven: each
  day's own forecast (an emoji from `weather_code.py::weather_code_info` plus min-max temperature)
  shows even on a day with no workout, and that day's workout(s) — a day can hold more than one —
  render underneath it. `_coming_week_forecast` calls `weather_forecast.py::fetch_forecast`
  exactly as `GET /weather/forecast` does (un-cached, un-archived, the athlete's own
  `athlete.timezone`, never hardcoded UTC), sizing `days` to reach `coming_end` from `today` (the
  Sunday the job fires on) — `[]` when the athlete has no home location set or the fetch fails,
  never fabricated. The day-by-day table falls back to the original single "Nothing scheduled
  yet." row only when the whole week is bare (no workouts *and* no forecast at all), avoiding
  seven identical empty rows for an athlete with neither set up yet. A "Notes for the coming week"
  section, placed immediately before this table, reads the athlete's own `note` rows for the
  coming week (`entity_type="week"`, `entity_id` = that week's Monday — the same row
  `WeekView.tsx`'s own Notes card reads/writes), ordered oldest-first like `GET /notes`; omitted
  entirely, not shown empty, when the athlete hasn't written one for that week yet.

## Races on the calendar (planned_races.py)

A single upcoming race — name, date, optional time of day, distance, and an optional target
finish time — deliberately its own table, not a `planned_workout` sport tier: a race has no
step model to push to Garmin, it's just an event to look forward to and pace a goal against.

- **`planned_race`** — one row per race, own `id` (any number per athlete per date, same
  "addressed by id, not by date" convention `planned_workout` adopted after its own
  multi-per-day revision): `local_date`, `scheduled_time` ("HH:MM", nullable, display-only —
  never any Garmin push here at all, so there's no vendor time-of-day API constraint to work
  around, it's simply optional because not every race's start time is known when first added),
  `name`, `sport` (open string, default `"running"` — a cycling sportive or a swim event can be
  logged too, not just runs), `distance_m`, `target_duration_s` (the athlete's own goal finish
  time — "run in less than 4 hours" → `14400.0`; null means no target was set, the race still
  shows with just its distance/countdown).
- **Target vs. predicted finish time** (`planned_races.py::predicted_duration_s_for_distance`):
  reuses `performance_daily_rollup`'s own independently-computed race predictions
  (`performance_rollup.py`) rather than a second prediction path — the most recent
  `predicted_{5k,10k,half_marathon,marathon}_s` value for a `distance_m` that matches one of
  `vdot.RACE_DISTANCES_M` (small float tolerance for km↔m round-tripping). A custom distance (a
  15K, a 50-miler) gets no prediction at all — honest rather than extrapolated past
  `predict_race_time_s`'s own real search-bound limitation. The frontend shows this as
  "Predicted 3:52:10 — on track" (predicted faster than or equal to target) or "— N over target"
  otherwise.
- **`days_until`** and `predicted_duration_s` are computed fresh on every read (`api/routers/
  planned_races.py::_to_out`), never stored — a race's countdown obviously changes daily, and a
  stored prediction would go stale the moment a new performance rollup runs.
- **Routes**: `GET /planned-races?start_date=&end_date=` (range, for the Month grid), `GET
  /planned-races/by-date/{local_date}`, `POST /planned-races`, `GET/PUT/DELETE
  /planned-races/{race_id}` — the exact same id-keyed/by-date/range route shape
  `/planned-workouts` uses, for the same reason (any number of rows per date). Frontend:
  `PlannedRaceForm.tsx` (Day view's own "Race" card, and Month view's expanded-day card),
  code-split via `React.lazy` (not a static import — the app-shell bundle is already right at
  vite-plugin-pwa's 2MB single-file precache limit, the same constraint
  `ExerciseStepEditor.tsx::preloadExerciseCatalog`'s own docstring already documents once); a
  `trophy`-iconed chip in the Month grid cell and Week view (read-only there, same as a planned
  workout's own chip — Edit/Delete live on Day/Month view only).
- **Also surfaces in**: the iCal feed (`calendar_feed.py::_build_race_event` — timed vs. all-day
  exactly like a planned workout's own VEVENT, `target_duration_s` sized if set else a 4h
  default; description is `"{distance} km"` plus `" · target {clock}"` when set) and the weekly
  summary email's "Races this week" section (`email_reports.py`, target/predicted comparison
  included) — never the monthly email, which is stats-only.

## Race Readiness (race_readiness.py)

Has the athlete actually run enough *volume* for their next scheduled race, not just "are they
fit" — a materially different question from `planned_race`'s own VDOT-based prediction above
(`predicted_duration_s_for_distance`), which only answers what the athlete could run today at
their current fitness. This module answers whether they've put in the specific weekly mileage
and long runs a race of this distance actually calls for, and leaves the VDOT prediction
untouched, surfaced alongside as this module's own "prognosis" rather than re-derived or blended
into a new number.

- **Targets, by race distance** (`_WEEKLY_TARGET_ANCHORS`/`_LONG_RUN_TARGET_ANCHORS`): four
  anchor points (5k/10k/half/marathon distance → target), log-linear interpolated for anything in
  between (`_interpolate`) and clamped — never extrapolated — outside that range, same "honest
  rather than extrapolated" posture `predict_race_time_s`'s own search bounds already establish.
  Unlike VDOT, "how much should you run for a marathon" has no single physiological equation —
  real published plans disagree by roughly 2x depending on athlete level (Hal Higdon Intermediate
  peaks around 55 km/34 mi with a 29 km/18 mi long run; Pfitzinger Advanced plans peak past
  110 km/70 mi with a 32 km/20+ mi long run). This module deliberately targets the
  recreational/intermediate end (Higdon Novice/Intermediate, Daniels' Running Formula's own
  easier plans), not an advanced/competitive baseline, since the advanced number would read as
  "not ready" for the common recreational case this app is built for. The two targets are set
  independently per distance rather than derived from each other via one shared ratio — the real
  "long run as % of weekly volume" relationship itself varies by athlete level (roughly 25% at
  high weekly volumes to over 40% at recreational volumes), so no single ratio stays honest
  across the whole range.
- **Compliance is recency-weighted, not a flat average** (`_weighted_compliance`) — an
  exponential decay by days-ago (`0.5 ** (days_ago / half_life_days)`), mirroring the same EWMA
  philosophy `fitness_daily_rollup`'s own Coggan/Banister CTL(42d)/ATL(7d) already uses in this
  codebase. Weekly running distance looks back `WEEKLY_DISTANCE_WINDOW_DAYS` (182 days/26 weeks)
  with a `WEEKLY_DISTANCE_HALF_LIFE_DAYS` (28-day) half-life; the long run — this app's stand-in
  for a "long run" it has no separate tag for is simply that week's own single longest run —
  looks back a shorter `LONG_RUN_WINDOW_DAYS` (70 days/10 weeks) with a shorter
  `LONG_RUN_HALF_LIFE_DAYS` (14-day) half-life, since a taper's most recent long run matters far
  more than one from two months out. Each week is credited up to (never past) 100% of target — a
  week at 2x target isn't "200% ready," it's fully credited, same capping instinct
  `email_reports.py::_bar`/`_bar_rows` already apply elsewhere to a comparison against a target. A
  week with nothing recorded contributes a real `0.0` at its own full weight (not excluded from
  the average) — genuinely no running that week is genuinely 0% compliant for it, not unknown.
- **Combining the two into one readiness percentage** (`READINESS_WEEKLY_DISTANCE_WEIGHT` = 0.6,
  `READINESS_LONG_RUN_WEIGHT` = 0.4): overall weekly volume is the primary driver of
  endurance-race readiness in the same literature the targets come from (Daniels, Pfitzinger both
  treat total volume as the dominant training variable, the long run an important but secondary
  specificity factor) — hence weighted toward weekly distance. Like the target anchors, this is
  this app's own stated policy, not a claimed universal formula — an ordinary module constant,
  easy to revisit.
- **The prognosis is the existing VDOT-based prediction, reused as-is, never blended** — the same
  `predicted_duration_s_for_distance` value `planned_race` itself already surfaces. Shown
  *alongside* readiness rather than mathematically combined with it: a volume-adequacy fraction
  and a fitness-derived time have different physiological bases, and inventing a formula that
  adjusts one by the other would overclaim precision this app has no grounds for. `None` for a
  non-standard race distance, same as that existing field's own limitation.
- **Which race** (`_nearest_upcoming_race`): defaults to the athlete's own nearest upcoming
  `sport == "running"` race (`local_date >= as_of`); `GET /performance/race-readiness`'s own
  `race_id` query param targets a specific one instead. `available: false` (never fabricated)
  when there's no upcoming running race at all, or `race_id` doesn't belong to the caller.
- **Deliberately request-time, not rollup-backed** (AGENTS.md's rollup mandate) — the same
  "bounded, occasional diagnostic lookup" exception `vo2max_analysis.py`/`pace_hr_zones.py`
  already establish. Which race this even applies to can change day to day (a nearer race gets
  added, an old one passes), so there's no stable rollup-row identity to accumulate against; the
  whole computation, history included, is cheap enough (two queries total — running distance and
  the weekly-longest-run, both fetched once over a window wide enough to cover every historical
  `as_of` point's own lookback, with all bucketing/weighting done in Python) to redo on request.
- **History ("the evolution of this readiness over time")**: one point per week over the
  182-day weekly-distance window — a genuine backtest, the same weighted-compliance calculation
  re-run with `as_of` shifted back to each historical week and only data available up to that
  date used, never leaking future weeks into a past reading.
- **The realized numbers, not just the compliance percentage** (`WeekValue`,
  `_dense_weekly_series()`): `weekly_distance_series`/`long_run_series` are dense, zero-filled
  weekly series (one entry per Monday-start week, oldest first, over each series' own window —
  182 days for weekly distance, 70 days for the long run) carrying the actual realized
  `distance_m` a dedicated chart plots real bars for against a target reference line — `0.0`
  never omitted for a week with nothing recorded, the same never-fabricated convention `history`
  above already follows. Built from the same `weekly_totals`/`longest_runs` queries
  `compute_race_readiness` already fetches once for `history`'s own weighted-compliance
  calculation, not a third query.
- **`GET /performance/race-readiness`** (`race_id`/`as_of` optional query params) returns
  `available`, the race's own `race_id`/`race_name`/`race_local_date`/`race_distance_m`, both
  targets, `current` (today's own `RaceReadinessPointOut`: `weekly_distance_compliance_pct`/
  `long_run_compliance_pct`/`readiness_pct`), `predicted_duration_s`, `history` (a list of the
  same point shape, oldest first), and `weekly_distance_series`/`long_run_series` (a list of
  `RaceReadinessWeekOut`: `week_start`/`distance_m`). Frontend: `RaceReadinessChart.tsx`, a new
  Insights tab — a stat-tile row (readiness/weekly distance/long run/prognosis, the latter two
  also showing their own target distance as `meta` text) plus a `TrendChart` fed the `history`
  array directly (already pre-bucketed weekly server-side, unlike `Vo2maxChart.tsx`'s own raw
  daily series, so this skips `TrendControls`/`trendWindow.ts` entirely), and two dedicated
  `RaceVolumeBarChart.tsx` bar charts — one bar per week from `weekly_distance_series`/
  `long_run_series`, a dashed `ReferenceLine` at the matching target, the same "bars against a
  threshold" idiom `EddingtonBarChart.tsx` already establishes.

## Blood test results (blood_tests.py)

Athlete-entered lab results — one row per marker per draw, several rows sharing one `local_date`
forming one logical panel (a full lipid panel drawn the same day, say). Deliberately a plain CRUD
table, not the `health_observation` EAV pipeline every vendor adapter feeds into: that machinery
exists specifically to catalog *auto-discovered* fields from a raw vendor payload
(`metric_definition`'s own "never drop an unknown field" contract), but a blood panel is typed in
by the athlete directly — there's no raw byte stream to archive and no unknown-field problem to
solve, only a marker name and a value the athlete is entering themselves.

- **`blood_test_result`** — `athlete_id`, `local_date` (the draw date), `marker` (e.g. `"LDL
  Cholesterol"` — the athlete's own freeform label, not a fixed catalog this project maintains),
  `value_num`, `unit` (nullable, e.g. `"mg/dL"`), `reference_low`/`reference_high` (nullable,
  either or both — the athlete's own lab-reported range, copied from their report), `lab_name`
  (nullable), `notes` (nullable).
- **Reference ranges are informational only, never a clinical claim**: this project deliberately
  does *not* maintain a "normal range" catalog (unlike, say, `pace_bands.py`'s own fixed pace
  bands) — ranges genuinely vary by lab, assay, sex, and age, and asserting a canonical one here
  would overstate what a personal data archive should claim. The one thing this app does with a
  stored range is flag a value that falls outside the athlete's *own* stated range (a simple
  `value < reference_low OR value > reference_high` check, `BloodTestsPanel.tsx`), never assess
  or diagnose.
- **Routes** (`api/routers/blood_tests.py`): `GET /blood-tests?start_date=&end_date=` (range,
  ordered by `local_date` descending then `marker`), `GET/PUT/DELETE /blood-tests/{result_id}`
  (single marker), `POST /blood-tests` (single marker — for adding one more to an existing draw),
  `POST /blood-tests/batch` (the primary write path: one draw date, any number of markers, in one
  call, sharing one `lab_name`/`notes`), `DELETE /blood-tests/by-date/{local_date}` (the whole
  panel at once, rather than the athlete removing each of a panel's markers one by one).
- **Frontend** (`BloodTestsPanel.tsx`, Health page): a plain sibling section rendered below the
  metric-explorer trend charts, not woven into `MetricExplorer`'s own list+`TrendControls` shape
  — a blood panel is an event carrying many named markers, not a single continuous metric to
  chart over time. Panels grouped by draw date into collapsible `<details>` (most recent open by
  default), each a table of marker/value/unit/reference-range with the out-of-range flag above;
  per-row inline Edit (the same reveal-in-place editing `NotesPanel.tsx` already established) and
  Delete; a collapsed-by-default "+ Add blood test" form (same reveal-on-click convention
  `NotesPanel.tsx` established for its own new-note textarea) with dynamic marker rows
  (add/remove) since a real panel typically has many markers entered at once, submitted via the
  batch route in one call.
- **A real bug this surfaced**: `formatDate` initially called `toLocaleDateString` on a
  UTC-midnight `Date` (from `parseIsoDate`) with no `timeZone: "UTC"` option, which silently
  rolls the *displayed* date back one day for any athlete west of UTC — confirmed live (entering
  `"2026-09-13"` rendered back as `"Sep 12, 2026"`) and fixed the same way `RunningStats.tsx`'s
  own `formatShortDate` already had to for the identical parse-then-format shape. Pinned down
  with a regression test asserting the exact formatted string rather than just that *some* date
  renders — this repo's own test environment defaults to `America/Los_Angeles`, so the test
  genuinely exercises the bug rather than passing by accident of the runner's own timezone.
- **The marker field is a dropdown built from the athlete's own history, plus a small bundled
  name list -- never a hardcoded reference range** (`buildMarkerCatalog`, `COMMON_MARKERS`,
  `BloodTestsPanel.tsx`): `marker` stays freeform text in the database (no catalog table, no
  foreign key — the "Reference ranges are informational only" bullet above applies exactly as
  much to marker names themselves), but the add form now offers a `<select>` of every distinct
  marker name already present in the athlete's own fetched history, unioned with `COMMON_MARKERS`
  (~24 plain names -- Total Cholesterol, HbA1c, TSH, Vitamin D, and the like), sorted
  alphabetically. Since `GET /blood-tests` is already ordered `local_date` desc, the first row
  seen for a given marker name in that same array is already its own most recent entry, so no
  extra sort or query is needed to know which unit/reference range to carry forward. Choosing a
  marker from the athlete's own history auto-fills unit/reference low/high from that most-recent
  row -- still plain, independently editable `<input>`s afterward, the same as a freshly-typed
  value would be, since a different lab or a genuinely revised range is exactly as real as the
  first one; choosing a bundled-list name the athlete has never entered before fills in nothing
  (there's no personal data behind it yet). `COMMON_MARKERS` carries names only, never a unit or
  range -- that would be exactly the "normal range this app asserts" the reference-range
  principle above forbids. It exists because the athlete's-own-history-only version of this
  dropdown, confirmed live, left a brand-new athlete with no dropdown at all on their very first
  entry (an empty `<select>` with nothing but "+ New marker…" seemed like a worse experience than
  a small standard list to start from) -- a real gap the bundled list closes, not a hypothetical
  one. A "+ New marker…" option (a sentinel `<option>` value, not a real marker string) switches
  that row to a free-text `<input>` instead, with a small "Choose existing" link back, for
  anything not in either list.

## Performance Curve: best sustained pace/GAP/heart rate across a date range (performance_curve.py)

A Runalyze-style "Heart Rate Curve"/cycling "Critical Power Curve": for a chosen metric (pace,
GAP, or heart rate) and a chosen date range, the single best sustained value for each of a fixed
set of durations — the best D-second window *anywhere* across every qualifying activity, not one
activity's own average — plotted duration (log x-axis) against best value. New algorithmic
territory for this app: no sliding-window "best effort of duration D" primitive existed anywhere
before this feature (`runningStats.ts::personalRecords`'s own docstring already documents this
exact gap, working around it by taking the fastest whole *activity* instead of a real
sub-window). `GET /performance/curve` is request-time, not rollup-backed — the same
bounded/occasional-lookup exception `vo2max_analysis.py`/`pace_hr_zones.py`/
`race_readiness.py` already establish, since which activities qualify changes with every new
date-range/sport-filter combination rather than accumulating against a stable rollup-row
identity.

- **Duration buckets** (`performance_curve.py::DURATION_BUCKETS_S`): `1, 5, 10, 15, 30, 60, 120,
  180, 300, 600, 900, 1200, 1800, 2700, 3600, 5400, 7200` seconds (1s through 2h) — a superset of
  the reference product's own shown labels. A bucket longer than an activity's own duration is
  simply skipped for that activity, never extrapolated.
- **`best_window_over_stream`**: the core primitive, a two-pointer O(N)-amortized scan per
  duration bucket over one activity's own timestamp/value stream — both the window start and end
  only ever move forward across the whole scan, since the minimal window end needed to reach a
  given duration is non-decreasing as the start advances. Two windowing conventions, deliberately
  different per mode, not one shared one:
  - `mode="mean"` (heart rate): duration D means D *samples*, the standard GoldenCheetah/
    TrainingPeaks convention — the window is right-exclusive `[i, j)`, averaging `j - i` samples.
    At ~1Hz sampling, a naive "smallest window whose elapsed span reaches D seconds" touches
    D+1 samples, not D — a real off-by-one bug caught by two failing unit tests before this
    convention was fixed.
  - `mode="rate"` (pace/GAP): a real physical rate (distance / actual elapsed time), so no
    sample-counting convention applies — the window stays inclusive `[i, j]`, dividing the
    distance covered by the real elapsed span (which may exceed D within tolerance).
  - **Gap disqualification, never silent bridging**: a candidate window is rejected if reaching
    `duration_s` needed a span more than 10% longer (`_SPAN_TOLERANCE`) than the target, or if any
    single inter-sample gap inside the window's actually-averaged range exceeds `_MAX_GAP_S`
    (15.0 seconds) — checked via binary search (`bisect_left`) against a once-per-call
    precomputed list of the stream's own gap positions, not recomputed per window. A gap under
    the threshold is tolerated — an explicit, adjustable app policy, the same never-fabricate
    discipline `race_readiness.py`/`weather.py` already apply elsewhere in this app. Returns
    `None` (never a fabricated value) when no valid window exists for that duration.
- **GAP reuse**: `gap.py::compute_gap_adjusted_distances` was extracted from
  `compute_avg_gap_speed_mps`'s own inner loop (an efficiency-preserving refactor —
  `compute_lap_gap_speeds_mps` now precomputes this per-interval array once and passes it into
  each per-lap call via an internal-only `_per_interval` parameter, avoiding an
  O(N) → O(N × lap_count) regression a naive refactor would introduce). The Performance Curve
  module cumsums this into a GAP-equivalent cumulative-distance array and feeds it through the
  exact same sliding-window path as plain pace.
- **Sport scope**: `pace`/`gap` are running-only always, regardless of any `sports` query
  param — matching this app's own established exact-`sport=="running"` convention for every
  other pace feature (GAP's Minetti cost-of-running model has no meaning for other gaits).
  `heart_rate` instead takes an athlete-chosen `sports` filter, since a hard bike ride or hiit
  session is a real sustained HR effort too.
- **One combined DuckDB query across every qualifying activity's own Parquet file**
  (`read_parquet($1, filename=true)` bound to a Python list of paths) — a new pattern for this
  codebase; every prior Parquet read (including `stream_query.py`) was one
  `read_parquet(single_path)` call per query. Confirmed empirically (not assumed) that the
  installed DuckDB version (1.5.5) accepts a bound Python list for this parameter. Rows are
  grouped by filename in Python; `activity_trim_override` windows are honored per activity
  (relative to that Parquet file's own first-recorded-sample epoch) before the sliding-window
  search runs, so a trimmed-out stretch of car travel can't set a nonsensical short-duration
  record.
- **Verified against real data, not assumed fast**: a first, pure-Python version of both
  `best_window_over_stream` and `gap.py::compute_gap_adjusted_distances` (a pre-existing function
  this feature calls once per activity for GAP) measured 24-40s for the "all time" preset over
  this app's own real multi-year history (~1,000 running activities) — past the bar this app
  already set with the `ix_activity_metric_athlete_key` precedent above. Both were rewritten with
  numpy: the same two-pointer/widening-window boundary logic, just found independently per index
  via `np.searchsorted` rather than a per-index Python while-loop (mathematically identical for a
  sorted timestamp array — verified against the existing unit tests plus a 200-trial randomized
  property check comparing scalar and vectorized output index-by-index, not just the hand-crafted
  cases). Real measured figures after: under 2s for "last 3/6 months", 3-4s for "last year", and
  8-15s for "all time" (pace fastest, GAP slowest — it alone also runs the grade-smoothing pass).
- **Reference values, shown alongside, never reconciled**: the athlete's own already-computed
  threshold pace/HR (`performance_daily_rollup`, a plain "exact `as_of`-dated row" read) are
  returned purely for the frontend to draw as dashed reference lines — never blended into the
  curve itself, the same posture Race Readiness's own VDOT-based prognosis already establishes
  toward its own readiness percentage.
- **Frontend** (`PerformanceCurveChart.tsx`, an Insights tab): metric selector (Pace/GAP/Heart
  rate), a date-range preset dropdown (Last 3 months/6 months/Year/All time — no custom from/to
  pickers, a deliberate v1 scope cut), HR-only sport checkboxes derived from the athlete's own
  real activity history (`useAllActivities({})`, never a hardcoded sport catalog — the same
  "derive from real data" precedent `buildMarkerCatalog` establishes for blood-test markers),
  default all checked. A Recharts `ComposedChart` with a log-scale `XAxis` (duration spans 1s–2h,
  four-plus orders of magnitude) and dashed `<ReferenceLine>`s for whichever threshold values the
  active metric has — each passing `ifOverflow="extendDomain"`, since Recharts' own default
  (`ifOverflow="discard"`) silently drops a reference line that falls outside the curve's own
  auto-computed value domain, a real rendering bug this feature's own tests caught by asserting
  an exact reference-line count rather than just "at least one." Stat tiles for the 20-min and
  60-min best (each omitted, not zero, when that duration has no qualifying data yet), plus one
  plain comparison sentence under them when both the 60-min value and the matching threshold
  value exist (e.g. "Your 60-min best is 8s/km faster than your computed threshold pace") —
  phrased as a plain observation, never a new blended metric.

## Running Eddington number, per year (eddington.ts)

The largest integer E such that the athlete completed at least E runs of at least E km each in a
given calendar year — a classic cycling-logging statistic (VeloViewer and others use it for
rides), applied here to running. A new Insights tab (`EddingtonChart.tsx`), not a new backend
endpoint or table: computed entirely client-side over the same full running-history fetch
(`useAllActivities({ sport: "running" })`) this page's own Pace trends tab already performs — the
same "fetch once, aggregate in the browser" precedent `runningStats.ts`'s own
`bestVdot`/`personalRecords`/streak logic already established, extended to a statistic that page
didn't have yet.

- **The algorithm** (`eddington.ts::computeEddingtonNumber`) is mathematically identical to the
  h-index: sort a year's distances (km) descending, and E is the largest N whose Nth-largest
  value (1-indexed) is itself `>= N`. Raw `distance_m`, not GAP-adjusted — Eddington number is
  traditionally a real-distance-covered statistic, not an effort-adjusted one. Exact
  `sport === "running"` via the same API filter `useAllActivities` already applies for this page's
  other tabs, matching this app's own established precedent (`RunningStats.tsx`, `AGENTS.md`'s
  own note on the Running section above) of exact-sport matching over `sport_family()` for
  running-specific stats — trail_running/track_running are real, deliberate exclusions, not an
  oversight.
- **Progress to next** (`runsTowardNext`/`runsNeededForNext`): how many of a year's runs already
  meet the *next* Eddington number's own distance threshold, and how many more such runs are
  needed to actually reach it. A mathematical invariant of the h-index algorithm guarantees
  `runsTowardNext` can never reach the next threshold on its own — if it did, that threshold would
  already *be* the current Eddington number, not the next one — so `runsNeededForNext` is always
  `>= 1`, never zero, regardless of how much distance the athlete has already banked at the
  current level.
- **The current-year bar chart** (`eddington.ts::computeEddingtonBars`,
  `EddingtonBarChart.tsx`) — the classic VeloViewer-style visualization: one bar per integer km
  from 1 to the current year's longest run (rounded up), height = how many of that year's runs
  reached at least that far. A non-increasing step function by construction (every run counted at
  km also counts at every smaller km), rendered as a Recharts `ComposedChart` — a `<Bar>` colored
  green per-bar while `count >= km` (`km <= that year's Eddington number`) and red once it falls
  short, plus a dotted `<Line>` tracing `y = x` (Recharts' `<ReferenceLine>` only draws
  horizontal/vertical lines, not a diagonal) — the bar curve's crossing point with that diagonal
  is the Eddington number itself, made visible rather than only tabulated. Only the current
  calendar year gets this chart; every year still gets its own row in the table above.

## PR progress, per week (prProgress.ts)

Has this year's pace actually improved on last year's, at every distance, not just the athlete's
own named-distance PR table? A new Insights tab (`PrProgressChart.tsx`), not a new backend
endpoint or table: computed entirely client-side over the same full running-history fetch
(`useAllActivities({ sport: "running" })`) the Pace trends and Eddington tabs already perform,
same "fetch once, aggregate in the browser" precedent.

- **One dot per ISO week** (`prProgress.ts::prProgress`): the week's (Monday-Sunday) single
  highest-VDOT running activity represents that week, mirroring `PaceTrendsChart.tsx`'s own "gold
  trace" precedent of one representative effort per week rather than plotting every run. Each
  week's winner is colored by whether it fell at least a calendar year before `today` (`yearAgo` —
  a real calendar-year anniversary, not a fixed 365-day window, with Feb 29 clamped back to Feb 28
  on a non-leap anniversary year rather than overflowing into March) — red for "one year or
  older," blue for "less than one year."
- **Two exact-distance pace frontiers**, one per color (`recordFrontier`): sort a set of weekly
  winners by distance descending, keep a point only when its pace beats every point already kept
  at an equal-or-longer distance — "no equally long or longer run is as fast," the same personal-
  record concept `runningStats.ts::personalRecords` already establishes for a handful of named
  race distances, generalized here into a continuous step frontier across every observed distance.
  An exact pace tie prefers the *older* point (never lets an unchanged record masquerade as the
  frontier's own newest point).
- **Where recent actually beats old** (`improvements`) is the chart's real point, not the two
  frontiers alone: walking every distance boundary either frontier has a point at, an interval is
  drawn as an extra blue segment directly over the red step only where a *non-older* point on the
  *combined* (both years pooled) frontier is strictly faster than the older-only frontier at that
  same distance — genuinely adjacent improving intervals merge into one continuous polyline
  (rather than two separate `<Line>` elements, which could each carry a different `best.pace` and
  so still trace a real step within that one merged stretch) specifically so a real
  *non*-improving gap between two improving stretches never gets bridged by a stray connecting
  line — confirmed by its own test, "keeps disconnected improvements separate across a
  non-improving interval." A tie is deliberately never colored as an
  improvement (a small float-equality epsilon guards the comparison, same "don't overclaim a tie as
  progress" instinct as `recordFrontier`'s own tie-break above). The blue line only ever
  extrapolates back to distance 0 or forward to the next real boundary already on one of the two
  frontiers — never beyond the longest distance the older frontier actually covers, since there is
  no older baseline yet to compare a longer recent run against.
- Each plotted dot is a real, keyboard-focusable `<a href="/activities/{id}">` (not hover-only),
  surfacing that run's own name/date/distance/pace/VDOT/duration in an `aria-live` details strip
  and linking straight to the activity.

## Weather: full conditions judgement from one endpoint (weather.py)

`GET /activities/{id}/weather` originally carried just enough for a header badge (temperature/
humidity range, a representative weather code/feels-like/wind at the activity's own start). It
was extended so the endpoint alone supports a full conditions *judgement* — heat stress in bpm/
pace terms, not just numbers — for a consumer (an AI coaching agent reading this endpoint daily)
that previously had to make its own second call to Open-Meteo for the data this project wasn't
storing. See `weather.py`'s own module docstring for the complete reasoning; this section is the
metric-key/API-shape reference.

- **New `activity_metric` keys** (`weather.open_meteo.*`, `source="open-meteo"`, all in
  `weather.py::_OPTIONAL_METRIC_KEYS` — never `_ALL_METRIC_KEYS`, the five-key cache-hit
  requirement `_read_cached` checks; see that function's own docstring for why adding a key there
  would be the bug that makes every already-cached activity re-fetch from Open-Meteo forever):
  `dew_point_min_c`/`dew_point_max_c`, `solar_radiation_max_wm2`/`solar_radiation_mean_wm2`,
  `cloud_cover_min_pct`/`cloud_cover_max_pct`, `apparent_temperature_min_c`/
  `apparent_temperature_max_c` — all window aggregates over every hourly bucket overlapping the
  activity's own time window, the same convention `temperature_min_c`/`max_c` already established
  — and `sunrise_utc`/`sunset_utc`, the daily entry matching the activity's own start date, stored
  as `value_text` (ISO string) rather than `value_num` since `activity_metric.value_num` is a
  Float column with no datetime concept of its own (same `value_type="text"` precedent
  `geocoding.py`'s own location-name metric already established). Every field is independently
  `None`/absent (never fabricated) whenever Open-Meteo's response lacks that array entirely — most
  notably on every activity whose weather was cached before these fields were ever requested,
  until `sync backfill-weather-fields` re-fetches it (see below).
- **Why heat stress needs these specifically**: relative humidity alone doesn't say how much
  moisture the air can actually hold — dew point does, and it's the number that predicts real
  physiological heat strain. Shortwave radiation (W/m²) captures direct-sun load a min/max on air
  temperature can't: >800 W/m² sustained is severe, and two runs at the same air temperature can
  be wildly different efforts depending on cloud cover. Apparent temperature sitting notably below
  air temperature signals dry air or wind doing real evaporative-cooling work — invisible if only
  a single start-of-run value is ever shown, which is why it now also gets a full window range
  (`apparent_temperature_min_c`/`max_c`) alongside the pre-existing single `feels_like_c`
  representative value (unchanged, still the hour closest to the activity's own start — see
  `weather.py`'s own docstring for why that field stays a single value like wind, not a range).
- **The `hourly[]` trajectory** (`ActivityWeatherOut.hourly`, `weather.py::
  parse_open_meteo_hourly_series`) is the field that actually replaces a consumer's own second
  Open-Meteo call: one entry per hourly bucket overlapping the activity's window, each carrying
  the UTC timestamp plus air temp, apparent temp, dew point, relative humidity, shortwave
  radiation, cloud cover, wind speed, and wind direction — enough to render a full run-window
  conditions table directly from this one response. Deliberately **not** stored in
  `activity_metric` at all: that table is scalar-only (`value_num`/`value_text`, one row per
  metric key), and inventing per-hour synthetic metric keys would pollute `metric_definition` with
  hundreds of junk rows for zero benefit over just re-reading the archive. Instead it's re-derived
  at request time from the same raw Open-Meteo response already archived verbatim on first fetch
  (`weather.py::read_archived_open_meteo_response`, a plain gzip-decompress + JSON parse of bytes
  already on disk — no network call, exactly what "raw first, always" exists to enable). This is
  also what makes an already-cached activity behave correctly with zero special-casing: its
  archived response genuinely doesn't have the newer arrays, so every hourly point simply has
  those fields `None` — re-parsing an old payload can't manufacture data that was never fetched.
- **`sunset_during_run`** (`ActivityWeatherOut` only — not a stored metric): whether
  `sunset_utc` falls inside `[start, end]` of the activity, computed once at the API layer from
  already-available data rather than persisted as a synthetic activity_metric row. `None` when
  `sunset_utc` itself is `None` (nothing to judge against), never a guessed `True`/`False`.
- **Request shape**: `hourly=` now also requests `dew_point_2m,shortwave_radiation,cloud_cover`
  (alongside the original `temperature_2m,relative_humidity_2m,weathercode,
  apparent_temperature,wind_speed_10m,wind_direction_10m`), and a new `daily=sunrise,sunset` param
  is added — both still under the same `timezone=UTC`/`wind_speed_unit=ms` request-level params
  the original fetch already used, so every timestamp in the response (hourly and daily alike)
  stays UTC-aligned with no per-activity-timezone handling needed.
- **Backfill** (`weather_backfill.py::backfill_weather_fields`, CLI: `sync
  backfill-weather-fields [--dry-run] [--athlete-id ...]`): an already-cached activity's archived
  response genuinely doesn't have the newer Open-Meteo variables on disk — they can't be
  re-derived, only re-fetched (`get_or_fetch_activity_weather(force_refresh=True)`). Idempotent
  and cheap to re-run: an activity's own archived response is read back first (free, no network
  call) and checked for whether its `hourly` block already has a `dew_point_2m` key at all — a
  structural marker for "this was fetched under the newer request," independent of whether
  Open-Meteo actually had a non-null reading for every hour (the same reason `_read_cached`
  doesn't require the newer fields to be non-`None`: a real historical date can legitimately lack
  that data on Open-Meteo's own side). An activity that already carries this marker is skipped
  with zero further work, so a second run over an already-backfilled athlete costs one archive
  read per activity and no network calls at all. Run once after upgrading past this change; every
  activity ingested from then on is fetched with the full field set from the start.
- **`precipitation_mm`** (added later still, same `_OPTIONAL_METRIC_KEYS`/backfill treatment as
  above): a window **SUM**, not a min/max range like every other field in this section — "how
  much rain fell during the run" is a total, the same way a runner would describe it, not a
  range. `0.0` is a real, meaningful reading (no rain) and stays distinct from `None`
  (Open-Meteo's `precipitation` array is absent entirely, or every overlapping hour's reading is
  null) — summing an empty list would silently collapse those two very different cases into the
  same `0.0`, so the window list is checked for emptiness before summing, the same guard the
  solar-radiation mean already uses for the identical reason. Request shape: `hourly=` gained
  `precipitation` alongside the existing fields; `hourly[]` gained a matching
  `precipitation_mm` per bucket. `weather_backfill.py::_NEW_FIELD_MARKER` moved from
  `dew_point_2m` to `precipitation` when this field shipped — a response carrying `precipitation`
  was necessarily fetched under a request that already included `dew_point_2m` too, since both
  land in the same joint `hourly=` param list — so re-running `sync backfill-weather-fields` after
  this change does one more real pass over every activity, even ones an earlier pass already
  backfilled under the prior marker, rather than a no-op.

## Weather forecast for the Week view (weather_forecast.py)

`GET /weather/forecast` is the future-facing counterpart to the section above -- and a
deliberately different shape from it. See `weather_forecast.py`'s own module docstring for the
full reasoning; this section is the schema/API-shape reference.

- **`athlete.home_lat`/`home_lon`** (both `Float`, nullable, added alongside `athlete.email`):
  the one location this feature (and this schema) has for "where does this athlete live" --
  nothing else in this project has a concept of a default/current location, since every other
  weather feature (`weather.py`) is keyed to one specific activity's own GPS start point.
  Settable via `GET/PUT /settings/profile` (`AthleteProfileIn.home_lat`/`home_lon`, validated to
  -90..90/-180..180 and required to be set or cleared together) -- manual entry, or the Settings
  page's own "Use current location" button (`navigator.geolocation.getCurrentPosition`, browser-
  side only, never sent anywhere but into these two fields).
- **No archiving, no caching** -- the one deliberate exception to this project's own raw-first
  rule for a live vendor fetch. Every other Open-Meteo/vendor call in this codebase archives the
  raw response and caches the derived value forever (`weather.py`'s own `activity_metric` cache),
  because AGENTS.md's raw-first rule exists so a permanent record can be re-derived without
  recontacting a vendor. A forecast has no permanent-record concept: it's superseded by reality
  as the date approaches, so archiving it would only accumulate useless bytes with zero
  re-derivation benefit. This mirrors the "request-time exception to the rollup mandate"
  `vo2max_analysis.py`/`pace_hr_zones.py` already establish for a bounded, occasional live
  lookup, not a new precedent.
- **Open-Meteo's *forecast* API** (`api.open-meteo.com/v1/forecast`), a distinct endpoint from
  the historical archive API `weather.py` calls. `forecast_days` is hard-capped to 0-16,
  confirmed live (`weather_forecast.MAX_FORECAST_DAYS = 16`) -- requesting more raises an error
  response rather than silently truncating. Request: `daily=weathercode,temperature_2m_max,
  temperature_2m_min`, returning parallel `time`/`weathercode`/
  `temperature_2m_max`/`temperature_2m_min` arrays, the same field-naming convention
  `weather.py`'s own historical request already uses. A day missing any of the three fields is
  skipped (never fabricated), though Open-Meteo reliably fills every requested day in practice.
  `timezone` is the athlete's own `athlete.timezone` (see below), never a hardcoded `UTC` --
  confirmed live that Open-Meteo's `daily` entries are dates in the *requested* timezone, so a
  UTC request for an athlete west of Greenwich returns "today" as already tomorrow locally for
  several hours a day, a full calendar-day misalignment against the Week view's own local-date
  grouping.
- **`athlete.timezone`** (existing column, `String`, `nullable=False`, default `"UTC"` -- not a
  new one): previously CLI-only (`sync athlete create`'s own default, read only by
  `calendar_feed.py`'s VTIMEZONE for the iCal feed's timed events), now also exposed via
  `GET/PUT /settings/profile` (validated as a real IANA name via `zoneinfo.ZoneInfo`, raising on
  anything else) and read by `weather_forecast.py` above. Settings page: a native `<select>`
  populated from `Intl.supportedValuesOf("timeZone")` at render time (no bundled/hand-maintained
  IANA list needed) plus a "Use my browser's timezone" button
  (`Intl.DateTimeFormat().resolvedOptions().timeZone`). Since this field can never be null on the
  athlete row, `PUT /settings/profile` omitting it resets it to `"UTC"` -- consistent with this
  endpoint's own pre-existing "full replacement, not a partial patch" contract for every other
  field.
- **`WeatherForecastOut`** (`api/schemas/weather_forecast.py`): `available: bool` (`false` --
  never a fabricated forecast -- when the athlete has no home location set or the fetch fails,
  matching `ActivityWeatherOut`/`ActivityLocationOut`'s own convention) plus `days:
  ForecastDayOut[]` (`local_date`, `weather_code`, `temperature_min_c`, `temperature_max_c`).
  `GET /weather/forecast?days=N` accepts 1-16, defaulting to the 16-day cap.
- **Frontend** (`WeekView.tsx`): `useWeatherForecast()` fetches the whole week's forecast once
  in the parent `WeekView` component -- not per-day, unlike `WeekDayPlannedWorkouts`/
  `WeekDayRaces`, which exist specifically to work around Rules-of-Hooks for genuinely per-date
  endpoints; a whole-week forecast is naturally one call regardless of how many of its returned
  days fall inside this particular week. Indexed by `local_date` and passed down to each
  `WeekDayColumn`; a day with a matching entry (today through however many days Open-Meteo
  actually returned) renders a `.week-columns__forecast` row -- a weather icon
  (`weatherCodeInfo()`, reused as-is from `weatherCode.ts`) plus min/max temperature -- on its
  own row directly under that day's date label (`week-columns__header`), deliberately not folded
  into that header's own flex-wrap row alongside the sleep chip. A past day, or one beyond
  Open-Meteo's forecast horizon, simply has no matching entry and renders nothing.

### Revision: near-term rich conditions detail (`upcoming`)

The same richer field set the Weather section above gathers for a past activity's own window,
gathered instead for the athlete's own next `weather_forecast.UPCOMING_DETAIL_DAYS` (3) days —
dew point, shortwave radiation, cloud cover, a full apparent-temperature range, precipitation,
sunrise/sunset, and an hour-by-hour trajectory — so a coaching agent reading `GET
/weather/forecast` can judge tomorrow's conditions in the same bpm/pace terms it already judges a
past run in, without a second Open-Meteo call.

- **A second, independent Open-Meteo request** (`weather_forecast.py::fetch_upcoming_conditions`),
  not an extension of the coarse `days` forecast above: `forecast_days` controls both the `daily`
  and `hourly` ranges together in one request, and the coarse forecast's own simple icon+
  temperature columns need up to 16 days while the rich hourly detail is only fetched for the
  near-term handful of days it stays meaningfully accurate for — combining the two would mean
  fetching 16 days of mostly-unused hourly data just to serve 3 days' worth to any consumer that
  wants it, or capping the coarse forecast at 3 days and breaking the Week view's own week-long
  display. The two fetches can succeed or fail independently: `WeatherForecastOut.upcoming` is
  `[]` (never fabricated) whenever this second request fails or returns nothing usable, regardless
  of whether `available`/`days` above succeeded, and vice versa.
- **Request shape**: `daily=weathercode,temperature_2m_max,temperature_2m_min,sunrise,sunset` and
  `hourly=temperature_2m,relative_humidity_2m,apparent_temperature,dew_point_2m,
  shortwave_radiation,cloud_cover,precipitation,wind_speed_10m,wind_direction_10m` (the same
  hourly field set `weather.py`'s own historical request uses), `wind_speed_unit=ms` for the same
  SI-units reason, `timezone` the athlete's own IANA zone (same as the coarse forecast).
- **Every datetime field is local, not UTC** — `ForecastDayDetail.local_date`, `sunrise_local`/
  `sunset_local`, and each `ForecastHourlyPoint.time_local` are naive values in the athlete's own
  local time (confirmed live, same as the coarse forecast's own `daily` dates), never UTC; the
  explicit `_local` suffix (vs. `weather.py`'s own `_utc` fields) says so rather than leaving the
  distinction implicit. `parse_upcoming_conditions_response` groups every hourly bucket by the
  calendar date its own local timestamp falls on (a cheap string-prefix match against `daily.
  time`'s identical `"YYYY-MM-DD"` spelling), then reduces each day's own group to the same
  min/max/sum aggregates `weather.py::parse_open_meteo_response` uses for an activity's window.
- **No scalar feels_like_c/wind_speed_mps/wind_direction_deg** on `ForecastDayDetail`, unlike
  `ActivityWeatherOut` — a whole day has no single "activity start" hour to anchor one
  representative reading against the way a run's own start time does, so rather than fabricate an
  arbitrary representative hour, those three fields simply don't exist at the day level; `hourly`
  carries per-hour wind/apparent-temperature instead, letting a consumer pick whichever hour
  matches their own planned time.
- **`WeatherForecastOut.upcoming: list[ForecastDayDetailOut]`** (`api/schemas/weather_forecast.
  py`): `local_date`, `weather_code`, `temperature_min_c`/`max_c`, `humidity_min_pct`/`max_pct`,
  `dew_point_min_c`/`max_c`, `solar_radiation_max_wm2`/`mean_wm2`, `cloud_cover_min_pct`/`max_pct`,
  `apparent_temperature_min_c`/`max_c`, `precipitation_mm` (a day-total sum, `0.0` a real reading
  distinct from `None`, same convention as the activity-weather section above), `sunrise_local`/
  `sunset_local`, and `hourly: list[ForecastHourlyPointOut]` (`time_local`, `temperature_c`,
  `apparent_temperature_c`, `dew_point_c`, `relative_humidity_pct`, `shortwave_radiation_wm2`,
  `cloud_cover_pct`, `wind_speed_mps`, `wind_direction_deg`, `precipitation_mm`).
- **Backend-only, matching `ActivityWeatherOut.hourly[]`'s own precedent**: no Week view UI change
  ships alongside this. `hourly[]` on a past activity's own weather already has zero frontend
  rendering in this codebase (verified: `ActivityWeather.tsx` never reads it) — it exists purely
  as an API/MCP-consumer field for exactly this kind of "judge conditions without a second vendor
  call" use case, and `upcoming` follows the identical posture.

## Personalize settings: week start day, time format, starting page, distance units

`GET/PUT /settings/personalize` (`api/schemas/settings.py::PersonalizeSettingsIn`/`Out`) — four
pure display preferences, a deliberate second endpoint from `/settings/profile` rather than
folded into it (same `athlete` table, different concern, mirroring this app's own
Profile-vs-Password split). Unlike Profile's `birthdate`/`height_cm`/`home_lat`/`home_lon` (real
inputs to formula fallbacks elsewhere), none of these four are ever read by any backend
computation — they only change how the frontend renders already-computed data.

- **`athlete.week_start_day`** (`String`, `nullable=False`, default `"monday"`), **`time_format`**
  (`String`, `nullable=False`, default `"24h"`) — new columns, both added by migration
  `9861c92c896b` with a `server_default` (not just the Python-side `Column(default=...)` every
  other athlete-profile field relies on) so the ALTER TABLE backfills existing rows in place,
  since these are `NOT NULL` on a table that already has rows, unlike every prior additive column
  on `athlete` (all either nullable or added at the initial-schema migration with no existing
  rows to violate).
- **`athlete.default_view`** (`String`, `nullable=False`, default `"week"`) — same migration,
  same server_default treatment. One of `"week"|"month"|"day"|"activities"`.
- **`athlete.unit_preference`** (`String`, `nullable=False`, default `"metric"`) — **reused, not
  new**: this column already existed on `athlete` (set only at `sync athlete create` seed time),
  but was never read anywhere in the app before this feature — confirmed by a full-codebase grep
  before repurposing it as the real km/miles toggle, rather than adding a redundant column.
- **Validation**: closed `Literal` types on the Pydantic schema (`week_start_day:
  Literal["monday", "sunday"]`, etc.) — Pydantic itself rejects anything outside the set (422),
  unlike `AthleteProfileIn`'s own manual `_sane_values` validator, which exists because that
  endpoint's fields are open-ended strings/floats with no fixed enum to lean on.
- **Full replacement on PUT**, identical contract to `/settings/profile`: every field is always
  sent, and omitting one resets it to its own schema default rather than leaving the stored value
  untouched.

### The reported bug: scheduled-workout time showed AM/PM instead of 24h

A native `<input type="time">`'s stored *value* is always a 24h `"HH:MM"` string per the HTML
spec — the bug was never in what got saved — but its *displayed* picker widget follows the
browser/OS locale, and confirmed empirically that neither Firefox nor Safari honor the `lang`
attribute override for this control the way Chrome partially does. No reliable HTML-only fix
exists.

- **`TimeOfDayField.tsx`** (new component): hour/minute `<input type="number">` steppers (24h
  range 0-23, or 12h range 1-12 plus an AM/PM toggle button pair shown only in 12h mode) that
  always store/emit the same 24h `"HH:MM"` string — the exact same `value`/`onChange` contract
  the native control had, so it's a drop-in replacement. Fully custom-rendered, so it can't
  silently follow browser/OS locale the way the native element does.
- **Every native time input in the app was replaced** — 4 found by a full-codebase search, one
  more than the single reported instance: three in `ScheduleWorkoutForm.tsx` (one per sport-tier
  branch: placeholder/exercise/running) and one in `PlannedRaceForm.tsx`'s own race edit form.
- **Fixed outright, not just made configurable**: `time_format` defaults to `"24h"`, so the bug
  is gone for every athlete immediately, before anyone even visits the new Personalize section.

### Frontend infrastructure: `PersonalizeContext`, `formatDistance.ts`, `formatTime.ts`

- **`PersonalizeContext.tsx`** (new — this app's first React Context): `usePersonalize()` exposes
  the athlete's own four settings anywhere in the tree, avoiding prop-drilling through the many
  view components (calendar grids, activity cards, stat tiles, forms) that each need one or more
  of them. A `DEFAULT_PERSONALIZE_SETTINGS` constant is returned synchronously before the
  `GET /settings/personalize` query resolves, so no consumer needs its own loading-state branch
  just to read a display preference. Mounted once in `App.tsx`, inside `AuthGate` (needs an
  authenticated athlete) and wrapping the nav + route `<Switch>`.
- **`formatDistance.ts`/`formatTime.ts`** (new): the shared unit-aware formatters this app never
  had before — confirmed by a full-codebase search that every screen did its own ad hoc
  `distance_m / 1000` + `.toFixed()` + a literal `"km"` suffix, and every clock-time display
  independently hand-rolled its own 12h-only AM/PM branch. `formatDistanceValue`/`formatPaceValue`/
  `kmhToDisplaySpeed`/`speedUnitLabel`/`paceMinPerDisplayUnit` (the last splits a pace into a bare
  "M:SS" value + separate `"/km"`/`"/mi"` unit, for call sites using `StatTile`'s own value/unit
  prop split rather than one combined string) and `displayDistanceToMeters` (the inverse, for an
  entry form's own round-trip — `GoalForm.tsx`'s goal-distance field and `PlannedRaceForm.tsx`'s
  custom-race-distance field both take input in the athlete's own display unit and convert to
  meters at submit, matching AGENTS.md's SI-in-storage rule). `formatClock`/`formatHHMM`/
  `formatTimeOfDay` for time. Each has a `useDistanceFormat()`/`useTimeFormat()` hook pre-bound to
  the athlete's own current setting via `usePersonalize()`, so a call site just does
  `formatDistance(activity.distance_m)`. Pace composes with the **existing**
  `runningStats.ts::formatMinPerKm` for its "M:SS" part rather than reimplementing carry-safe
  minute/second rounding a second time — that function and its own ~15 other callers needed no
  changes at all.

### Week start day is a frontend display preference only

Every backend weekly concept stays Monday-anchored regardless of this setting:
`rollups.py::week_start_monday` and the `period_rollup` table it populates, a week-`note`'s own
`entity_id` (literally that week's Monday `local_date`), `race_readiness.py`'s weekly-distance/
long-run windows, `email_reports.py`'s weekly report boundaries, and `sharing.py`'s recap/
share-image calendar-grid generation. Only the frontend's own calendar-grid rendering and
client-side weekly aggregates respect it:

- **`dateUtils.ts`**: `mondayOf` (unchanged, still the Monday-only primitive several
  backend-aligned call sites still need) gains a sibling `startOfWeek(date, weekStartDay)`;
  `weekRange`/`monthGridWeeks` gain an optional `weekStartDay: "monday"|"sunday" = "monday"`
  param — additive, not breaking, so every pre-existing call site keeps compiling and behaving
  identically unless explicitly updated to pass the setting. New `weekdayLabels(weekStartDay)`
  returns the rotated 7-label array, replacing `MonthView.tsx`'s previously hardcoded
  `WEEKDAY_LABELS` constant. `isoWeekNumber` is deliberately untouched — an ISO 8601 week number
  is a fixed international standard independent of display start-day (the same convention Google
  Calendar's own "start of week" setting follows — the week's own `W36`-style label doesn't
  change just because its visual first column does).
- **`runningStats.ts::weekdayIndex`** gains the same optional param: Sunday-start is `getUTCDay()`
  directly (already 0=Sun..6=Sat); Monday-start keeps the original `(day + 6) % 7`.
  `yearStats.ts::busiestWeekStart` and `RunningStats.tsx`'s own calendar-heatmap column placement
  (both a Month-view/All-time-view week-grid, and the year-rows all-time heatmap's own
  Monday-bucketed weekly totals) are generalized the same way.
- **`WeekView.tsx`'s own "Week stats" card was genuinely incompatible with a non-Monday
  display**, not just cosmetically wrong: it read its totals from `useCalendarWeeks`, a
  Monday-keyed `period_rollup` row — a row that simply doesn't exist for a Sunday-Saturday
  window, since the backend only ever computes Monday-anchored weekly rollups. Fixed by
  **`dateUtils.ts::sumDayRollups`**, which sums the same per-day `DayRollupOut` rows the view
  already fetches via `useCalendar`, for whichever 7-day range is actually showing — numerically
  identical to the old `period_rollup`-based total for the Monday default (same underlying daily
  data, just summed client-side instead of server-side), but correct for any week-start setting.
  This also removed `WeekView`'s own dependency on `useCalendarWeeks` entirely — one code path
  instead of two, not just a special case for the new setting. `activity_elevation_gain_m` in the
  summed result stays `null` (hiding its own stat tile, matching the pre-existing behavior) when
  not one day in the range actually recorded any elevation channel, rather than silently reading
  as a real `"0m"` — distinct from a real, summed flat week.
- **`MonthView.tsx`'s per-row "Week" column had the identical `period_rollup` mismatch** and gets
  the identical `sumDayRollups`-based fix — but additionally needed its own `useCalendar` fetch
  widened from just the calendar month's own start/end to the full padded grid range (the same
  range `usePlannedWorkoutsList`/`usePlannedRacesForRange` already used), since a first/last row
  can span into the adjacent month and a real week total must include those days too, not just
  the ones visible inside the current month.
- **`eddington.ts::computeYearlyEddington`/`computeEddingtonBars` reach a real, different number,
  not just a relabeling** — a deliberate exception to "week start day is display-only," since
  distance-unit conversion (not week-start) is what changes here: both functions take a `unit`
  param and convert each activity's `distance_m` to the display unit *before* running the
  Eddington algorithm, because an Eddington number is genuinely defined in terms of a real
  distance unit (VeloViewer and others offer the same km-vs-mile choice for cycling) — a
  mile-preferring athlete's Eddington number is a real, different integer computed over mile
  buckets, not the km-computed number with a different suffix.

### Deliberately left on km internally (flagged, not silently incomplete)

A few real per-sample chart pipelines and one backend-fixed bucket identity were left
unconverted in this pass — not overlooked, but a materially bigger job than a display-preference
sweep, since they'd mean touching chart axes/color scales or backend bucket semantics rather than
swapping a formatted string:

- **`RunningStats.tsx`'s own bar/scatter/heatmap chart data** (the Month/Year/All-time "Running"
  card's distance-bucket bar chart, trailing-window line chart, and calendar heatmap) — every
  bucket value is pre-computed in km by `runningStats.ts`'s own `distanceByDay`/`distanceByYear`/
  `monthlyDistanceM`/`rollingDistanceKm`, feeding Recharts `dataKey`s, legend thresholds, and a
  fixed heatmap color scale (`DAILY_HEATMAP_SCALE`) all keyed to km values.
- **`ActivityCharts.tsx`'s per-second Pace/Speed/GAP stream panels** — `runningStats.ts::
  streamSpeedValue` converts every raw `speed_mps` sample to min/km or km/h inline, per sample,
  feeding a live per-second chart's own y-axis domain and panel `unit`/`formatValue` fields.
- **`SplitsTable.tsx`'s per-km split table** — each row *is* a literal 1km segment
  (`splits.ts::computeKmSplits`), a real segmentation choice, not a formatted value; a genuine
  "mile splits" feature would need re-deriving splits at 1-mile intervals from the raw stream, a
  different computation, not a display conversion.
- **`ActivityFastestTable.tsx`'s own "Fastest N km runs" heading** stays km-labeled deliberately
  — `distanceKmLabel` must match the backend's own comparison-pool bucket exactly
  (`routers/activities.py::get_activity_context`'s `km_floor_m`, `floor(distance_m / 1000)`), so
  relabeling it to miles while the underlying pool stays km-bucketed would show a wrong, mixed-unit
  number. The per-row pace/speed *values* in that same table were still converted — only the
  heading's own bucket identity is out of scope.
- **`workoutSyntax.ts`/`workoutSteps.ts`/`splits.ts`** (the running workout text-syntax
  parser/preview and per-km splits computation) are untouched entirely — a parser for a
  km-denominated mini-language and a fixed-km segmentation, neither a display concern.
