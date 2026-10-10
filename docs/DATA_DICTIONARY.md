# Data Dictionary

What every stored field means, its unit, and where it comes from. For how the pieces fit together
see [ARCHITECTURE.md](ARCHITECTURE.md); for the HTTP shapes see [API.md](API.md).

Contents: [Conventions](#conventions) · [Tables](#tables) · [Metric registry](#metric-registry)
· [Strava export metrics](#strava-export-metrics) · [Cross-source merge](#cross-source-merge)
· [Activities and analytics](#activities-and-analytics) · [Health](#health)
· [Planning and goals](#planning-and-goals) · [Gear, weather, reports, settings](#gear-weather-reports-settings)

## Conventions

- **Units.** Physical quantities are stored in SI units: metres, seconds, m/s, kg, °C, W, bpm.
  Conversion to display units happens only in the presentation layer. Vendor values that are not
  SI are stored raw and converted on read; each such case is called out below.
- **Datetimes** are naive Python `datetime`s that are implicitly UTC. SQLite and SQLAlchemy do
  not round-trip `tzinfo`, so `DateTime(timezone=True)` is never used.
- **Local time.** `activity.utc_offset_s` is the local UTC offset in seconds. For FIT files it is
  `local_timestamp - timestamp` (`fit/parser.py::_derive_utc_offset_s`); an implausible result
  (`|offset| > 16 h`, seen on some indoor activities) falls back to 0. GPX/TCX timestamps are all
  UTC, so their offset and IANA `tz_name` come from the first GPS point
  (`timezone_lookup.py`, `timezonefinder` + `zoneinfo`, DST-correct for that date). FIT-sourced
  activities have no `tz_name` (FIT carries only the numeric offset).
- **`local_date`** on `activity` is offset-adjusted: `(start_time_utc + utc_offset_s).date()`.
  On `health_observation` and `sleep_session` it is the UTC date of the observation, so a
  `day_rollup` row can combine activity and health data whose day boundaries differ by the offset.
- **Positions** are decimal degrees (FIT semicircles × 180 / 2³¹ at parse time).
- **Provenance.** Every `metric_definition` row records `first_seen_at` and `first_seen_source`;
  every `activity_metric` and `health_observation` row records its `source`.
- **Metric keys** are namespaced by origin: `fit.<message>.<field>` (unnamed FIT fields by number,
  e.g. `fit.lap.27`), `garmin.daily_*.<field>` (Garmin Connect live API), `garmin.export.<report>.<field>`
  (Garmin export JSON), `strava.*`, `eufy.scale.*`, `apple_health.*`, `weather.open_meteo.*`,
  `perseverer.*` (computed by this app).

## Tables

### Raw archive

- **`raw_object`**: one row per archived blob, gzip-compressed at `storage_path`, content-addressed
  by `sha256`, unique per `(athlete_id, sha256)`. A JSON sidecar next to the blob
  (`<sha256>.json`) mirrors the row and is the durable catalog: `sync rebuild` restores this table
  from sidecars first. `source_locator` is a file path for file-based adapters or a resource
  reference for API adapters (e.g. `activity/<id>`). `kind` identifies the parser:
  - `fit`: any FIT file (activity or health, decided by `ingest_dispatch.py`); `fit_activity` is an
    older activity-only kind still replayed.
  - `daily_summary_json`, `hydration_json`: Garmin Connect-shaped health JSON found by
    `fit_folder`.
  - `garmin_connect_json` (activity summary; archived, not linked from `activity_source_link`) and
    one `garmin_connect_daily_*_json` kind per live wellness fetch.
  - `garmin_export_health_json` (wellness report JSON from an export, parsed);
    `garmin_export_json`, `garmin_export_csv`, `garmin_export_other` (everything else in an export,
    archived but not parsed).
  - `strava_export_*`, `apple_health_export_xml`, `eufy_scale_reading_json`,
    `kaya_sessions_json`/`kaya_ascents_json`/`kaya_unsent_climbs_json`, `historical-weather`
    (Open-Meteo), `nominatim_reverse_json` (geocoding), and `athlete_upload` (files the athlete
    uploads, e.g. a planned workout's GPX, `planned_workout_gpx`).

### Activities

- **`athlete`**: one row per athlete. `username`/`password_hash` and `api_key_hash`/
  `api_key_created_at` are both optional (an athlete may use either or both credential types;
  created with the CLI, password changeable via `PUT /settings/password`). Profile fields
  (`birthdate` as an ISO date, `height_cm`, `sex` = `male`|`female`, `email`, `timezone`,
  `home_lat`/`home_lon`) are optional; birthdate, height and sex only feed formula fallbacks (max
  HR, BMR) until real data exists. Display preferences: `week_start_day`, `time_format`,
  `default_view`, `unit_preference`. `last_full_export_at` is set by a successful `garmin_export`
  and drives the export-freshness alert. `calendar_feed_token_hash`/`calendar_feed_created_at`
  back the calendar feed.
- **`device`**: one row per distinct `(manufacturer, product, serial_number)` from a FIT
  `file_id`; `product` prefers the SDK's friendly name (e.g. `fr955`).
- **`activity`**: one row per real-world activity after merging sources. `id` is a ULID (sortable,
  no row-count leak), regenerated on every rebuild. `primary_source` is the adapter whose data
  fills the core fields. Core fields: `sport`/`sub_sport`, `start_time_utc`, `utc_offset_s`,
  `tz_name`, `local_date`, `duration_s`, `moving_duration_s`, `distance_m`, `elevation_gain_m`,
  `max_altitude_m`, `calories`, `is_race`, `name`, `shoe_id`, `carbohydrates_g`/`sodium_mg`
  (athlete-logged fueling; no vendor source), `deleted_at`.
- **`activity_sport_override`**: the athlete's corrections, keyed by `(athlete_id,
  start_time_utc)` so they survive a rebuild: `sport`/`sub_sport`, `is_race`, `name`,
  `carbohydrates_g`/`sodium_mg`, each independent. `apply_sport_overrides` re-applies them after
  every rebuild. `sync backfill-garmin-activity-names` also writes `name` here, using Garmin
  Connect's cloud-side activity name, but only when the current name is still the sport's generic
  device default (`_GENERIC_DEFAULT_NAME_BY_SPORT`, plus `(training, yoga)`); a custom title is
  never replaced.
- **`activity_trim_override`**: an athlete-chosen start/end trim (e.g. a watch left running),
  keyed like the sport override; streams, totals and derived values honour it.
- **`activity_source_link`**: links an activity to every raw record that contributes to it.
  `external_id` is the adapter's idempotency key: device serial + start time for `fit_folder`
  (never the filename; sha256 as fallback); for `garmin_export`, the Garmin activity id embedded in
  the filename (`<activityId>_ACTIVITY.fit` or `<email>_<activityId>.fit`), else the `fit_folder`
  rule; for `garmin_connect`, the Connect `activityId`. `raw_object_id` points at the FIT file that
  was parsed.
- **`activity_metric`**: open-ended per-activity scalars with no dedicated column (`value_num` or
  `value_text`), e.g. `fit.session.total_grit`, `perseverer.performance.vdot`. A new device field
  appears here without a migration.
- **`activity_stream`**: one row per activity pointing at its Parquet file; `channels` lists which
  of `heart_rate`, `cadence`, `power`, `temperature`, `speed_mps`, `altitude_m`,
  `respiration_rate`, `distance_m`, `lat`, `lon` the file carries.
- **`lap`**, **`split`**: per-lap and per-split rows (FIT `lap_mesgs`, `split_mesgs`). Bouldering
  routes are `split` rows; see [Bouldering per-route data](#bouldering-per-route-data-reverse-engineered-undocumented-fit-fields)
  and [Kaya](#kaya-bouldering-logbook-kaya_session-kaya_ascent-adapterskaya_ingestpy).
- **`route_geom`**: `encoded_polyline`/`simplified_polyline`, start/end points and bounding box;
  falls back to the session's start/end/bbox fields for activities without per-record GPS.
- **`activity_workout`**/**`activity_workout_step`**: the structured workout a device executed
  (FIT `workout`/`workout_step`), unexpanded (a `repeat_until_steps_cmplt` row repeats
  `[repeat_from_step..step_index-1]` `repeat_count` times).
- **`bouldering_route_status_override`**, **`bouldering_manual_route`**: route status/grade
  corrections keyed by `(athlete_id, activity_start_time_utc, split_index)`, and routes added by
  hand (their own `manual_order`); re-applied after every rebuild.

### Health

- **`health_observation`**: one row per health fact, keyed `metric_key` + `observed_at_utc` +
  `source`; `aggregation` is `instant | interval | daily`. Key families:
  - FIT-sourced facts without a dedicated column: `hrv.*`, `sleep.<field>` (every
    `sleep_assessment_mesgs` score), `nap` (an `interval` row with `interval_start`/`interval_end`
    and the nap feedback in `value_text`).
  - `garmin.daily_summary.<key>`, `garmin.hydration.<key>`: scalar fields of Garmin Connect's
    daily summary and hydration responses (from a folder of JSON files or the live sync).
  - `garmin.export.<report_kind>.<field>`: scalar fields of an export's
    `DI-Connect-Wellness`/`Metrics`/`Aggregator` JSON, `report_kind` taken from the filename
    (`sleepData`, `UDSFile`, `HydrationLogFile`, `TrainingReadinessDTO`, `EnduranceScore` and about
    20 more), all through one generic parser.
  - Live wellness fetches: `garmin.daily_sleep.*` (`get_sleep_data`), `garmin.daily_hrv.*`
    (`get_hrv_data`), `garmin.daily_training_readiness.*` (`get_training_readiness`, newest
    intraday reading only), `garmin.daily_vo2max.*`, `garmin.daily_heat_altitude.*` and
    `garmin.daily_training_status.*` (all from one `get_training_status` call),
    `garmin.daily_race_predictions.*` (`get_race_predictions`, one range request per sync), and
    `garmin.daily_lactate_threshold.{speed,heart_rate,power}` (range request; one row per day
    Garmin recomputed it). Lactate-threshold `speed` is stored raw and is **not** m/s: multiply by
    10 for a real speed (done in `FitnessPage.tsx`).
  - Export and live responses use different field names for the same reading (e.g.
    `averageRespiration` vs `averageRespirationValue`); they are aliased at read time by
    `LOGICAL_METRICS` (`api/routers/health.py`), not reconciled at ingest.
  - `eufy.scale.*` and `apple_health.*`: see their sections.
  - Nested objects in vendor JSON are archived raw but not flattened; their keys are cataloged.
  - Export-only report kinds not fetched live: `healthStatusData`, `AbnormalHrEvents`.
- **`health_stream`**: Parquet-backed intraday series, one row per `(metric_key, year_month)`:
  `heart_rate` (from `monitoring_mesgs`, `timestamp_16`-reconstructed), `stress_level`,
  `respiration_rate`, `spo2`, `hrv`, and `garmin.daily_body_battery.level`. Body battery comes
  from `get_stress_data`'s `bodyBatteryValuesArray` (about every 3 minutes, columns located through
  `bodyBatteryValueDescriptorsDTOList`). The older `get_body_battery` raw kind is still replayed.
  A month's file is built up across many source files by `write_health_stream`'s merge by
  timestamp (timestamps normalized to naive UTC), so re-ingesting a day is idempotent. Served by
  `GET /health/stream`.
- **`sleep_session`**: one row per night per source. From FIT, start/end come from
  `sleep_level_mesgs` and `sleep_score` from `sleep_assessment_mesgs.overall_sleep_score`; from the
  live sync (`parse_daily_sleep_json`), from `dailySleepDTO.sleepStart/EndTimestampGMT` and
  `sleepScores.overall.value`. `total_sleep_s` is the FIT session span, or Garmin's
  `sleepTimeSeconds` for the live sync. When both sources cover a night, `GET /sleep` prefers
  `garmin_connect` (its total excludes wake periods).
- **`sleep_stage`**: `light | deep | rem | awake` intervals of a session: from
  `sleep_level_mesgs`, or from the live sync's `sleepLevels[].activityLevel` (0 deep, 1 light,
  2 rem, 3 awake).

Daily steps, distance and calories are taken from Garmin's own daily totals, never reconstructed
from `monitoring_mesgs`.

### Rollups

Derived caches, written only by the refresh functions and recomputed by `sync rebuild`.

- **`day_rollup`**: one row per `(athlete_id, local_date)`: activity count, duration, moving
  duration, distance, elevation gain, calories (summed), `sleep_total_s`/`sleep_score` (the longest
  session that night).
- **`health_metric_daily_rollup`**: one row per `(athlete_id, local_date, metric_key)`:
  `value_sum`, `value_avg`, `value_min`, `value_max`, `value_last` (latest `observed_at_utc`),
  `n_observations`. EAV-shaped like `health_observation`, so new metric keys need no change; the
  API picks the aggregate each metric needs.
- **`period_rollup`**, **`health_metric_period_rollup`**: the same shapes per week (Monday start)
  or month (`period_type`), rolled up from the daily tables (sums of sums, weighted averages).
- **`fitness_daily_rollup`**: per day, `training_load` (deduplicated daily sum of per-activity
  load: `perseverer.performance.running_tss` when present, else `fit.session.training_load_peak`)
  and the derived `ctl` (42-day), `atl` (7-day) and `tsb` (Coggan/Banister EWMA,
  `fitness.py::refresh_fitness_rollup`).
- **`performance_daily_rollup`**: see [Race predictions and thresholds](#race-predictions-thresholds-and-max-hr-performance_daily_rollup).

### Notes and insights

- **`note`**: free-text notes from the athlete or an agent. `entity_type` is `activity`, `day` or
  `week`; `entity_id` is an activity id, an ISO date, or the week's Monday. Shown on the day view
  and above the week view's day columns.
- **`insight`**: output of the rules engine (`insights/engine.py`), deleted and recomputed per
  athlete on each refresh. `kind` (`effort` | `streak` | `pb` | `window_best` | `load` | `health`; the per-activity endpoint
  also returns `climb_record` for bouldering) ×
  `window` (`30d`, `90d`, `180d`, `year`, `365d`, or `current`) × `subject_key` (e.g.
  `distance:run`, `start_latest:run`, `calories:run`) identify a row; `detail` is rule-specific
  JSON. Effort dimensions: distance, duration, pace, average and max heart rate (both directions),
  average and max cadence, elevation gained and lost, highest point, calories, earliest and latest
  start, hottest and coldest conditions, all per sport family.

### Registries (shared, not athlete-scoped)

- **`metric_definition`**: the metric catalog, keyed by `metric_key`. `category` is
  `activity | health | device | unknown`; `unknown` means seen but not yet documented
  (`is_promoted`).

### Operations

- **`ingest_run`**: one row per adapter run (CLI, scheduled job or Settings button) with `source`,
  `items_seen`, `items_new`, `errors` (JSON) and, for `garmin_connect`, the
  `watermark_from`/`watermark_to` window. Read by the staleness check and by
  `GET /settings/jobs/latest`; `rebuild` and `kaya` are sources too.
- **`merge_decision`**: one row per activity ingest attempt: matched or new, with
  `MergeDecision.reasons` and `inputs`. Shown by `GET /activities/{id}/sources`.
- **`auth_login_attempt`**: `username`, `attempted_at`, `success` per login attempt; five failures
  for a username within 15 minutes lock it out with the same 401 as a wrong password. Not
  athlete-scoped (an unknown username must still count); rows older than 24 h are pruned on insert.
  Password reset requests are recorded here too, under `password-reset:<athlete id>`, which limits
  them to five per account per 15 minutes; a successful reset deletes the account's failed logins.
- **`share_link`**: see [API.md](API.md#sharing); **`oauth_*`**: see
  [OAuth](#oauth-authorization-server-for-the-mcp-endpoint-oauth_client-oauth_authorization_code-oauth_token).

### Planned workouts

- **`planned_workout`**: an athlete-authored future workout, any number per day, each addressed by
  its own id. `sport` decides the shape:
  - `running`: `source_text` is the workout syntax, re-parsed into `planned_workout_step` rows on
    every save (`workout_syntax.py`); `estimated_duration_s` comes from the parse.
  - `yoga`, `bouldering`: `source_text` is free notes, no steps; `estimated_duration_s` comes from
    `duration_minutes`.
  - `hiit`, `strength_training`: structured steps naming Garmin exercises (no text syntax);
    `estimated_duration_s` is computed from the steps.

  `scheduled_time` (`HH:MM`, optional) orders same-day workouts and is display-only (Garmin's
  schedule API is date-only). `comment` is a note for the whole workout, pushed as Garmin's
  `description`. Push lifecycle: `push_status` (`draft` | `pushed` | `push_failed`), `push_error`,
  `garmin_workout_id`, `garmin_scheduled_at`. `completed_at` is set only by the athlete; a matching
  recorded activity is reported at read time as `matched_activity_id`, never stored. Route columns
  (`route_*`) and course columns (`garmin_course_*`): see
  [Planned-workout GPX route](#planned-workout-gpx-route).
- **`planned_workout_step`**: unexpanded steps in the same shape as `activity_workout_step` (a
  `repeat_until_steps_cmplt` row repeats a block), with `duration_type` (`time` | `distance` |
  `lap_button` | `reps`, or `repeat_until_steps_cmplt` for a repeat marker), `target_type` (`pace` | `heart_rate`, an absolute range or
  `target_hr_zone` resolved against `athlete_hr_zone_config` at push time), an independent
  `cadence_low`/`cadence_high`, and a per-step `comment`. HIIT/strength columns: `duration_reps`,
  `exercise_category`/`exercise_name` (`""`, not null, when only the category is named),
  `weight_kg` (converted to grams only when pushing).

## Metric registry

`metric_definition` is filled automatically by `perseverer.metrics.registry.get_or_register_metric`,
called by every parser (activity FIT, health FIT and JSON, export JSON, Eufy, Apple Health) for
every field it encounters, mapped or not. A multi-year Garmin export catalogs well over a thousand
distinct keys. Browse it with `select * from metric_definition`.

## Strava export metrics

`strava_export` (`adapters/strava_export.py`) materializes a few `activity_metric` keys from
`activities.csv` beyond the core `activity` fields: `strava.relative_effort`,
`strava.perceived_exertion`, `strava.training_load`, and, for GPX/TCX-sourced activities (which
have no FIT session message), `strava.session.avg_heart_rate`, `strava.session.max_heart_rate` and
`strava.session.total_descent`. Readers coalesce `fit.session.*` and `strava.session.*`, FIT
first. A GPX `creator` is registered as `gpx.creator`. GPX/TCX streams carry `speed_mps`
(haversine-derived for GPX, consecutive deltas for TCX).

Every other CSV column is preserved in the raw `strava_export_csv_row` object (positional
`[header, value]` pairs, so duplicate column names survive). `raw_object.kind` values:
`strava_export_gz` (compressed vendor bytes, archived before decompression), `strava_export_gpx`,
`strava_export_tcx`, `strava_export_csv_row`, `strava_export_manual_entry` (rows with no file),
`strava_export_other` (archived, not parsed). `sync rebuild` replays all of them.

## Cross-source merge

**`activity_merge_override`** (`activity_merge.py`) records a merge the athlete made by hand, for
duplicates the automatic matcher missed (two platforms disagreeing on duration, or a sport
correction made after both sources were imported). The athlete picks, per field, which side
survives: `MERGEABLE_SCALAR_FIELDS` (distance, duration, elevation...), `MERGEABLE_METRIC_FIELDS`
(average/max heart rate, training load: the whole `activity_metric` row is copied, keeping its
`source`) and `MERGEABLE_COLLECTION_FIELDS` (route, laps, splits, stream: whole collections only).

`GET /activities/possible-duplicates` (Settings) and the activity page's duplicate banner share
`find_duplicate_candidates`; `GET /activities/{id}/merge-preview/{other_id}` builds the per-field
comparison and `POST /activities/{id}/merge` applies it. The override is keyed by the
`(source, external_id)` pairs of both sides (not the activity id, which changes on rebuild, nor the
start time, which both sides share) and re-applied by `apply_activity_merge_overrides` after every
rebuild.

## Activities and analytics

Values computed by Perseverer are stored with `source="perseverer"` under `perseverer.*` keys, so
they are never confused with a vendor's own figures. Recomputations are full delete-and-reinsert
per athlete, run at every ingest entry point in dependency order (VDOT, pace bands, average GAP,
running TSS, then the rollups and insights).

### Running performance index (VDOT)

- **`perseverer.performance.vdot`**: Daniels–Gilbert VDOT for every `sport == "running"` activity,
  from `distance_m` and `moving_duration_s`, grade-adjusted when the stream has `distance_m` and
  `altitude_m` (`vdot.py::compute_gap_factor`, a distance-weighted Minetti energy-cost model, so a
  paused device can't skew it). No row for non-running activities, or when the duration is so
  short the %VO2max curve would exceed 100% (the model's own limit).
- Computed by `performance.py::refresh_vdot`; exposed as `vdot` on `GET /activities` and
  `GET /activities/{id}`. Period views show the *best* VDOT in the period, since an easy run reads
  low from intensity rather than fitness.

### Grade-adjusted pace and similar runs

- **`perseverer.performance.avg_gap_speed_mps`** (m/s): whole-activity average grade-adjusted
  speed for running activities with `altitude_m` and `distance_m` streams
  (`gap.py::compute_avg_gap_speed_mps`): each stream interval's flat-equivalent time
  (`dt_s × FLAT_COST / cost(grade)`) summed and divided into the real distance, i.e. a
  distance-weighted average. `sync backfill-avg-gap` recomputes it.
- **Per-lap GAP**: `LapOut.avg_gap_speed_mps` on `GET /activities/{id}`, computed per request by
  `gap.py::compute_lap_gap_speeds_mps` over the lap's slice of the stream (a lap ends at the next
  lap's start, or the stream's last sample).
- **`GET /activities/{id}/comparisons`** ("Similar runs from here"): the 10 most recent other
  same-sport activities within ±15% of this distance (`_COMPARISON_DISTANCE_BAND_FRACTION`) that
  start within 300 m of this one (`_COMPARISON_START_RADIUS_M`), with VDOT, average GAP, average HR
  (`AVG_HR_METRIC_KEYS`) and average cadence (`fit.session.avg_running_cadence` doubled to
  steps/min). Empty, with `matched_count: 0`, when the activity has no distance or no GPS start.
- **`GET /activities/{id}/context`**: percentile rank against same-sport activities within ±15%
  distance, and `fastest`, the 30 fastest of that pool.

### Running TSS

Garmin's `fit.session.training_load_peak` is an EPOC/HR-based number not calibrated to the
"100 = one hour at threshold" TSS convention, which inflates CTL/ATL relative to other tools. For
running, Perseverer computes a pace-based TSS instead:

- **`athlete_running_load_config`**: one row per athlete, `threshold_pace_sec_per_km` (null = not
  configured). Set with `GET`/`PUT /settings/running-load`; saving immediately re-runs running TSS,
  the fitness rollup and insights.
- **`perseverer.performance.running_tss`**: `duration_hours × IF² × 100` with
  `IF = avg_gap_speed_mps / threshold_speed_mps`, using `moving_duration_s`
  (`running_load.py::compute_running_tss`). No rows while no threshold pace is configured.
- `fitness_daily_rollup` uses `running_tss` per activity when present, else `training_load_peak`.

### Race predictions, thresholds and max HR (performance_daily_rollup)

`performance_daily_rollup` (`performance_rollup.py::refresh_performance_rollup`): one row per day
per athlete from the first VDOT reading to today, recomputed in full (the rolling windows depend on
"today", so it advances through rest days). Independent of Garmin's own race predictions and
lactate threshold, which are stored and shown alongside. Exposed by `GET /performance`.

| Column | Meaning | Basis |
|---|---|---|
| `rolling_vdot` | 42-day trailing **maximum** of `perseverer.performance.vdot` | A maximum, not an average, because an easy run's VDOT reads low from intensity. Under the Daniels–Gilbert model this is the VO2max estimate itself (ml/kg/min), shown as "VO2max". |
| `max_hr_bpm`, `max_hr_source` | 365-day trailing maximum of `{fit,strava}.session.max_heart_rate`, all sports (`empirical`); else Tanaka `208 − 0.7 × age` from `athlete.birthdate` (`formula_fallback`); else null | Real max-HR efforts are rare, so a long window; any sport counts. Age formulas carry ~10–15 bpm error ([PMC10146295](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10146295/)), so they only fill the gap for a new athlete. |
| `threshold_pace_s_per_km` | Lactate threshold (LT2/VT2) pace at 88% of vVO2max, from `rolling_vdot` | Daniels cites 86–92%; Fathi et al. 2025 (*Int J Exerc Sci* 18(5)) measured VT2 at 89.6±3.8% VO2max, Esteve-Lanao et al. 2026 (*Sports* 14(1):29, n = 1,411) at 83.8–87.4% VO2peak. Solved in closed form from `VO2(v) = −4.60 + 0.182258v + 0.000104v²` (`compute_threshold_pace_s_per_km`); VDOT 50 → 4:15/km. |
| `threshold_hr_bpm`, `threshold_hr_source` | Median HR of runs within ±5% of that day's threshold pace (by GAP) over 365 days, at least 3 runs (`empirical`); else 88% of `max_hr_bpm` (`fallback`) | LT HR is commonly 85–92% of max HR; the median resists outliers. |
| `aerobic_threshold_pace_s_per_km`, `aerobic_threshold_hr_bpm`, `aerobic_threshold_hr_source` | Aerobic threshold (LT1/VT1) at 73% of VO2max; HR as above with an 85.1%-of-max fallback | Fathi et al. measured VT1 at 73.2±4.1% VO2max; Esteve-Lanao et al. at 67.5–73.4% VO2peak and 85.1±4.6% HRpeak. Shared helper `compute_threshold_hr`. |
| `predicted_5k_s`, `predicted_10k_s`, `predicted_half_marathon_s`, `predicted_marathon_s` | Race time (s) at `rolling_vdot` | Bisection on duration until `compute_vdot(distance, T) = rolling_vdot` (monotonic in `T`), 1 s tolerance; `None` outside the search bounds rather than extrapolated. VDOT stays within ~1–2.5% across distances where Riegel's formula drifts. |

`refreshed_at` records when the row was computed.

**`GET /performance/vo2max-analysis`** (`vo2max_analysis.py`, computed per request): the run that
sets the current `rolling_vdot` (`driving_activity`), every other qualifying run in the window,
when the driving run ages out (`expires_on`), and plain-language gaps (`missing`). Served
efficiently by the `ix_activity_metric_athlete_key` index (`athlete_id`, `metric_key`).

**`GET /performance/pace-hr-zones`** (`pace_hr_zones.py`, computed per request): five zones
(recovery, basic endurance, aerobic threshold, lactate threshold, VO2max) with a pace range, an HR
range and the runs behind each range.

- Profile VDOT: the best VDOT of an activity marked as a race in the last 730 days
  (`RACE_WINDOW_DAYS`), else the best training run in 365 days (`TRAINING_RUN_WINDOW_DAYS`);
  `profile_vdot_source` is `race` or `training_run`. Profile max HR: the highest in 730 days
  (`MAX_HR_WINDOW_DAYS`), any sport.
- Boundaries: Zone 1/2 at 59% VO2max / 65% HRmax (the floor of Daniels' Easy range,
  `ZONE1_2_*`), Zone 2/3 at the aerobic threshold (73%, HR 85.1%), Zone 3/4 at the midpoint, Zone
  4/5 at the lactate threshold (88%, HR 93.5%).
- Each zone's HR range is the 25th–75th percentile of the average HR of real runs whose GAP falls
  in that pace band (365 days, at least `MIN_ZONE_HR_SAMPLES`), else a formula band around the
  fraction of max HR.

### Performance curve

`GET /performance/curve` (`performance_curve.py`, computed per request): for a metric (`pace`,
`gap` or `heart_rate`) and a date range, the best sustained value for each duration in
`DURATION_BUCKETS_S` (1, 5, 10, 15, 30, 60, 120, 180, 300, 600, 900, 1200, 1800, 2700, 3600, 5400,
7200 s), across every qualifying activity.

- `best_window_over_stream`: a vectorised two-pointer sliding window per bucket. Heart rate uses a
  mean over D samples (`[i, j)`); pace/GAP use distance over real elapsed time (`[i, j]`).
- A window is rejected if reaching the duration needed a span more than 10% longer
  (`_SPAN_TOLERANCE`) or contains a gap over 15 s (`_MAX_GAP_S`); never bridged or fabricated.
  A bucket longer than an activity is skipped.
- GAP uses `gap.py::compute_gap_adjusted_distances` cumulated into a GAP-equivalent distance.
- `pace`/`gap` are running-only; `heart_rate` takes a `sports` filter.
- One DuckDB query reads all qualifying Parquet files; activity trims are honoured.
- The response includes the athlete's threshold pace and HR from `performance_daily_rollup` as
  reference values (drawn as dashed lines, never blended into the curve).

### Eddington number (computed in the browser)

The largest E such that the athlete ran at least E runs of at least E km in a calendar year
(`eddington.ts`, exact `sport === "running"`, raw distance). Per year: the number, total runs,
`runsTowardNext` and `runsNeededForNext` (always ≥ 1). The current year also gets bars of how many
runs reached each whole km. With miles selected, the number is computed in miles.

### PR progress (computed in the browser)

`prProgress.ts`: one point per ISO week (Monday–Sunday), the week's highest-VDOT run, coloured by
whether it is at least a year old. `recordFrontier` keeps, per colour, the points faster than every
equal-or-longer point (ties prefer the older point); `improvements` marks the distances where a
recent point beats the older frontier. Each point links to its activity.

### Bouldering per-route data (reverse-engineered, undocumented FIT fields)

Bouldering activities (`sport="rock_climbing"`, `sub_sport="bouldering"`) record one route per
attempt as a pair of `split_mesgs` rows, a `climb_active` split followed by a `climb_rest` split,
using field numbers the FIT SDK profile does not name. Decoded against logged route sequences
(27 routes, no exceptions):

| Field | Meaning |
|---|---|
| `70` | V-grade + 1 (V0 = 1); stored de-offset as `split.climb_grade` |
| `71` | Result: `2` = attempt, `3` = completed; anything else stored as `unknown_<n>` in `split.climb_result` |
| `15`, `16` | Average / max heart rate of the split (`climb_avg_hr`, `climb_max_hr`), on active and rest splits |

Field `11` is a constant on every file and is left unmapped; other field numbers seen in public
lists don't appear in indoor bouldering files. `fit/parser.py::_climb_fields` is the only reader.

**Corrections** (`bouldering_overrides.py`): `PATCH /activities/{id}/climb-routes/{split_index}`
(status and/or grade, `bouldering_route_status_override`, keyed by `(athlete_id,
activity_start_time_utc, split_index)`), `POST /activities/{id}/climb-routes` (a route the watch
missed, `bouldering_manual_route` with its own `manual_order` and a synced live `split_index`),
`DELETE /activities/{id}/climb-routes/{split_index}` (manual routes only). Only the columns set on
an override are written, and everything is re-applied after every rebuild.

### Kaya bouldering logbook (kaya_session, kaya_ascent, adapters/kaya_ingest.py)

Route-level bouldering data from Kaya's private GraphQL API. Every page is archived raw
(`kaya_sessions_json`, `kaya_ascents_json`, `kaya_unsent_climbs_json`; source `kaya`), then parsed
into tables keyed by Kaya's own ids, which survive rebuilds (the rebuild re-upserts them from the
archive):

- **`kaya_session`**: `kaya_id`, `start_time_utc`/`end_time_utc` (unreliable: sessions are often
  logged after the fact, so never used for matching), `notes`, `gym_name`, `gym_city`,
  `raw_object_id`.
- **`kaya_ascent`**: `kaya_id`, `session_kaya_id`, `date_utc`, `ascent_type` (Flash, Onsight,
  Redpoint, Repeat), `grade_name` (e.g. `v3`), `climb_kaya_id`, `climb_name` (usually null),
  `climb_color`, `climb_wall`, `climb_type`, `is_lead`, `attempts` (includes the send), `rating`,
  `comment`, `raw_object_id`.
- **`kaya_attempt`**: climbs a session lists as attempted but not sent.
- **`kaya_unsent_climb`**: each unsent climb's lifetime attempt count (`attemptedClimbsForUser`).
- **`kaya_climb_note`**: the athlete's note on a route (`athlete_id`, `climb_kaya_id`, `note`,
  `updated_at`), set with `PUT /kaya-climbs/{id}/note` (empty removes it), never wiped by a rebuild.
- **`kaya_dismissed_effort`**: a watch effort the athlete removed from a Kaya-merged session's
  route list (`athlete_id`, `activity_start_time_utc`, `effort_start_time_utc` = the effort's own
  `split.start_time_utc`, `created_at`), set with `DELETE /activities/{id}/climb-routes/{n}` on a
  `garmin_extra` row. Both times come from the FIT file, so it survives a rebuild; never wiped.

`apply_kaya_sessions` derives `activity`, `split` and `activity_source_link` rows after every
import and rebuild (after the bouldering overrides). Only bouldering ascents are used; sessions
are grouped by local date:

- **Exactly one Garmin bouldering activity that day:** Kaya supplies the route list. Each send or
  failed try becomes a `climb_active` split with `source="kaya"`, `climb_grade` from `grade_name`,
  `climb_result`, `climb_name` and `climb_kaya_id`; a climb's attempts come before its send. A send
  with `attempts = N` adds N − 1 attempt rows; unsent climbs use their lifetime count (split across
  the sessions listing them, the remainder in the latest), or 1 when unknown. Garmin's own route
  rows become `climb_active_superseded`, with grade and result cleared (kept in
  `split.garmin_grade`/`garmin_result`), so nothing counts twice while their duration and HR stay.
  Garmin efforts Kaya has no entry for (matched by grade and result; a Kaya `v?` route absorbs one
  leftover) are promoted back to routes with `source="garmin_extra"`, except efforts listed in
  `kaya_dismissed_effort`, which stay superseded. Total time, calories, HR,
  training effect, the HR chart and climb time (`climb_active` + `climb_active_superseded`
  durations) stay Garmin's. Kaya is linked with `source="kaya"`, `external_id="session:<id>"`.
- **No or several Garmin candidates:** each Kaya session with routes becomes its own activity
  (`primary_source="kaya"`, named after the gym, no duration); several candidates are logged and not
  merged.

`split.climb_name` is Kaya's route name, else `"<colour> - <wall>"`. A `v?` grade is stored as a
null `climb_grade`, shown as "V?" and excluded from grade statistics. A `Repeat` counts as
completed. A Kaya route's note comes back as `SplitOut.note` on every row of that route, in every
activity. On a Kaya-covered day, manual corrections to the superseded Garmin rows no longer apply.

## Health

### Garmin Connect daily wellness

The daily sync calls, for every date in the rolling window (`PERSEVERER_GARMIN_ROLLING_WINDOW_DAYS`),
`get_stats`, `get_sleep_data`, `get_hrv_data`, `get_training_readiness`, `get_training_status`,
`get_hydration_data` and `get_stress_data`, plus range calls `get_race_predictions` and
`get_lactate_threshold`. Each response is archived under its own `garmin_connect_daily_*_json`
kind and parsed into the key families listed under [Health](#health) in Tables. The whole window
is re-fetched every run; upserts make unchanged days no-ops and a failed day heals on the next run.
A 429 aborts the run.

### Eufy body composition

`adapters/eufy.py::sync_eufy` logs in and fetches `last_device_data`, which always returns the
account's entire reading history (its `limit` parameter is ignored), so every run reprocesses all
readings; content addressing makes repeats free.

- Raw kind `eufy_scale_reading_json`, one per reading (external id = Eufy's record id).
- `health/eufy_parser.py::parse_eufy_scale_reading` flattens **every** scalar of `scale_data` into
  `eufy.scale.<field>` (`aggregation="instant"`), and other non-identifier record fields into
  `eufy.reading.<field>` (`product_code` as text).
- Units: `weight` is hectograms ÷ 10 → kg (cross-checked against muscle and bone ratios);
  `body_fat`, `muscle`, `bone`, `water`, `protein_ratio` are `%`; every other field (BMI, BMR,
  muscle/bone mass, visceral fat, body age, impedance...) is stored as reported with no unit.
- Credentials: **`athlete_eufy_config`** (`email`, `password`, `device_id`, `customer_id`, one row
  per athlete), set by `sync athlete set-eufy-credentials` or `POST /settings/eufy/login` (which
  verifies the login first). The default athlete falls back to `PERSEVERER_EUFY_*`. Missing
  credentials skip the sync.
- Dashboard (`LOGICAL_METRICS`): `weight_kg`, `bmi`, `body_fat_pct`, `muscle_mass_kg`,
  `bone_mass_kg`, `water_pct`, `bmr_kcal`, `visceral_fat`, `metabolic_age`, `protein_ratio_pct`
  (weight, BMI and body fat also alias the Apple Health keys).
- **BMR fallback:** with a day's weight but no Eufy BMR, and `birthdate`, `height_cm` and `sex`
  set, `bmr_kcal` is Mifflin–St Jeor (`10 × kg + 6.25 × cm − 5 × age + (5 male / −161 female)`,
  `bmr.py`), marked `source_metric_key="computed.mifflin_st_jeor"`, `n_observations=0`. No fallback
  for metabolic age.
- **Outlier filtering** (`api/routers/health.py::_body_composition_daily`, display only): a shared
  scale records other people's weigh-ins with no way to tell them apart. Body-composition metrics
  read straight from `health_observation` and walk the history in order, accepting a reading only
  within 15% of the last *accepted* value. A run of foreign readings is rejected as a block, even
  where it outnumbers the athlete's own.

### Apple Health export

`adapters/apple_health_export.py` imports an `export.xml` with no network access:

- **Scope:** blood pressure in full; body mass, BMI and body-fat percentage only before
  `weight_before` (default: the first `eufy.scale.weight`, `detect_weight_cutoff_from_eufy`), so it
  fills the pre-scale era without overlapping Eufy. Records from `sourceName == "eufy Life"` are
  skipped.
- **Keys** (`aggregation="instant"`): `apple_health.body_mass` (kg), `apple_health.body_mass_index`,
  `apple_health.body_fat_percentage` (%), `apple_health.blood_pressure_systolic` / `_diastolic`
  (mmHg, two observations sharing one timestamp).
- **Units:** body mass in lb is converted to kg; HealthKit stores body fat as a 0–1 fraction despite
  `unit="%"`, multiplied by 100.
- **Archive:** the whole file is one `raw_object` (`apple_health_export_xml`), stream-parsed with
  `ET.iterparse` (`health/apple_health_parser.py`). Other record types (clinical records, ECG,
  nutrition, mindfulness, watch vitals) stay unparsed in the archive and can be extracted later
  with a rebuild. The rebuild re-derives `weight_before` at replay time.
- **Dashboard:** `weight_kg`, `bmi`, `body_fat_pct` alias both sources; blood pressure has its own
  logical metrics (`blood_pressure_systolic`, `blood_pressure_diastolic`) and chart.

### Blood test results

Athlete-entered lab results, a plain table rather than the vendor EAV pipeline (nothing to archive,
no unknown fields).

- **`blood_test_result`**: `local_date` (draw date), `marker` (free text), `value_num`, `unit`,
  `reference_low`/`reference_high` (the athlete's own lab range, optional), `lab_name`, `notes`.
  Several rows on one date form one panel.
- Reference ranges are informational: the app flags a value outside the athlete's own stored range
  and never ships "normal" ranges of its own.
- Routes: `GET /blood-tests`, `POST /blood-tests` (one marker), `POST /blood-tests/batch` (a whole
  draw), `GET/PUT/DELETE /blood-tests/{id}`, `DELETE /blood-tests/by-date/{local_date}`.
- The Health page lists every distinct marker (grouped into Lipids, Glucose & thyroid, Electrolytes
  & kidney, Liver & pancreas, Blood count, Blood gas, Other) with its history and range; the add
  form suggests the athlete's own markers (pre-filling the latest unit and range) plus a short list
  of common marker names, never ranges.

## Planning and goals

### Scheduled workouts

Tables in [Planned workouts](#planned-workouts). Pushing (`GarminConnectAdapter.push_planned_workout`)
uploads a Garmin workout through the generic `upload_workout` and schedules it with
`schedule_workout()`:

- **Running** (`build_running_workout`): steps from the workout syntax, parsed by `workout_syntax.py`
  and its TypeScript twin `workoutSyntax.ts`, both tested against
  `tests/fixtures/workout_syntax_cases.json`. Syntax: `[intensity] duration [target] [cadence]`,
  durations in time, distance or `lap` (ends on the watch's lap button, `ConditionType.LAP_BUTTON`;
  an optional `lap 5km` estimate is never sent as an end condition), pace ranges, HR ranges or
  zones, `170-180spm` cadence, `Nx` repeat blocks ended by a blank line, and a trailing `# comment`.
- **Yoga, bouldering** (`build_placeholder_workout`): one untargeted step for `duration_minutes`;
  bouldering uses Garmin's `OTHER` sport type (the workout builder has no climbing type).
- **HIIT, strength** (`build_exercise_workout`): named exercises from the bundled catalog
  (`garminconnect.exercises`, 1,527 exercises in 47 categories), reps or time, weight in grams on
  the wire.
- A workout's `comment` becomes Garmin's `description`; step comments are never pushed.
- The worker pushes everything due within `PERSEVERER_PLANNED_WORKOUT_PUSH_WINDOW_DAYS` (7);
  `POST /planned-workouts/{id}/push` pushes now. `POST /planned-workouts/recurring` creates a
  series.
- **Done:** `completed_at` (set by the athlete) **or** `matched_activity_id` (computed per request
  by `planned_workouts.py::matching_activity_id`: a same-day activity whose `(sport, sub_sport)`
  fits the plan, running by `sport_family`, yoga/strength/HIIT also as `training` + sub-sport).
  The week view's compliance card counts done / scheduled per sport for days up to today.

### Planned-workout GPX route

A planned run can carry a GPX route (`POST /planned-workouts/{id}/route`, at most 5 MB, entity and
DOCTYPE declarations refused). The file is archived (`source="athlete_upload"`,
`kind="planned_workout_gpx"`, `external_id` = workout id) and referenced by
`planned_workout.route_raw_object_id`. Display summary columns, re-derivable from the archive:
`route_name` (the GPX name, else the file name), `route_distance_m` (haversine),
`route_elevation_gain_m` (sum of rises ≥ 1 m; null without elevation), `route_polyline` (encoded,
thinned to about 600 points), `route_uploaded_at`. Removing the route clears them; the file stays.

The route is pushed to Garmin as a **private course** with the workout (`garmin_course.py`):
`garmin_course_id`, `garmin_course_pushed_at`, `garmin_course_error` (a course failure never fails
the workout push). It is re-pushed only when the route changes, deleting the old course.
`GET /planned-workouts/{id}/route.gpx` returns the original file.

### Calendar feed

`calendar_feed.py` publishes the athlete's planned workouts and races as iCalendar (RFC 5545) at
`GET /share/calendar/{token}.ics` (public by token). `athlete.calendar_feed_token_hash` and
`calendar_feed_created_at` hold the one current token (SHA-256 of a `token_urlsafe(32)` secret,
shown once). `GET/POST/DELETE /settings/calendar-feed` show the status, publish or rotate, and
unpublish.

- One `VEVENT` per workout, `UID` `planned-workout-<id>@perseverer` (ids are stable: plans are not
  rebuilt). With `scheduled_time`, a timed event in `athlete.timezone` lasting
  `estimated_duration_s` (60 min if unknown), with a generated `VTIMEZONE`; without, an all-day
  event.
- `SUMMARY` is the sport label and the workout name. `DESCRIPTION` is the comment followed by the
  `source_text`, or, for HIIT/strength, one line per exercise (`name — reps or time @ weight`) with
  `Nx:` headers for repeated blocks and step comments appended.
- Races appear as their own events (`_build_race_event`), sized by the target time or 4 h.

### Races

- **`planned_race`**: `local_date`, `scheduled_time` (optional), `name`, `sport` (default
  `running`), `distance_m`, `target_duration_s` (optional goal). Any number per day, each with its
  own id. Routes: `GET /planned-races?start_date=&end_date=`, `GET /planned-races/by-date/{date}`,
  `POST /planned-races`, `GET/PUT/DELETE /planned-races/{id}`.
- `days_until` and `predicted_duration_s` are computed on every read: the prediction is the latest
  `performance_daily_rollup` value for a standard distance (5K, 10K, half, marathon, matched with a
  small tolerance); other distances get none.

### Race readiness

`GET /performance/race-readiness` (`race_readiness.py`, computed per request) asks whether the
athlete's volume matches their next running race (or `race_id`); `available: false` when there is
none.

- **Targets** by race distance from four anchors (5K, 10K, half, marathon), log-linear in between
  and clamped outside, set at the recreational/intermediate level of published plans (e.g. Higdon
  Intermediate marathon: about 55 km/week, a 29 km long run).
- **Compliance** is recency-weighted (`0.5 ^ (days_ago / half_life)`), each week capped at 100% and
  empty weeks counted as 0: weekly distance over 182 days with a 28-day half-life; the week's longest
  run over 70 days with a 14-day half-life.
- **Readiness** = 0.6 × weekly-distance compliance + 0.4 × long-run compliance.
- **Prognosis** = the VDOT race prediction, shown alongside, never blended.
- **History**: the same calculation re-run for each past week using only data available then.
  `weekly_distance_series` and `long_run_series` give the actual weekly figures behind it.

### Distance goals

**`goal`**: a target distance for a year, month or week (`period_type`; `period_start` is `YYYY`,
`YYYY-MM` or the ISO date a week starts on, any weekday), optionally for one sport (`null` = all
sports). One goal per `(athlete_id, period_type, period_start)` (`uq_goal_identity`); setting a
different sport replaces it. `PUT /goals` upserts; `POST /goals/repeat` repeats a weekly goal over
several weeks (replacing existing ones, at most 104 weeks); `GET /goals` returns
`{available: false}` when none is set.

Progress (`goals.py::compute_progress`, computed per request): `daily` cumulative distance (one
point per day, gap-filled), `target_distance_as_of_today_m = target / total_days × days_elapsed`,
`ahead_behind_m = current − target as of today`.

### Duration goals

**`duration_goal`**: a target time (`target_duration_s`) per week, month or year, for one sport or
all (`sport = null`). Several per period, one per sport (the API returns 409 on a duplicate; a null
sport can't carry a SQL unique constraint). Progress sums `COALESCE(moving_duration_s, duration_s)`
over the period's activities, with the same straight-line pace. Routes: `GET/POST /duration-goals`,
`POST /duration-goals/repeat` (skips weeks that already have that sport's goal),
`PUT/DELETE /duration-goals/{id}`.

### Bouldering goals

**`bouldering_goal`**: a target number of completed routes per week, month or year, at one
V-grade (`grade`, `and_harder` for that grade or harder) or any grade (`grade = null`). Several per
period; an identical goal returns 409. Progress counts `climb_result = "completed"` splits of
non-deleted bouldering activities in the period (attempts and `unknown_<n>` never count), so
corrections apply immediately. Routes: `GET/POST /bouldering-goals`, `POST /bouldering-goals/repeat`
(skips weeks holding an identical goal and reports them in `skipped_period_starts`),
`PUT/DELETE /bouldering-goals/{id}`.

## Gear, weather, reports, settings

### Gear

- **`shoe`**: `brand`, `model`, optional `size`, `comments`; `initial_distance_m` (distance before
  tracking began); `max_distance_m` (replacement limit, null = none); `alert_emailed_at`;
  `retired_at` (retiring keeps history).
- **`athlete_default_shoe`**: one row per `(athlete_id, sport)`: `shoe_id`, `assigned_at`.
- **Mileage** is computed on every read (`gear.py::shoe_mileages`): activities whose `shoe_id`
  names the pair, plus activities of the default's sport with no explicit shoe that started at or
  after `assigned_at`. An activity's shoe is its explicit `activity.shoe_id`
  (`PUT /gear/activities/{id}/shoe`; 422 for an activity without distance), else the sport default;
  `GET /gear/activities/{id}/shoe` returns the resolved shoe with `is_default`.
- **Alerts**: `GET /gear/alerts` lists active pairs at or over their limit (shown as a banner); the
  daily job emails each pair once (`alert_emailed_at`). `PUT /gear/shoes/{id}/retire` removes a pair
  from lists, defaults and alerts.

### Activity weather

`weather.py` fetches Open-Meteo's historical API once per activity (hourly UTC data, wind in m/s,
plus daily sunrise/sunset), archives the raw response (`historical-weather`) and caches scalars as
`activity_metric` rows `weather.open_meteo.*` (`source="open-meteo"`):

| Key | Meaning |
|---|---|
| `temperature_min_c`, `temperature_max_c`, `humidity_min_pct`, `humidity_max_pct` | Ranges over the hours overlapping the activity |
| `weather_code`, `feels_like_c`, `wind_speed_mps`, `wind_direction_deg` | The hour closest to the start |
| `dew_point_min_c`, `dew_point_max_c` | Dew point range (heat strain) |
| `apparent_temperature_min_c`, `apparent_temperature_max_c` | Feels-like range |
| `solar_radiation_max_wm2`, `solar_radiation_mean_wm2` | Shortwave radiation (above ~800 W/m² is severe sun load) |
| `cloud_cover_min_pct`, `cloud_cover_max_pct` | Cloud cover range |
| `precipitation_mm` | Total over the window (0.0 = dry; null = no data) |
| `sunrise_utc`, `sunset_utc` | Text (ISO), for the start date |

Temperature min/max, humidity min/max and `weather_code` are required for a cache hit
(`_ALL_METRIC_KEYS`); the rest are optional (`_OPTIONAL_METRIC_KEYS`) so older caches stay valid. `GET /activities/{id}/weather` adds
`sunset_during_run` and `hourly[]` (per-hour temperature, apparent temperature, dew point,
humidity, radiation, cloud cover, wind, precipitation), re-parsed from the archived response rather
than stored. `sync backfill-weather-fields` re-fetches activities whose archived response lacks the
newest variables (marker: `precipitation` in the hourly block). Location names come from cached
Nominatim reverse geocoding (`nominatim_reverse_json`).

### Weather forecast

`GET /weather/forecast` (`weather_forecast.py`) forecasts the athlete's home location
(`athlete.home_lat`/`home_lon`, set in Settings → Profile, both or neither) in their `timezone`
(an IANA name; Open-Meteo's daily dates follow the requested zone). Forecasts are neither archived
nor cached: they are superseded by reality, so there is nothing to re-derive.

- `days`: up to 16 (`MAX_FORECAST_DAYS`) days of `local_date`, `weather_code`, min/max
  temperature; `available: false` without a home location or when the fetch fails.
- `upcoming`: for the next 3 days (`UPCOMING_DETAIL_DAYS`), from a separate request, the same rich
  set as activity weather (humidity, dew point, radiation, cloud, apparent temperature,
  precipitation total, `sunrise_local`/`sunset_local`) and `hourly[]` in local time; `[]` if that
  request fails.
- `GET /weather/forecast/last-activity`: the same, at the start point of the latest activity with
  GPS, in that location's own time zone (`source_activity.timezone`); no fallback to home.

### Email reports

**`athlete_email_report_config`**: `weekly_enabled`, `monthly_enabled` (both default false). The
recipient is `athlete.email`; the SMTP relay is deployment-wide (`PERSEVERER_SMTP_*`, `security`
`starttls` on 587, `ssl` on 465, or `none`). Routes: `GET/PUT /settings/email-reports` (with
`smtp_configured`, `recipient_email`), `POST /settings/email-reports/test` (sends the current
weekly report; 400 if not configured, 502 if the send fails).

`email_reports.py` builds HTML (inline styles, pixel-width bars) plus plain text:

- **Weekly** (Sunday 18:00, after a Garmin re-sync): totals from `period_rollup` and per-sport split
  (grouped by display sport, so `training` + `yoga` reads "Yoga"); running distance per day with
  average pace and the week before for comparison; each run of the week and of the week before
  (distance, pace, duration, best of each highlighted); steps and sleep per day; races this week;
  every future race with its goal pace; notes for the coming week; the coming week day by day with
  forecast and planned workouts (running ones with estimated distance, duration and load).
- **Monthly** (last day 18:00): the month's totals and per-sport split.
- Sections with nothing to show are omitted; missing values are never shown as zero.

### OAuth authorization server for the MCP endpoint (oauth_client, oauth_authorization_code, oauth_token)

State for `auth/oauth.py`, kept in SQLite so both uvicorn workers share it:

- **`oauth_client`**: a dynamically registered client (`client_id`, RFC 7591 `client_info_json`,
  `created_at`); not athlete-scoped (it exists before anyone logs in).
- **`oauth_authorization_code`**: single-use, keyed by SHA-256 (`code_hash`), with athlete, client,
  `redirect_uri`, PKCE `code_challenge`, scopes, optional `resource`, a 5-minute `expires_at`;
  deleted on exchange.
- **`oauth_token`**: access (1 h) and refresh (30 days) tokens keyed by SHA-256 (`token_hash`),
  `kind`, `athlete_id`, `client_id`, scopes and `grant_id` (rotating or revoking either revokes
  both).

Raw codes and tokens are never stored. Expired rows are pruned when new ones are issued.

### Personalize settings

`GET/PUT /settings/personalize` (full replacement): display preferences only, never read by
backend computations.

| Column | Values | Default |
|---|---|---|
| `athlete.week_start_day` | `monday`, `sunday` | `monday` |
| `athlete.time_format` | `24h`, `12h` | `24h` |
| `athlete.default_view` | `week`, `month`, `day`, `activities`, `last_activity` (most recently imported activity) | `week` |
| `athlete.unit_preference` | `metric`, `imperial` | `metric` |

All backend weekly data (rollups, week notes, race readiness, email reports, share pages) stays
Monday-based; the week start only changes the frontend's calendar grids and client-side weekly
sums. With miles selected, distances and paces are converted for display and the Eddington number
is computed in miles. Per-km splits, per-second stream charts, running statistics charts and the
workout syntax stay in kilometres. The light/dark theme is a per-device browser setting, not stored
on the athlete.
