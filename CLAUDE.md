# CLAUDE.md

Self-hosted fitness & health data platform. Garmin + Strava in, one owned SQLite+Parquet
archive, a REST/JSON API an AI agent can write notes through, a fast web frontend. Runs on a
Synology DS1019+ (Celeron J3455, no AVX/AVX2, 8GB RAM) behind an existing reverse proxy;
developed on Windows + Docker Desktop.

**Current phase: 0 (repo skeleton).** See the phase table in the project brief (kept outside
this repo) for the full 10-phase plan. Do not skip ahead — each phase has its own ADR in
`docs/adr/` and its own acceptance criterion.

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
7. **Additive schema evolution.** A new activity type or health metric needs zero migrations
   and zero code changes — only a registry row.

## Architecture (target shape — most of this doesn't exist yet in Phase 0)

- **Bronze/raw archive**: every vendor HTTP response and every FIT/TCX/GPX file, gzipped,
  content-addressed, immutable.
- **SQLite (WAL mode)**: metadata, daily/summary health observations, activity summaries,
  laps/splits, rollups, notes. What you filter and join on.
- **Parquet, one file per activity or per metric-month**: per-second activity streams and
  intraday health streams. What you plot. Never rows-in-SQLite for this.
- **DuckDB**, attached read-only, for analytical queries spanning SQLite + Parquet in one
  statement.
- **Adapters** implement one `SourceAdapter` protocol (`health_check`, `authenticate`,
  `list_changed`, `fetch_raw`, `parse`). When a vendor breaks — and Garmin already has, twice,
  as of this writing — the fix is confined to one adapter file. If fixing a vendor break means
  touching the schema, the design is wrong; stop and say so.
- Multi-tenant from day one: every data table carries `athlete_id`, scoping is enforced in the
  repository layer, not per-endpoint. Only one athlete row exists today.

## Commands

```bash
# Python (uv-managed)
uv sync                      # install deps (installed via `pip install uv` if uv isn't on PATH)
uv run pytest                # test suite
uv run ruff check .          # lint
uv run mypy                  # strict type check

# Frontend
cd frontend && npm install
npm run typecheck            # tsc --noEmit
npm run build                # tsc --noEmit && vite build

# Full stack (Windows/Docker Desktop — compose.override.yml auto-merges)
docker compose up --build
curl http://localhost:8000/api/v1/healthz

# NAS deploy (never build on the NAS — see docs/DEPLOY.md)
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
