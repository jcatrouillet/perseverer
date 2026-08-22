# ADR 0006: Phase 3 — read API, precomputed rollups, DuckDB, and a notes write path

## Status

Accepted. Building against the real archive populated by Phase 2's extensions (2+ years of
real activity and health data, 1250 activities, 288,939 health observations).

## Context

Phase 2 finished ingestion but nothing could read it back — `api/main.py` had only
`/healthz`/`/version`. Two CLAUDE.md commitments had to actually be delivered in this phase,
not just referenced: precomputed rollups ("the Celeron cannot aggregate a decade of activities
per request... every dashboard/calendar/recap view reads a `*_rollup` table... never scans at
request time") and DuckDB attached read-only over SQLite+Parquet ("arrives with the read API
in Phase 3"). Direct exploration confirmed neither existed yet — `duckdb>=1.1` had been an
unused dependency since Phase 0, and no `*_rollup` table existed anywhere in the schema.

The user chose to infer Phase 3's endpoint scope from CLAUDE.md's hints, include the notes
write path now (not deferred to a later phase), scope notes to activities and days only, and
add a shared API key rather than defer auth.

## Decisions

### 1. Two new rollup tables, not one wide table

`activity`/`sleep_session` have fixed columns and roll up cleanly into fixed columns.
`health_observation` is `metric_key`/`value_num` EAV, and the same physiological fact already
carries different keys depending on source — confirmed by reading all three health parsers:
`resting_heart_rate` (FIT), `garmin.daily_summary.restingHeartRate` (Connect-shaped JSON),
`garmin.export.UDSFile.restingHeartRate` (GDPR export JSON). A wide rollup table would need a
hardcoded `metric_key` → column mapping that breaks every time a new source or report kind
appears. `health_metric_daily_rollup` stays EAV-shaped instead — grain
`(athlete_id, local_date, metric_key)`, storing `value_sum`/`value_avg`/`value_min`/
`value_max`/`value_last`/`n_observations` so the API can pick whichever aggregate a given
metric needs at read time, with zero code changes as new metric_keys appear. This is the same
"additive schema evolution" principle that already shaped `activity_metric`/
`health_observation` themselves. `day_rollup` (grain `(athlete_id, local_date)`) covers the
fixed-shape activity/sleep aggregates directly as columns.

### 2. `activity.local_date` added as a companion column

`activity` was the only core table without a `local_date` (`health_observation`/
`sleep_session` already have one). Without it, "which calendar day does this activity belong
to" needs UTC-offset arithmetic inline in the rollup refresh path. Populated by
`ingest_canonical_batch` going forward; the 776+ pre-existing rows backfill for free via
`sync rebuild` — parsing is already a pure, replayable function, so no bespoke data migration
was needed, only a schema migration plus a rebuild run.

### 3. Rollup refresh is wired to accumulate-then-refresh-once-per-date, not per-file

Every ingest entry point (`fit_folder.import_from_folder`, `garmin_export.import_garmin_export`,
`garmin_connect.sync_garmin_connect`, `rebuild.rebuild_database`) already loops per-item. Each
now accumulates the *distinct calendar dates* its newly-created rows touched (via new fields on
`IngestResult`/`HealthIngestResult`/`DispatchResult`) and calls
`rollups.refresh_daily_rollup(conn, athlete_id, local_date)` once per distinct date after the
loop, not once per file. A multi-year backfill touches at most a few hundred distinct dates,
never one recompute per file — the same reasoning that already justified `garmin_export`'s
recursive-zip discovery doing one archive pass, not N.
`refresh_daily_rollup` itself deletes-then-reinserts both rollup tables' rows for that one date
— the same recompute-from-scratch idempotency `rebuild.py` already uses for every other
derived table, and both rollup tables joined `_REBUILDABLE_TABLES`.

### 4. DuckDB: verified by direct testing against the installed package, not recalled

Per CLAUDE.md's own standing instruction ("verify a vendor library's API by introspecting the
installed package, not from memory"), DuckDB's actual behavior was tested directly rather than
assumed, surfacing two real gotchas:

- `CAST(timestamp_utc AS TIMESTAMP)` on the Parquet `TIMESTAMPTZ` column silently converts to
  the *host machine's local timezone*, not a straight tz-strip — verified: UTC midnight became
  `16:00` on this Pacific-configured dev machine. This is the exact class of bug ADR 0002
  decision 10 already hit once with SQLite/SQLAlchemy's `DateTime(timezone=True)`. The fix is
  the same shape: never trust the library's own timezone cast, use `epoch(timestamp_utc)`
  (verified correct, no drift) and reconstruct a UTC-aware `datetime` in Python.
- `INSTALL sqlite` fetches the extension over the network on first use, caching it locally.
  Given "never build on the NAS" and pull-only GHCR images, this has to be baked into the
  Docker image at build time (`api.Dockerfile`'s builder stage runs `INSTALL sqlite` into a
  known `extension_directory`, which gets `COPY`'d into the runtime stage), or the container
  would need outbound internet at first request in production — unacceptable for a
  self-hosted, NAS-deployed service.
- Also confirmed: a long-lived DuckDB connection with SQLite attached read-only sees fresh
  commits from a separate SQLite writer connection without re-attaching, and `.cursor()`
  returns a cheap duplicate connection sharing the same attach — this is what makes a
  per-worker-process singleton (rather than per-request attach) both correct and cheap.

**Only the stream-downsampling endpoint uses DuckDB.** With rollups precomputed in SQLite, the
calendar/dashboard endpoint needs no DuckDB at all — it's a plain indexed SQLite read. This
keeps the DuckDB surface area to exactly the one thing that actually benefits from it
(bucket-aggregating a per-second Parquet file), not a general query-builder abstraction.

### 5. Downsampling is one DuckDB SQL statement, not Python-side aggregation

Bucket-averaging (`GROUP BY floor((ts - min_ts) / bucket_width)`) runs entirely in DuckDB
against the Parquet file — exactly the kind of columnar aggregation DuckDB exists for, and the
Celeron benefits from not doing it in a Python loop. Three fixed tiers (`low`≈200,
`medium`≈1000, `high`≈20000 points) rather than an arbitrary client-specified point count,
keeping the response-size contract predictable. Requested `channels` are validated against the
activity's own `activity_stream.channels` (the trusted, ingest-time-written list, fetched via
SQLAlchemy first) before being interpolated into the SQL column list — DuckDB's parameter
binding covers values, not column identifiers, so this check is what stands between a
query-string parameter and a SQL-identifier-injection-shaped bug.

### 6. Auth: a single shared API key, fail-closed

`X-API-Key` header checked via `secrets.compare_digest` against `PERSEVERER_API_KEY`. Fails
closed (503) when the env var is unset, rather than fail-open — the safer default for a
personal health-data API, and consistent with this project's existing security posture (e.g.
`garmin_connect` structurally never falls back to credentials rather than failing open into a
credentialed client). Applied per-router (`Depends` on each of the five data routers), not at
the app level, so `/healthz`/`/version` — and the Dockerfile's existing unauthenticated
healthcheck — are untouched.

### 7. Notes: a single polymorphic table, scoped to activities and days

One `note` table (`entity_type` ∈ `{"activity", "day"}`, `entity_id` a ULID or ISO date)
rather than per-entity note tables — matches the project's existing preference for one
generic, additively-extensible shape over bespoke per-type tables (see decision 1). Scoped
narrowly to activities/days per this session's explicit decision; `sleep_session`/
`health_observation` notes are a small follow-up if ever needed, not a redesign, since the
table shape already generalizes.

### 8. `PERSEVERER_TRUSTED_PROXY_IP` added but not wired

The `Settings` field is added (matches the existing reserved-but-unused convention already
established for it in `.env.example`), but actually using it to set uvicorn's
`--forwarded-allow-ips` requires changing `api.Dockerfile`'s `CMD` from exec-form to shell-form
(exec-form can't expand env vars) — a small, unrelated Dockerfile change deferred as a
follow-up rather than folded into this phase's core scope.

## Consequences

- The calendar/dashboard endpoint is genuinely rollup-backed from day one, not a placeholder
  that scans `activity`/`health_observation` directly — the platform constraint CLAUDE.md
  states is now actually enforced by the code, not just documented.
- Any future ingest path (a hypothetical Strava adapter, say) must remember to report which
  local_dates it touched and call `refresh_daily_rollup` — this is now a real contract new
  adapters need to honor, not automatic. Worth flagging explicitly in `adapters/base.py`'s
  docstring if a fourth adapter is ever added.
- DuckDB's extension-baking requirement means `api.Dockerfile` has a new build-time step that
  must stay in sync with whatever DuckDB version `pyproject.toml` pins — a version bump that
  changes the extension format would need the Docker build step revisited too.
- `activity.local_date` backfilling and the first rollup population for an existing database
  are real operational steps, not automatic on `alembic upgrade head` alone. The real dev
  database was backfilled directly (`UPDATE activity SET local_date = date(start_time_utc)`
  plus one `refresh_daily_rollup` call per distinct date already present across
  `activity`/`health_observation`/`sleep_session`) rather than via `sync rebuild` — a full
  rebuild was avoided deliberately, because `rebuild.py` currently has a known, separately
  tracked gap (doesn't replay `garmin_export_health_json` raw objects, added in ADR 0005) that
  would have silently dropped ~277k already-populated GDPR-export-sourced
  `health_observation` rows with no way to restore them via replay. Once that gap is fixed,
  `sync rebuild` becomes the correct general-purpose backfill path again; until then, a direct
  backfill (as done here) is the safe option for any database that already has GDPR-export
  health data in it. Should be called out in `docs/DEPLOY.md`'s upgrade notes if this ever
  needs deploying to the NAS.
