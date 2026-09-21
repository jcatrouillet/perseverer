# ADR 0005: Phase 2 extension — real Garmin GDPR export: nested-zip FIT discovery + export JSON

## Status

Accepted. Built and validated against a real Garmin "Export Your Data" archive (172MB,
`Garmin_Connect_export_20260803.zip`) — the acceptance step ADR 0003 explicitly deferred to
the user.

## Context

`garmin_export.py` (ADR 0003) was built and tested only against a synthetic export tree and
Phase 1's local `HealthData\FitFiles\Activities` folder, both organized with loose `.fit`
files matching `<id>_ACTIVITY.fit`. No real GDPR export archive existed at the time — Garmin's
export takes days to generate, and the JSON schema inside one was, by ADR 0003's own words,
"undocumented and unsampled." That gap has now closed.

Direct inspection of the real archive (via `zipfile`, without extracting anything into the
repo) showed the actual structure is fundamentally different from what the adapter assumed:

- **No loose `.fit` files anywhere in the top-level tree.** Every real FIT file — activities
  and device health/monitoring uploads mixed together, confirmed by decoding a sample that
  turned out to be HRV-only, not an activity — lives one zip-level deeper, inside
  `DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part{1-6}.zip` (~5000 entries each)
  plus two more single-file zips (`..._LhaBackup_Part1.zip`, `..._PrimaryTrainingBackup_Part1.zip`).
  Filenames are `<email>_<numericId>.fit`, never `<id>_ACTIVITY.fit`. As built, `garmin_export`
  would have silently imported zero real FIT files from this archive.
- **`DI_CONNECT/DI-Connect-{Wellness,Metrics,Aggregator}` hold real health data**, but shaped
  as a JSON array of ~100 day/event records spanning a multi-month date range per file (e.g.
  `2023-01-12_2023-04-22_87061520_sleepData.json`), not the one-file-per-day
  `daily_summary_*.json`/`hydration_*.json` shape `fit_folder` already parses. ~24 distinct
  report kinds exist (`sleepData`, `UDSFile`, `HydrationLogFile`, `TrainingReadinessDTO`,
  `EnduranceScore`, `HillScore`, `ActivityVo2Max`, `healthStatusData`, `AbnormalHrEvents`,
  `BloodPressureFile`, and more), each mostly flat scalars plus a few nested sub-objects
  (`sleepScores`, `spo2SleepSummary`).
- The export also bundles ~35 entirely unrelated top-level domains this Garmin account
  happens to touch (`INREACH`, `DI_TACX`, and several aviation/golf/baseball product-data
  domains) — none of it fitness/health data for this platform.

## Decisions

### 1. Nested-zip FIT discovery is a recursive extraction pre-pass, not a special case

Rather than teach the per-file dispatch loop to understand zip-within-zip, a pre-pass
recursively finds every `*.zip` under the extracted root and extracts each one into a sibling
directory named deterministically from the zip's own path (mirroring `_extract_if_zip`'s
existing `extract_root / path.stem` convention, so a re-run overwrites consistently rather
than accumulating). This repeats until no new zips are found. The existing flat
`root.rglob("*")` walk then needs no new branching for "found a fit file inside a zip inside a
zip" — it just sees more files on disk, exactly like before. The only change to the walk
itself is skipping `.zip`-suffix entries (their contents are now individually
archived/parsed; archiving the container blob itself would just duplicate already-archived
bytes with no new information).

### 2. External-id derivation gets a second, more permissive pattern, tried second

`_ACTIVITY_FILENAME_RE` (`<digits>_ACTIVITY.fit`) stays first and unchanged — it's confirmed
real for Phase 1's manually-organized data. A new `_EXPORT_FIT_ID_RE` (`_<digits>\.fit$`) is
tried next, matching the real export's `<email>_<numericId>.fit` naming. Files matching
neither (the two backup zips' fixed-name FIT files, e.g. `..._LhaBackup.fit`) fall back to
`ingest_fit_bytes`'s existing device-serial+start-time/sha256 heuristic — already built for
exactly this "no reliable filename id" case, no change needed there.

### 3. GDPR export JSON gets one generic parser, not ~24 bespoke ones

Given the volume and diversity of report kinds, hand-modeling each one (a dedicated
`TrainingReadinessDTO` table, a dedicated `HillScore` table, etc.) would be a large, open-ended
effort disproportionate to value and contrary to AGENTS.md's "additive schema evolution"
principle — a new report kind should need zero code changes, not a new bespoke parser. Instead,
one generic function (`parse_garmin_export_json`) treats every recognized file as a list of
dated records and flattens top-level scalars into `health_observation` rows keyed
`garmin.export.<report_kind>.<field>`, where `report_kind` is mechanically derived from the
filename (strip the email prefix, date range, and trailing profile id — e.g.
`UDSFile_2022-10-03_2023-01-11.json` → `"UDSFile"`). Nested sub-objects (`sleepScores`,
`spo2SleepSummary`, `activityUuid`) are not flattened — cataloged only, the same precedent ADR
0004 decision 5 set for Connect-API JSON. A record missing `calendarDate` is skipped rather
than failing the whole file (some report kinds may have occasional incomplete records; the
other ~99 records in the same file shouldn't be lost over one).

### 4. Anchor timestamp: a short priority list of common field names, not per-report-kind config

Different report kinds name their most-precise timestamp differently (`timestampGmt`,
`wellnessEndTimeGmt`, `sleepEndTimestampGMT`, `persistedTimestampGMT`, plain `timestamp`).
Rather than hand-curate an anchor field per report kind (~24 entries), the parser tries a
fixed priority list of the common names actually observed across samples and falls back to
midnight of `calendarDate` if none match — one small, generic rule instead of a growing
per-kind table.

### 5. Recognition is gated by directory, not applied blindly to every `.json`

Only files under `DI-Connect-Wellness`, `DI-Connect-Metrics`, or `DI-Connect-Aggregator` (path
relative to the export root) go through the new parser. Everything else stays on the existing
`_kind_for_suffix` raw-archive path:

- `DI-Connect-User` — account settings, profile images, contact info. Not health data.
- `DI-Connect-Fitness/*_summarizedActivities.json` — a bulk Connect-API activity listing.
  Redundant once the FIT files themselves are correctly extracted (decision 1); same precedent
  as ADR 0003 decision 3 (`garmin_connect_json`: archived, never parsed, because the FIT is
  authoritative).
- The ~35 non-`DI_CONNECT` domains (INREACH, aviation/golf/baseball product data) — entirely
  out of scope for a fitness/health platform. Zero special-casing added; they simply continue
  through the same generic by-extension archiving every unrecognized file already gets.

## Consequences

- `garmin_export` now actually does what its docstring always claimed: a real historical
  backfill from a real export archive, including health data, not just a synthetic-tested
  code path.
- The multi-source reconciliation `merge_decision`/`activity_source_link` was built for from
  Phase 1 onward gets its first real large-scale exercise: activities already known from
  `fit_folder`'s local folder should now also link via `garmin_export`, with zero duplicates.
- If a specific `garmin.export.<report_kind>.<field>` metric turns out to matter enough to
  deserve a dedicated column (e.g. a `TrainingReadinessDTO` widget), promoting it out of the
  generic catalog is a small, targeted follow-up — not a schema migration, since the field is
  already flowing through `metric_definition`/`health_observation` today.
- `DI-Connect-User` and `*_summarizedActivities.json` remain archived-but-unparsed; if a future
  feature needs account metadata or wants the Connect API's own activity list as a secondary
  source, that's new scope, not something this change silently gained.
