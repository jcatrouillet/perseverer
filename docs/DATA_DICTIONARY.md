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
  `activity_source_link`, see below), `garmin_export_json`/`garmin_export_csv`/
  `garmin_export_other` (everything in an export archive that isn't a `.fit` file — archived
  raw, not yet parsed; see `docs/adr/0003-phase-2-garmin-adapters.md` decision 4).

### Core

- **`athlete`** — single row today (multi-tenancy scaffolding for a possible future).
  `last_full_export_at` is set by `garmin_export` on successful completion — the "days since
  last full Garmin export" health signal `sporthealth.staleness` nags on past 90 days.
- **`device`** — one row per distinct `(manufacturer, product, serial_number)` seen in a FIT
  file's `file_id` message. `product` prefers the SDK's friendly name (e.g. `"fr955"`) over
  the raw numeric product code.
- **`activity`** — one row per real-world activity (post-merge; see `activity_source_link`).
  `id` is a ULID, not an autoincrement int, so it's sortable and safe to expose externally
  later without leaking row counts. `primary_source` records which adapter's data currently
  populates the core fields (field-level provenance/override precedence is a later-phase
  concern).
- **`activity_source_link`** — links one `activity` to every raw file/record that contributes
  to it. `external_id` is the adapter's idempotency key: for `fit_folder`, derived from the FIT
  file's device serial + start time (falling back to the file's sha256), deliberately never the
  filename, since real device folders don't use descriptive names; for `garmin_export`, the
  filename-embedded Garmin activity ID when present (real exports name files
  `<activityId>_ACTIVITY.fit`); for `garmin_connect`, the Connect API's own `activityId`
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
  Two `metric_key` naming families: `fit.<message>.<field>`-style keys for FIT-sourced facts
  that have no dedicated column (e.g. `hrv.status`, `sleep.deep_sleep_score` — every
  `sleep_assessment_mesgs` score maps generically to `sleep.<field>`, ADR 0004 decision 3), and
  `garmin.daily_summary.<key>` / `garmin.hydration.<key>` for scalar fields flattened from the
  Garmin Connect-shaped `daily_summary_*.json` / `hydration_*.json` files (nested dict/list
  values in those files are archived but not flattened — decision 5). `nap` is a single
  `aggregation="interval"` row per nap (`interval_start`/`interval_end`, `value_text` =
  feedback) rather than a dedicated table (decision 4).
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
both the activity FIT parser's and the health FIT/JSON parsers' ingest paths for every field
they encounter. As of the Phase 1 acceptance run (776 real activity FIT files), 824 distinct
metric keys were cataloged; after also running the Phase 2 health extension against 1808 real
monitoring FIT/JSON files (735 WELLNESS, 636 METRICS, 103 HRV_STATUS, 103 SLEEP_DATA, 17 NAP,
107 daily_summary, 107 hydration — zero errors, fully idempotent on re-run), 1156 distinct
metric keys are cataloged in total. Browse the live catalog via `select * from
metric_definition` — there is no separate promoted-metrics document yet (Phase 3's `/metrics`
endpoint and the frontend's metric registry browser, Phase 5+, are the intended long-term ways
to browse this).
