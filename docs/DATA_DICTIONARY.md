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
  last full Garmin export" health signal `perseverer.staleness` nags on past 90 days.
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
  `training_load` (the deduplicated daily sum of per-activity load), and the derived
  `ctl`/`atl`/`tsb` from an independently-computed Coggan/Banister EWMA — see
  `fitness.py::refresh_fitness_rollup`. Garmin's own exports have no CTL/ATL/TSB triplet at
  all, so this is genuinely independent, not a mirror of a Garmin-provided value. Per-activity
  load prefers `perseverer.performance.running_tss` (see "Running TSS" below) when one exists,
  falling back to `fit.session.training_load_peak` otherwise — see that section for why.

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
  steps. One `planned_workout` per athlete per `local_date` (Garmin's own `schedule_workout()`
  is itself date-granular). Two sport tiers (`planned_workouts.py::PLACEHOLDER_SPORTS`):
  **running**'s `source_text` is the athlete's own typed workout-syntax text, kept verbatim and
  re-parsed into `planned_workout_step` rows on every save (`workout_syntax.py`), with
  `estimated_duration_s` derived from that parse; **yoga/bouldering**'s `source_text` (if any) is
  just freeform notes, never parsed — no `planned_workout_step` rows at all, and
  `estimated_duration_s` is set directly from the athlete's own `duration_minutes` input instead.
  `scheduled_time` ("HH:MM", nullable) is orthogonal to that split and stored either way — it's
  Perseverer's own calendar display metadata only, since Garmin's `schedule_workout()` has no
  time-of-day API at all. `push_status` (`draft`/`pushed`/`push_failed`) + `push_error` +
  `garmin_workout_id` + `garmin_scheduled_at` track the push lifecycle `activity_workout` (the
  retrospective, FIT-parsed workout-plan table from Phase 1, one-to-one with a completed
  `activity_id`) has no concept of. `planned_workout_step` mirrors `activity_workout_step`'s own
  unexpanded-repeat-block shape (a `repeat_until_steps_cmplt` row describing
  `[repeat_from_step..step_index-1] x repeat_count`, not pre-flattened) so `workoutSteps.ts`'s
  expand/group helpers work across both tables unmodified, but widens `target_type` to
  `"pace"`/`"heart_rate"` (an absolute range, or `target_hr_zone` resolved against the
  athlete's own `athlete_hr_zone_config` at push time) plus an independent `cadence_low`/
  `cadence_high` — a recorded step's `target_type` is speed-only. See
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
m/s — SI storage per CLAUDE.md principle 6) — a single whole-activity average grade-adjusted
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

Credentials (`PERSEVERER_EUFY_EMAIL`/`_PASSWORD`/`_DEVICE_ID`/`_CUSTOMER_ID`, all optional) are
deliberately **not** held to `garmin_connect.py`'s stricter token-store-only/never-auto-login model:
there's no evidence Eufy's API shares Garmin's SSO 429-lockout fragility, and the sibling project's
own plain-env-var pattern has run this exact login flow safely, daily, unattended, for months.
`sync_eufy()` just skips (logs, doesn't raise) when any credential is unset. `worker/main.py`'s
`run_daily_sync()` calls it in its own `try`/`except`, separate from the Garmin sync and staleness
check, so a Eufy-side failure (bad credentials, an API change) never blocks either of those.

`GET /health/dashboard`'s `LOGICAL_METRICS` promotes ten of the raw `eufy.scale.*` fields to
human-meaningful dashboard names (`weight_kg`, `bmi`, `body_fat_pct`, `muscle_mass_kg`,
`bone_mass_kg`, `water_pct`, `bmr_kcal`, `visceral_fat`, `metabolic_age`, `protein_ratio_pct`) —
single-alias entries, since Eufy is the only source for any of them, except `weight_kg`/`bmi`/
`body_fat_pct`, which each carry a second `apple_health.*` alias for the pre-Eufy era (see the
Apple Health export section below). The remaining ~14 raw
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
plain `BaseWorkout` (yoga gets a real Garmin sport type; bouldering has none and maps to
`SportType.OTHER`, a documented vendor limitation). This surfaced two real bugs, both fixed:
`GarminConnectAdapter.push_planned_workout` called the sport-specific `upload_running_workout`
(which rejects anything but a real `RunningWorkout`, confirmed by reading the check inside the
installed package) -- switched to the generic `upload_workout(workout.to_dict())`; and
`GET /planned-workouts/{date}` unconditionally re-parsed `source_text` for parse errors
regardless of sport, producing bogus errors on yoga/bouldering's freeform notes -- gated to
`sport == "running"` only. See `docs/adr/0015-scheduled-workouts.md` decision 9.
