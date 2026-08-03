# ADR 0002: Phase 1 — schema, raw archive, FIT parsing, fit_folder, merge engine core

## Status

Accepted. Verified against 776 real FIT files from the user's own Garmin export archive
(`C:\Users\Jerome\HealthData\FitFiles\Activities`) — not just synthetic fixtures.

## Context

Phase 1 is where the project's central invariant first gets built and load-bearing: raw
bytes are archived before parsing, parsing is a pure function over the archive, and the
database must be fully reconstructable from the archive alone. This ADR was drafted and
approved (via plan mode) before implementation, per the project's ways-of-working rule; this
version also records what changed during implementation, since real data surfaced three
genuine bugs the plan didn't anticipate — documenting them here is more useful than pretending
the first draft was correct.

## Decisions from the approved plan

### 1. SQLAlchemy 2.0 Core (not ORM), all tables in `db/schema.py`

One `MetaData` with plain `Table` objects. The data model is heavily open-ended
(`metric_key`/`value_num` rows rather than fixed per-class attributes), and Core stays closer
to the SQL that DuckDB will later query across SQLite + Parquet in one statement than the ORM
identity-map layer would.

### 2. Full schema created in Phase 1, not incrementally per phase

`health_observation`, `health_stream`, `sleep_session`, `sleep_stage` are all created now even
though only FIT **activity** files are ingested this phase (no monitoring/sleep FIT parsing
yet — the available real data was 776 `_ACTIVITY.fit` files, no monitoring/sleep files).
Defining the full schema once avoids a Phase-2 migration that exists purely to add tables,
keeping that phase's migration focused on what Phase 2 actually changes.

### 3. Per-athlete content addressing, `source_locator` generalizes `request_url`

`raw_object.sha256` is unique per `(athlete_id, sha256)`, not globally — consistent with the
multi-tenancy rule that every index composes on `athlete_id` first, even though two athletes
sharing byte-identical activity data isn't realistic. `source_locator` (a file path or an
HTTP URL, nullable) replaces the original sketch's HTTP-only `request_url` so the same table
serves file-based adapters (`fit_folder`) and future API-based ones without a schema change.

### 4. `fit_folder` external_id: device serial + start_time, never the filename

The real archive's filenames (`<garmin_activity_id>_ACTIVITY.fit`) happen to be descriptive,
but a real device's `/GARMIN/ACTIVITY/` folder uses opaque 8.3-style names. Deriving identity
from FIT content (falling back to the file's sha256 if no device serial is present) means
idempotency doesn't depend on filename conventions that won't hold for every real-world drop
target (device folder, export archive, manual re-copy).

### 5. Merge engine wired into ingest from day one, even with one source

`fit_folder` checks every new activity against existing ones (same athlete, ±1 day window)
via `is_same_activity` before creating a new row, logging a `merge_decision` audit row
either way. With only one source this mostly catches accidental duplicate files, but it means
the matching machinery is proven and unit-tested (Hypothesis property tests on the threshold
boundaries) before Phase 2 depends on it for real cross-source reconciliation. On the real
776-file dataset, all 776 decisions were `new` — no false merges, no missed ones.

### 6. Deferred: stream downsampling/tiering, polyline simplification, non-activity FIT kinds

Phase 1 writes one full-resolution Parquet file per activity and stores the full-resolution
encoded polyline in both `encoded_polyline` and `simplified_polyline`. Tiering and real
Douglas-Peucker simplification are presentation-layer concerns (Phase 3 API serving, Phase 7
map explorer) that should be built when there's a consumer to validate against, not
speculatively now. The parser does not crash on non-activity FIT kinds (monitoring/sleep) —
see decision 9 — but doesn't model them either, since no real sample data of that kind was
available to build against.

### 7. New dependencies

- `garmin-fit-sdk` — official parser, correct profile/developer-field handling.
- `python-ulid` — sortable, coordination-free IDs for `activity.id`.
- `typer` — type-hint-driven CLI (`sync` command), pairs with the existing Pydantic config.
- `polyline` — standard encoded-polyline format for `route_geom`.
- `alembic` + `sqlalchemy` — migrations and Core, per the recommended stack.
- `fitdecode` (dev only) — independent cross-check parser for golden-file tests.
- `hypothesis` (dev only) — property-based tests for the merge engine.

## What real data changed from the plan

Three genuine bugs surfaced only once real files (and, critically, more than a handful of
them) were run through the pipeline. All three are fixed and covered by regression tests.

### 8. `raw_object`'s own metadata needed a JSON sidecar, not just a SQLite row

The first rebuild-after-delete test caught a real gap: `raw_object`'s row (sha256 → storage
path, source, kind) lived only in SQLite. Deleting the database file — the exact scenario the
acceptance criterion describes — would have destroyed the catalog `rebuild` needs to replay,
even though the gzipped bytes were still on disk. Fixed by writing a `<sha256>.json` sidecar
next to every `<sha256>.gz` blob (`archive.py`), and adding `restore_raw_object_table`, which
`rebuild_database` calls first to reconstruct `raw_object` rows from sidecars alone before
replaying anything. The archive directory, not the database, is now the actual source of
truth for "what has been archived" — SQLite's copy is a queryable cache of it. (Recovering
from a fully-deleted database is therefore two steps: `alembic upgrade head` recreates the
schema and reseeds `athlete`, then `sync rebuild` restores everything else — documented in
`docs/DEPLOY.md`.)

### 9. Unrecognized fields inside a *known* message, and high-frequency unknown message
types, were reaching neither `metric_definition` nor any value store

`garmin_fit_sdk`'s bundled profile doesn't name every message/field a real Garmin device
writes: real files decoded message types as bare digit-strings (`"288"`, `"325"`, ...) instead
of names, and known messages like `record_mesgs` carried integer-keyed fields alongside named
ones. The parser correctly captured *low-frequency* unknowns (session/lap/activity-level, one
row per activity) as generic metrics, but a genuinely unhandled-yet-valid field inside
`record_mesgs` (verified with a real FIT field, `grade`) was silently discarded — caught by a
golden-file test using a synthetic fixture built with `garmin_fit_sdk`'s own `Encoder`, not by
running against real files (all of which happened to only exercise fields this parser already
mapped). Fixed by having the parser collect every field key it doesn't materialize a value
for — from inside `record_mesgs` and from high-row-count unrecognized message types — into
`CanonicalActivity.unrecognized_field_keys`, which the ingest layer registers into
`metric_definition` (category `"unknown"`) without storing a value for data whose meaning
isn't known yet. The original bytes remain fully recoverable from `raw_object` regardless;
this only affects whether the *catalog* knows the field exists before a human promotes it.

### 10. SQLite/SQLAlchemy silently drop `tzinfo` on read — `DateTime(timezone=True)` was a
lie for this backend

The full 776-file run failed ~100 files with `TypeError: can't subtract offset-naive and
offset-aware datetimes` — merge-matching comparing a freshly-parsed (timezone-aware) activity
against an existing one read back from SQLite (which comes back **naive**, confirmed
empirically: `DateTime(timezone=True)` does not survive a write/read round-trip on SQLite via
SQLAlchemy). It only surfaced once enough real activities existed close enough in time for the
±1-day merge window to actually find a candidate to compare against — no small synthetic test
happened to trigger it. Fixed two ways:
  - `db/schema.py` now declares every timestamp column as plain `DateTime()` and documents the
    project-wide convention explicitly: **all datetimes are naive, implicitly UTC**. Declaring
    `timezone=True` advertised a guarantee SQLite/SQLAlchemy can't keep, which is exactly what
    caused this.
  - `merge/engine.py`'s `is_same_activity` normalizes both operands to naive UTC before doing
    arithmetic, defensively, regardless of which shape either side arrives in — this is the
    boundary where a freshly-parsed (aware) value and a DB-read (naive) value are always going
    to meet.
  - A regression test (`test_matches_when_one_side_is_naive_and_the_other_aware`) locks this in.

This is the clearest example in Phase 1 of why the acceptance criterion specifies **100 real
files**, not a handful of synthetic ones: none of the three bugs above were reachable with a
small, quiet, single-activity test — they needed either a full delete-and-rebuild cycle, a
field FIT's real profile doesn't name, or enough real activities for the merge window to
actually have a neighbor to compare against.

## Consequences

- Any future code that reads a timestamp back from the database and compares it against a
  freshly-constructed `datetime` must go through the same naive-UTC discipline. This is worth
  watching for in Phase 3 (API layer) and beyond.
- `restore_raw_object_table` means the archive directory (not the SQLite file) is the real
  disaster-recovery unit going forward — backup procedures (Phase 9) should treat
  `data/raw/` as at least as important as the SQLite file itself, arguably more so.
- The `unrecognized_field_keys` mechanism generalizes beyond FIT: any future parser (Garmin
  Connect JSON, Strava export) should follow the same shape — comprehensive cataloging,
  conservative value materialization for genuinely high-frequency/unknown data.
