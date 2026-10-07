# AGENTS.md

Guidance for anyone changing Perseverer: human contributors and coding agents alike (Claude Code
reads this through `CLAUDE.md`; Codex, Cursor, Gemini CLI and Copilot read it directly).

Perseverer is a self-hosted fitness and health platform: Garmin, Strava, Apple Health, a Eufy
scale and the Kaya climbing logbook in; one owned archive (raw files + SQLite + Parquet); a REST
API, an MCP server and a React web app out. Start with
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Rules that override everything

1. **Raw first.** Archive every vendor byte (files, and every HTTP response with URL, timestamp,
   status and SHA-256) *before* parsing it. Parsing is a pure function of the archive; the whole
   database must be rebuildable from it without contacting a vendor.
2. **Never drop an unknown field.** Register every unmapped FIT field, message type or JSON key in
   `metric_definition`. If you are about to discard a field because you don't recognise it, that
   is the bug.
3. **Idempotent ingestion.** Content hashes and upserts; running any import twice is a no-op.
4. **Provenance per field.** Every value records its source. When sources disagree, keep both and
   pick at read time with an explicit priority.
5. **Never destructive.** No sync or migration deletes raw data. Soft-delete with `deleted_at`;
   store athlete corrections as durable overrides.
6. **SI units in storage**, converted only for display. Timestamps are naive UTC datetimes
   (SQLite does not keep `tzinfo`) plus the local UTC offset; `local_date` is offset-adjusted.
7. **Additive schema.** A new metric or activity type needs a registry row, not a migration.
8. **Rollups, not scans.** Pages read `*_rollup` tables refreshed at ingest. Request-time
   computation is only for bounded lookups (one activity, one goal period, one race).

## Garmin safety (non-negotiable)

- The Garmin client is never given credentials. `authenticate()` loads the token store and raises
  `GarminAuthRequired` if it can't. Credentials are used only in `sync auth login` and the Settings
  login form, both through `adapters/garmin_connect.py::login_with_credentials`, and never stored.
- Every call is rate limited. A 429 aborts the whole run immediately: no retry, anywhere. Garmin's
  SSO locks accounts that retry.
- Writing to Garmin (workouts, courses) follows the same rules. Courses must be confirmed private
  or are deleted.
- The same posture applies to Kaya: tokens only, refresh never falls back to credentials.

## When a vendor breaks

The fix stays inside that adapter. If a vendor change seems to require a schema, API or frontend
change, the abstraction leaked; stop and rethink. Verify vendor library APIs by reading the
installed package (`garminconnect`, `mcp`, `duckdb`...), never from memory: these libraries change
quickly.

## Workflow requirements

Apply these to every change:

- **Run every check before calling work done:** `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy`, `uv run alembic check`, and in `frontend/`:
  `npm run typecheck`, `npm run format:check`, `npm test`, `npm run build`; plus `uv run pip-audit`
  and `npm audit --audit-level=high` when dependencies change. Report any check you could not run.
- **UI changes:** follow the existing design system (`metricStyle.ts` tones and icons, theme
  tokens, shared components) and check the rendered page at desktop and phone (375 px) widths,
  light and dark, before claiming it works.
- **Docs:** update [docs/API.md](docs/API.md) *and* `frontend/public/api-docs.html` for any API
  change, [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) for any stored field, and
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for any structural change.
- **Migrations:** `uv run alembic revision --autogenerate -m "..."` after changing
  `db/schema.py`; review the generated file; migrations must be additive.
- **Deploy only when the owner asks.** Develop and verify locally first.
- **Preserve unrelated work** in a dirty working tree; commit only your own changes.

## Conventions and pitfalls

Things that are easy to get wrong, each learned from a real bug:

**Dates and time**
- `DateTime` columns are naive UTC; never assume a value read from the database is tz-aware.
- `local_date` is the activity's own local date. Weekly backend rollups are Monday-based; the week
  start preference is a frontend display setting only.
- In the frontend, "today" is `dateUtils.ts::localIsoDate()` (browser-local), never
  `isoDate(new Date())`, which is the UTC date.
- Format UTC-midnight `Date`s with `timeZone: "UTC"`, or dates shift by a day west of UTC.

**Data model**
- Activity ids change on every rebuild. Anything the athlete sets must be keyed by something
  stable (start time, `(source, external_id)`, a vendor id) and re-applied by the rebuild.
- Every table except the shared catalogs carries `athlete_id`, and every query filters on it.
- Child tables need an index on their parent id; a unique key that leads with `athlete_id` can't
  serve `WHERE activity_id = ?`. Check new access patterns with `EXPLAIN QUERY PLAN` on real data.
- One logical value often has several metric keys (FIT session field, Strava CSV, Garmin daily
  summary, Garmin export). Read through the alias lists (`LOGICAL_METRICS`, `AVG_HR_METRIC_KEYS`...)
  in priority order.
- Raw vendor units stay raw and are converted on read: running cadence is single-foot (double it),
  Garmin's lactate-threshold "speed" needs ×10.
- Yoga, strength and breathwork arrive as `sport="training"` with the real discipline in
  `sub_sport`; display and matching must use the sub-sport.
- Running statistics match `sport == "running"` exactly; grouping uses `merge/engine.py::sport_family`.
- Return `null` / `available: false` when a value isn't known; never fabricate a zero.

**Runtime**
- `api` runs two uvicorn workers: no state in process memory (sessions, locks, MCP sessions). Use
  SQLite.
- Every container write goes under `/data`; `api` and `worker` run read-only.
- Vendor lookups that cost money or are rate limited (weather, geocoding) are cached and archived;
  public pages read the cache only.
- The PWA service worker must not intercept `/api/`, `/mcp`, `/share/`, `/oauth/` or the OAuth
  paths; keep `navigateFallbackDenylist` in `vite.config.ts` in step with nginx.

**Frontend**
- Keep the default route light: new pages and heavy libraries (maps, the exercise catalog) are
  lazy-loaded. A single precached file must stay under the PWA's 2 MB limit.
- Calendar and sleep requests pass `include_health_metrics=false` / `include_stages=false`; don't
  fetch data a screen doesn't render.
- Use the shared formatters (`formatDistance.ts`, `formatTime.ts`) so the athlete's unit and time
  preferences apply.

**Email and share pages**
- Email HTML: inline styles only, and pixel widths for bars (percentage widths collapse to 0 in
  table layouts). Verify by rendering, not by string assertions.
- Share pages never show health data and never trigger a live vendor call.

## Commands

```bash
uv sync                                    # Python deps
uv run alembic upgrade head                # create/migrate the database (seeds the first athlete)
uv run uvicorn perseverer.api.main:app --port 8008
uv run sync --help                         # every CLI command
uv run sync auth login                     # Garmin, interactive (MFA)
uv run sync import garmin-connect          # incremental sync
uv run sync import garmin-export <zip>     # full-history import
uv run sync rebuild                        # re-derive everything from the raw archive
uv run sync athlete set-password           # login for an athlete
cd frontend && npm install && npm run dev  # web app on :5173
```

## Where things are

| Path | Contents |
|---|---|
| `src/perseverer/adapters/` | One module per data source |
| `src/perseverer/{fit,gpx,tcx,health}/` | Parsers |
| `src/perseverer/ingest_dispatch.py` | The single entry point for every FIT file |
| `src/perseverer/db/schema.py` | The whole schema |
| `src/perseverer/rollups.py`, `fitness.py`, `performance_rollup.py` | Derived tables |
| `src/perseverer/insights/` | Rules engine |
| `src/perseverer/api/` | FastAPI app, routers, schemas, MCP server |
| `src/perseverer/worker/main.py` | Scheduled jobs |
| `frontend/src/` | React app; pure logic in `*.ts` modules next to components |
| `quadlet/`, `docker/` | Production units and images |
| `docs/` | Architecture, deployment, API, data dictionary |

`CLAUDE.md` only imports this file; edit `AGENTS.md`, never `CLAUDE.md`.
