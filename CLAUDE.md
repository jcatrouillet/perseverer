# CLAUDE.md

Self-hosted fitness & health data platform. Garmin + Strava in, one owned SQLite+Parquet
archive, a REST/JSON API an AI agent can write notes through, a fast web frontend. Runs on a
Synology DS1019+ (Celeron J3455, no AVX/AVX2, 8GB RAM) behind an existing reverse proxy;
developed on Windows + Podman Desktop. The NAS itself runs Docker (DSM Container Manager) —
the engine swap is dev-only, see `docs/adr/0001-phase-0-foundations.md` decision 8.

**Current phase: 1 (raw archive, FIT parser, fit_folder adapter, schema, merge engine core).**
See the phase table in the project brief (kept outside this repo) for the full 10-phase plan.
Do not skip ahead — each phase has its own ADR in `docs/adr/` and its own acceptance criterion.

## The one rule that overrides everything else

**Raw first, always.** Every byte fetched from any vendor is archived verbatim — original
FIT/TCX/GPX bytes, and the raw JSON of every HTTP response with request URL, timestamp,
status, and SHA-256 — *before* it is parsed. Parsing is a pure function over that archive. If
we discover a field we ignored today, we must be able to re-derive the entire database from
the archive **without re-contacting any vendor**. And: **never drop an unknown field.**
Unmapped FIT fields and unmapped JSON keys get auto-registered into `metric_definition`, not
discarded. If you're writing an adapter or a parser and you're about to throw away a field
because you don't recognize it — stop, that's the bug.

## Non-negotiable principles (§3 of the project brief)

1. Raw first, always — see above.
2. Never drop an unknown field — see above.
3. **Idempotent, re-runnable ingestion.** Running any sync twice is a no-op. Content hashing
   plus upserts, never blind inserts.
4. **Provenance per field, not per record.** Every stored value knows its source and when.
5. **Never destructive.** No migration or sync deletes raw data. Soft-delete only.
6. **SI units in storage** (metres, seconds, m/s, kg, °C, W, bpm), converted at the
   presentation layer. Timestamps: UTC + local UTC offset + IANA timezone name.
   **Implementation note**: every `DateTime` column is a *naive* Python datetime that's
   implicitly UTC — SQLite/SQLAlchemy don't actually round-trip `tzinfo`, so declaring
   `timezone=True` would be a lie (see `docs/adr/0002-phase-1-schema-and-ingestion.md`
   decision 10, and `docs/DATA_DICTIONARY.md`). Never assume a value read from the DB is
   timezone-aware.
7. **Additive schema evolution.** A new activity type or health metric needs zero migrations
   and zero code changes — only a registry row.

## Architecture

- **Bronze/raw archive** (`data/raw/`, table `raw_object`): every vendor HTTP response and
  every FIT/TCX/GPX file, gzipped, content-addressed at `<sha256[:2]>/<sha256>.gz`, immutable.
  Each blob has a JSON sidecar (`<sha256>.json`) mirroring its `raw_object` row — the sidecar,
  not the SQLite row, is the durable catalog; `sync rebuild` restores `raw_object` from
  sidecars first, which is what makes rebuilding after deleting the database entirely work.
- **SQLite (WAL mode)**: metadata, daily/summary health observations, activity summaries,
  laps/splits, rollups, notes. What you filter and join on. See `docs/DATA_DICTIONARY.md` for
  the full table list.
- **Parquet, one file per activity** (`data/parquet/<athlete_id>/<activity_id>.parquet`):
  full-resolution per-second activity streams. What you plot. Never rows-in-SQLite for this.
  Downsampling into low/medium/high tiers is a Phase 3 (API-serving) concern, not ingestion.
- **DuckDB**, attached read-only, for analytical queries spanning SQLite + Parquet in one
  statement. (Not yet wired up — arrives with the read API in Phase 3.)
- **Adapters** implement one `SourceAdapter` protocol (`health_check`, `authenticate`,
  `list_changed`, `fetch_raw`, `parse` — see `adapters/base.py`). `fit_folder` (`adapters/
  fit_folder.py`) is the first implementation: a polling directory importer, content-hash
  idempotent, feeding the shared FIT parser (`fit/parser.py`) and merge engine
  (`merge/engine.py`). When a vendor breaks — and Garmin already has, twice, as of this
  writing — the fix is confined to one adapter file. If fixing a vendor break means touching
  the schema, the design is wrong; stop and say so.
- **Never drop a field, concretely**: the FIT parser registers every field it sees into
  `metric_definition`, even ones it doesn't materialize a value for (a field on a known
  message it doesn't map to a column, or a field of an entirely unrecognized message type).
  See the module docstring in `fit/parser.py` for exactly which fields get values stored
  where versus cataloged-only.
- Multi-tenant from day one: every data table carries `athlete_id` except `athlete` and
  `metric_definition` (shared catalogs, not personal data) — enforced by a schema test
  (`tests/db/test_schema.py`), not just convention. Only one athlete row exists today.

## Commands

```bash
# Python (uv-managed)
uv sync                      # install deps (installed via `pip install uv` if uv isn't on PATH)
uv run pytest                # test suite
uv run ruff check .          # lint
uv run mypy                  # strict type check

# Database (SPORTHEALTH_DATA_DIR in .env controls where — defaults to ./data for host tooling)
uv run alembic upgrade head              # create/migrate schema, seeds the one athlete row
uv run alembic revision --autogenerate -m "..."   # after changing db/schema.py

# Ingestion (the `sync` console script — see cli.py)
uv run sync import fit-folder <path>     # one-shot import of every .fit file in <path>
uv run sync watch fit-folder <path>      # continuously poll <path> (--interval seconds, default 30)
uv run sync rebuild                      # wipe derived tables, replay the entire raw archive

# Frontend
cd frontend && npm install
npm run typecheck            # tsc --noEmit
npm run build                # tsc --noEmit && vite build

# Full stack (Windows/Podman Desktop — compose.override.yml auto-merges)
podman compose up --build
curl http://localhost:8008/api/v1/healthz

# NAS deploy — the NAS runs Docker (DSM Container Manager), not Podman; never build on the
# NAS, see docs/DEPLOY.md
docker compose -f compose.yaml -f compose.nas.yml pull
docker compose -f compose.yaml -f compose.nas.yml up -d
```

## Platform constraints that shape every decision

- **DS1019+ / Celeron J3455 (Goldmont): no AVX, no AVX2, only up to SSE4.2.** Anything built
  for x86-64-v3 illegal-instruction-crashes on the NAS. Native builds are capped at
  x86-64-v2; numpy/pyarrow/duckdb ship wheels that runtime-dispatch. CI runs an AVX-masked
  (QEMU `qemu64` CPU model) import smoke test specifically to catch this before it reaches
  the NAS.
- **8GB RAM total, shared with DSM.** Whole stack budgeted at ~1.5GB. uvicorn runs 2 workers,
  not `cpu_count()`. This is also why SQLite, not Postgres.
- **Never build on the NAS.** Images are built on Windows or in CI, pushed to GHCR, pulled by
  the DS1019+'s Container Manager.
- **Precomputed rollups are mandatory.** The Celeron cannot aggregate a decade of activities
  per request — every dashboard/calendar/recap view reads a `*_rollup` table refreshed on
  ingest, never scans at request time.
- **Windows dev, Linux prod.** LF enforced via `.gitattributes`. `pathlib` everywhere. The
  `fit_folder` watcher polls (no reliance on inotify — SMB/rsync-written files don't reliably
  fire inotify events inside a container).

## When a vendor library breaks

It will. `python-garminconnect` has already been rebuilt once (garth → curl_cffi) as of this
writing. The contract: **the fix stays inside one adapter file.** If you find yourself
changing the schema, the API contract, or the frontend because a vendor changed a JSON key,
stop — that's a sign the abstraction leaked, not a sign the schema was wrong.

## Docs

- `docs/DEPLOY.md` — Windows → NAS handoff runbook.
- `docs/DATA_DICTIONARY.md` — grows every phase; the source of truth for what every stored
  field means and where it came from.
- `docs/adr/` — one ADR per phase, written before implementation starts.
