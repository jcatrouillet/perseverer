# ADR 0004: Phase 2 extension — health/wellness FIT + JSON ingestion

## Status

Accepted. Built and smoke-tested against 1808 real files (`WELLNESS`/`METRICS`/`HRV_STATUS`/
`SLEEP_DATA`/`NAP` FIT files plus `daily_summary_*.json`/`hydration_*.json`) from a real Garmin
device export. Formal test suite and a full `sync import fit-folder` acceptance run against that
directory are tracked separately (tasks 41-42).

## Context

ADR 0002 and ADR 0003 both left `health_observation`/`health_stream`/`sleep_session`/
`sleep_stage` as schema-only, blocked on real sample data. Mid-Phase-2, the user provided
exactly that: `C:\Users\Jerome\HealthData\FitFiles\Monitoring\2025`. Rather than defer this
again, the user chose to fold it into Phase 2 before committing.

Direct inspection of one real sample of each file kind (not guessed, not recalled) drove every
decision below.

## Decisions

### 1. `monitoring_mesgs` requires FIT `timestamp_16` reconstruction

305 of 445 rows in one real `WELLNESS` sample used FIT's compressed 16-bit rolling timestamp
instead of a full timestamp. Left unhandled, intraday heart-rate timestamps would be silently
wrong. `_resolve_compressed_timestamps()` reconstructs each `timestamp_16` against the last full
`timestamp` seen (seeded from `monitoring_info_mesgs.timestamp`, always full), handling 16-bit
rollover. `stress_level_mesgs`/`respiration_rate_mesgs`/`spo2_data_mesgs` were confirmed to carry
full timestamps in every row — no reconstruction needed there.

### 2. Daily totals (steps/distance/calories) are deliberately not reconstructed from `monitoring_mesgs`

Doing so needs per-activity-type `cycles_to_distance`/`cycles_to_calories` conversion factors
from `monitoring_info_mesgs` — genuinely complex — and is redundant: `daily_summary_*.json`
already carries Garmin's own server-computed daily totals directly. `monitoring_mesgs` is mined
for `heart_rate` only.

### 3. No first-class multi-score sleep table — `sleep_assessment_mesgs` maps generically to `sleep.<field>` observations

The existing schema has no dedicated table for the deep/light/rem/awake/quality/recovery/
restlessness score set `sleep_assessment_mesgs` provides. Rather than add one, each score becomes
a `health_observation` row keyed `sleep.<field>` — the same pattern already used for every other
unnamed/loosely-typed metric. `sleep_level_mesgs` (the stage transitions) is what populates the
purpose-built `sleep_session`/`sleep_stage` tables: consecutive stage-change rows are paired into
start/end intervals, and the session's own start/end/total-sleep are derived from them. Cross-
checked computed `total_sleep_s` against the independently-sourced `daily_summary` sleep duration
field on a real sample — matched.

### 4. `nap_event_mesgs` reuses the existing `interval` aggregation, no dedicated nap table

A nap is a single interval fact (start, end, feedback), which `health_observation`'s existing
`aggregation="interval"` column was already designed for. One row per nap, `metric_key="nap"`,
feedback text in `value_text`. Not worth a dedicated table for one field.

### 5. Nested JSON structure (event lists, the `rule` object, etc.) is archived raw, not decomposed

`daily_summary_*.json`/`hydration_*.json` top-level scalar fields become individual
`health_observation` rows (`garmin.daily_summary.<key>`/`garmin.hydration.<key>`). Nested
dict/list values are skipped as observations — raw-first means the bytes are archived regardless,
and the field's existence is still registered via `unrecognized_field_keys` so it stays visible
in `metric_definition`, not silently dropped. Promoting a specific nested structure to first-
class is a fast-follow if/when it's actually needed for a feature.

### 6. Every unnamed/unrecognized field is cataloged, never silently dropped

The METRICS files' unnamed message types (raw digit-string keys like `241`/`281`/`339`) and any
unnamed field inside a named message flow through `unrecognized_field_keys` into
`get_or_register_metric`, exactly like the activity FIT parser's existing pattern. This is the
same invariant ADR 0002 established for activity data, extended to health data.

### 7. Both FIT and JSON health files route through the existing `fit_folder` adapter, not a new one

The user's real export directory has activity and monitoring FIT files side by side, and
`fit_folder`'s charter is already "universal offline importer." A new adapter would duplicate
that discovery/polling logic for no benefit. `fit_folder.list_changed()` gained recognition of
two narrow, confirmed-real filename patterns (`daily_summary_\d{4}-\d{2}-\d{2}\.json`,
`hydration_\d{4}-\d{2}-\d{2}\.json`) — not a general JSON importer.

### 8. Unified per-FIT-file dispatch (`ingest_dispatch.ingest_fit_bytes`), not a filename/kind check

A `.fit` file's contents, not its filename, determine whether it's an activity or a health file
(`ACTIVITY.FIT` vs `WELLNESS.FIT` naming isn't guaranteed across every real export layout).
`ingest_fit_bytes()` archives once under a single generic `kind="fit"` (raw-first: archive before
you know what it is), tries `parse_fit`/`ingest_canonical_batch` (activity path), and falls back
to `parse_health_fit`/`ingest_health_batch` (health path) if the batch isn't an activity. Both
`fit_folder.import_from_folder` and `garmin_export.import_garmin_export` call this for every
`.fit` file, so a real Garmin export archive (which may nest monitoring FIT files anywhere) and
`fit_folder`'s directory polling are both handled identically. `garmin_connect` is deliberately
unchanged — it only ever downloads activity FIT files, so its direct `parse_fit` +
`ingest_canonical_batch` call stays as-is.

`fit_folder.py` and `ingest_dispatch.py` import from each other (dispatch needs
`IngestResult`/`ingest_canonical_batch`; `fit_folder` needs `ingest_fit_bytes`). Rather than
extract those into a third module, `fit_folder.import_from_folder` imports `ingest_dispatch`
lazily (inside the function body) to break the load-time cycle — a narrow, deliberate exception
to normal top-of-file imports, called out with a comment at the import site.

### 9. `rebuild_database` replays health kinds too, using the same idempotent dispatch

`raw_object.kind` filtering changed from `startswith("fit_")` to `startswith("fit")` — this
still matches Phase 1/2's legacy `"fit_activity"` kind and the new generic `"fit"` kind. Replay
calls the same `ingest_fit_bytes()` used at ingest time (passing the archived `external_id`
through, so rebuilt activities keep the same external identity); `archive_raw_bytes`'s
content-addressed idempotency means re-archiving already-archived bytes is a no-op lookup, not a
duplicate. `"daily_summary_json"`/`"hydration_json"`-kind raw objects are replayed through
`parse_daily_summary_json`/`parse_hydration_json` + `ingest_health_batch` directly (no FIT
dispatch involved). `health_observation`, `health_stream`, `sleep_session`, and `sleep_stage`
were added to `_REBUILDABLE_TABLES` (children before parents: `sleep_stage` before
`sleep_session`) so a rebuild actually wipes and reproduces health data, not just activity data.

### 10. `write_health_stream()` merges by timestamp instead of overwriting

Unlike `write_activity_stream` (one Parquet file, fully rewritten from a single activity's
parse), a health metric's monthly Parquet file is built up incrementally — separate daily source
files (one `WELLNESS`/`HRV_STATUS` FIT file per day) each contribute a few dozen points to the
same month's file across many separate ingest calls. `write_health_stream()` reads the existing
file if present, merges new points into it keyed by timestamp (last write wins), and rewrites
sorted. Confirmed idempotent and correctly-merging on real data: two different days' stress
files into one month file, and re-writing the same file twice produced a stable sample count.

## Consequences

- `health_observation`/`health_stream`/`sleep_session`/`sleep_stage` go from schema-only to
  populated for the first time, closing the gap both ADR 0002 and ADR 0003 flagged.
- `garmin_export`'s health/wellness JSON handling (ADR 0003 decision 4) remains explicitly
  unextended — only `fit_folder` recognizes `daily_summary_*.json`/`hydration_*.json`. If a real
  Garmin export archive turns out to contain these same files, wiring `garmin_export` to the same
  JSON parsers is a small, targeted follow-up, not a redesign.
- The lazy import between `fit_folder.py` and `ingest_dispatch.py` (decision 8) is a structural
  wart worth remembering if either module grows further — a third shared module for
  `IngestResult`/`ingest_canonical_batch` would remove it, but wasn't justified for two call
  sites.
- Formal automated tests (`tests/health/test_fit_parser.py`, `tests/health/test_json_parser.py`,
  `tests/test_ingest_dispatch.py`) and a full `sync import fit-folder` run against the real
  Monitoring/2025 directory are tracked as follow-up tasks, not yet done as of this ADR.
