# CLAUDE.md

**Perseverer** — a self-hosted fitness & health data platform. Garmin + Strava in, one owned SQLite+Parquet
archive, a REST/JSON API an AI agent can write notes through, a fast web frontend. Runs on
`bercy`, an Intel NUC6i55SYH (i5-6260U, AVX2, 32GB RAM) running Ubuntu Server 26.04 LTS, behind
an existing reverse proxy; developed on Windows + Podman Desktop. Production moved off an
original Synology DS1019+ target — see `docs/DEPLOY.md`'s history note and decision 8 of
`docs/adr/0001-phase-0-foundations.md` for the now-superseded NAS-era rationale (Docker via DSM
Container Manager vs. Podman in dev) that no longer applies now that both dev and prod run
Podman. Production is deployed as systemd Quadlet units (`quadlet/`), not Compose — see
`docs/DEPLOY.md`.

**Current phase: 8 (strava_export importer, merge visibility/split, rules-based insight engine —
see `docs/adr/0012-phase-8-strava-merge-insights.md`). Phase 7 (map explorer, recaps, PWA/offline
shell — see `docs/adr/0011-phase-7-map-recaps-pwa.md`; image export was scoped in but dropped
after verification showed the chosen library hangs on this app's Recharts-heavy pages, and PWA
service-worker registration itself still needs verification on a real device, not just the
build/manifest checks done so far). Phase 6.1 (frontend design overhaul — theming, Recharts,
colour/icon system, calendar date-navigator, rich activity cards, multi-panel activity detail,
day view, bounded activity-context enrichment), Phase 6 (calendar grid, weekly/monthly rollups,
Fitness & Form, health dashboard), Phase 5 (core dashboard frontend + per-athlete auth), Phase 4
(MCP server exposing the read API + notes), Phase 3 (read API, precomputed rollups, DuckDB,
notes write path), and Phase 2 (garmin_export/garmin_connect adapters, scheduler, staleness,
health/wellness ingestion, real-export nested-zip discovery) are complete.**
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
  Downsampling into `low`/`medium`/`high` tiers happens at serving time
  (`stream_query.py::downsample`, one DuckDB bucket-averaging SQL statement against the
  Parquet file — never ingestion-time, never a Python loop).
- **DuckDB**, attached read-only over the SQLite database (`api/duckdb_conn.py`), for the one
  query that actually benefits from it: `GET /activities/{id}/stream`. Everything else in the
  read API (activity list/detail, health, sleep, the rollup-backed calendar) stays on plain
  SQLAlchemy — DuckDB isn't a general query-builder abstraction here. See
  `docs/adr/0006-phase-3-read-api-and-rollups.md`.
- **MCP server** (`api/mcp_server.py`), mounted at `/mcp` inside the same `api`
  container/process, not a separate service — a deliberate, informed deviation from an early
  Phase-0 guess, made once Streamable HTTP's actual shape (a plain mountable ASGI app) was
  known. Eight tools, one per REST endpoint above plus `create_note`/`list_notes`; each tool
  calls its REST endpoint in-process via `httpx.ASGITransport`, reusing the REST layer's logic
  rather than a second implementation. Gated by the same `X-API-Key` via a raw ASGI wrapper
  (`Mount` bypasses FastAPI's own `Depends`). `mcp>=1.9,<2` — 2.x just went stable and isn't
  adopted yet. See `docs/adr/0007-phase-4-mcp-server.md`.
- **Per-athlete auth (Phase 5)**: `require_api_key` (`api/dependencies.py`) resolves — not just
  gates — the authenticated `athlete_id` from any of three credentials: the legacy shared
  `PERSEVERER_API_KEY` (→ `DEFAULT_ATHLETE_ID`, so the MCP server and existing scripts keep
  working unchanged), a per-athlete `X-API-Key` (SHA-256-hashed, `athlete.api_key_hash`), or an
  `Authorization: Bearer` JWT issued by `POST /auth/login` (password checked via stdlib
  PBKDF2, `auth/passwords.py`; signed with `PERSEVERER_JWT_SECRET`, `auth/tokens.py`). Every
  router query is scoped to the resolved athlete, not a hardcoded default. Provisioned via
  `sync athlete set-password`/`create-key` — CLI-only, no self-service signup. See
  `docs/adr/0008-phase-5-frontend.md`.
- **Frontend** (`frontend/src/`): Vite + React 19 + TS-strict, `wouter` for routing,
  `@tanstack/react-query` for data fetching, a hand-rolled SVG chart (no charting library) for
  the one stream-chart need. The API base URL is runtime-configured
  (`frontend/public/config.js`, regenerated at container start from
  `PERSEVERER_API_BASE_URL` via nginx's own `docker-entrypoint.d` mechanism) — never baked
  into the Vite build, since the reverse-proxy hostname doesn't resolve the same way inside vs.
  outside the house (double-NAT/split-horizon DNS, see `docs/DEPLOY.md`). `AuthGate` offers
  either credential path (password or a pasted API key); a background 401 clears the stored
  credential and re-prompts automatically.
- **Weekly/monthly rollups + Fitness & Form (Phase 6)**: `period_rollup`/
  `health_metric_period_rollup` are a rollup OF `day_rollup`/`health_metric_daily_rollup`
  (sum-of-sums, weighted averages), not of raw tables — `period_type` (`"week"`|`"month"`)
  discriminated rather than four separate tables. Week starts Monday. `fitness_daily_rollup`
  stores an independently-computed Coggan/Banister CTL(42d)/ATL(7d)/TSB EWMA over daily
  `fit.session.training_load_peak` — Garmin's own exports have no CTL/ATL/TSB at all, so the
  frontend shows this alongside (not reconciled against) Garmin's own Training Readiness/Status.
  Full history recompute on every relevant ingest run (`fitness.py::refresh_fitness_rollup`),
  not incremental — cheap even at ~1,400+ days, and a retroactive correction invalidates
  everything forward from it regardless of scheme. `GET /health/dashboard` merges the same
  logical field's several raw `metric_key` namespaces (`api/routers/health.py::
  LOGICAL_METRICS`, verified field-by-field against the real database, not assumed from parser
  docstrings). See `docs/adr/0009-phase-6-calendar-rollups-fitness-health.md`.
- **Adapters** implement one `SourceAdapter` protocol (`health_check`, `authenticate`,
  `list_changed`, `fetch_raw`, `parse` — see `adapters/base.py`). Five exist now:
  - `fit_folder` (`adapters/fit_folder.py`) — polling directory importer, content-hash
    idempotent. The universal offline importer/test harness for every other adapter. Also
    recognizes two narrow Garmin Connect-shaped health JSON filename patterns
    (`daily_summary_*.json`, `hydration_*.json`) — not a general JSON importer.
  - `garmin_export` (`adapters/garmin_export.py`) — historical backfill from a Garmin "Export
    Your Data" archive (directory or `.zip`), zero network calls. Recursively finds FIT files
    at any depth, including nested inside further `.zip` files — confirmed necessary against a
    real export, whose FIT files (activity and health mixed together) all live one zip-level
    deeper than the top-level archive; see `docs/adr/0005-phase-2-garmin-export-real-data.md`.
    Health JSON under `DI-Connect-Wellness`/`Metrics`/`Aggregator` is parsed generically
    (`garmin.export.<report_kind>.<field>`, one parser for all ~24 report kinds, not bespoke
    code per kind — ADR 0005). Everything else (account data, bulk activity summaries, and
    unrelated non-fitness product domains a Garmin account's export can bundle) is archived
    raw but not parsed.
  - `garmin_connect` (`adapters/garmin_connect.py`) — the primary, incremental, unattended
    sync path, and the adapter most likely to break. See the safety rules below before
    touching this file. Seven daily fetches per rolling-window date, each independently
    rate-limited/429-abortable and self-healing on the next run: activities (FIT download),
    `get_stats` (steps/HR/etc., `garmin.daily_summary.*`), `get_sleep_data`
    (`garmin.daily_sleep.*` + a real `sleep_session`/`sleep_stage` row — added after this
    adapter turned out to never fetch sleep at all, silently going stale the moment the last
    `garmin_export` backfill's own data ran out even though the daily sync kept succeeding),
    `get_hrv_data` (`garmin.daily_hrv.*`), `get_training_readiness`
    (`garmin.daily_training_readiness.*`), `get_training_status` (one call covering three
    export report kinds at once — `garmin.daily_vo2max.*`, `garmin.daily_heat_altitude.*`,
    `garmin.daily_training_status.*`), and `get_hydration_data` (reuses the same
    `parse_hydration_json` `fit_folder.py` already had, just a new `kind` string). Plus one
    *range* fetch per run (not per-date): `get_race_predictions` covers the whole rolling
    window in a single call (`garmin.daily_race_predictions.*`). A comprehensive metric-by-
    metric audit (checking every `garmin.export.*` report kind against the installed
    `garminconnect` package's live `get_*` methods) found and closed this same "never fetched
    live" gap for all of the above — each had silently gone stale the moment the last
    `garmin_export` backfill's data ran out, exactly like sleep above, even though the daily
    sync kept reporting success. Two low-value report kinds (`healthStatusData` — internal
    data-quality metadata, `outliersCount` almost always 0; `AbnormalHrEvents` — only 2 events
    across ~4 years of real data) were deliberately left export-only rather than wired live.
    Body battery (Phase 9, revised): `HealthStreamPoint`s under `garmin.daily_body_battery.level`
    in `health_stream`/Parquet, not `health_observation` — added because no daily-summary or
    GDPR-export field carries a real per-minute body-battery series, only 8 sparse named
    checkpoints. The first live source tried, `get_body_battery` (a *range* fetch, one call per
    run), itself turned out on live verification to return only ~6 sparse checkpoints/day —
    nowhere near dense enough. `get_stress_data` (a *per-date* fetch, like sleep/HRV/etc. above,
    not a range call — this endpoint only takes a single date) turned out to carry a genuinely
    dense, ~3-minute-cadence `bodyBatteryValuesArray` instead (confirmed live against the real
    account, not assumed), and is now the adapter's live body-battery source — see
    `health/json_parser.py::parse_daily_stress_json`'s own docstring. The original
    `get_body_battery`-based fetch/parser (`parse_daily_body_battery_json`) stay in place
    unchanged, purely so already-archived raw bytes of that shape still replay on `sync rebuild`
    (raw-first/never-destructive) — `rebuild.py` has a branch for each raw JSON `kind`. This
    response also carries a real intraday *stress* series and daily stress scalars
    (`avgStressLevel`/`maxStressLevel`) that aren't parsed into observations yet — cataloged via
    `unrecognized_field_keys`, not dropped, pending a future stress feature. Surfaces as a real
    intraday chart on the day view (`GET /health/stream`, the first endpoint to read
    `health_stream` at all), not the sparse connect-the-dots version the old fetch produced.
  - `strava_export` (`adapters/strava_export.py`, Phase 8) — historical backfill from Strava's
    "export your data" archive, zero network calls. `.fit`/`.fit.gz` files go through the same
    `ingest_dispatch.ingest_fit_bytes` as every other source (often literally the same Garmin
    FIT bytes Strava received via auto-upload); `.gpx`/`.gpx.gz`/`.tcx.gz` go through new
    `gpx/parser.py`/`tcx/parser.py` (bare geometry + whatever sensor extensions the file
    carries), with `activities.csv`'s own totals/sport classification overlaid afterward since
    GPX/TCX carry far less than FIT. See `docs/adr/0012-phase-8-strava-merge-insights.md` for
    the real archive shape this was built against (not assumed from docs).
  - `eufy` (`adapters/eufy.py`) — body composition (weight, body fat, muscle/bone mass, water %,
    BMR, visceral fat, metabolic age, protein ratio, BMI) from a Eufy smart scale, via the same
    Eufy Life app API a sibling `eufy-health-sync` project already uses in production, ported
    directly rather than reimplemented. Garmin has no body-composition data in this project at
    all, so this is the sole source. Unlike `garmin_connect`, deliberately NOT built around a
    persisted token-store/never-auto-login model — no evidence Eufy's API shares Garmin's SSO
    lockout fragility, and the sibling project's own plain-env-var, fresh-login-per-run pattern
    has run safely, daily, unattended for months. The one `last_device_data` endpoint always
    returns the athlete's *entire* reading history in one call (the `limit` param is silently
    ignored, confirmed live) — no separate backfill-vs-incremental mode; every run reprocesses
    full history, relying on content-addressed archiving and idempotent upsert to make repeat
    runs of unchanged readings cheap no-ops. `health/eufy_parser.py` flattens *every* scalar
    `scale_data` field into `eufy.scale.<field>` (not just the 9 fields the sibling project's
    own extraction uses) — see `docs/DATA_DICTIONARY.md` for the full field list and unit
    handling.
  Every `.fit` file, from any adapter (except `garmin_connect`, which only ever downloads
  activity FIT files), goes through `ingest_dispatch.ingest_fit_bytes` — archives once, tries
  the shared activity parser (`fit/parser.py`), falls back to the shared health parser
  (`health/fit_parser.py`) if it isn't an activity. No adapter has its own parser. See
  `docs/adr/0004-phase-2-health-ingestion.md`.
  When a vendor breaks — and Garmin already has, twice, as of this writing — the fix is
  confined to one adapter file. If fixing a vendor break means touching the schema, the design
  is wrong; stop and say so.
- **Merge engine + visibility (`merge/engine.py`, Phase 1; UI Phase 8)**: `is_same_activity()`
  is source-agnostic by design — comparing only start time / sport family / duration — and
  already runs on every ingest via `fit_folder.py::_find_merge_match`, so cross-source
  deduplication needed zero new matching code when `strava_export` arrived; every decision is
  logged to `merge_decision` with human-readable reasons. `GET /activities/{id}/sources` and
  `POST .../sources/{link_id}/split` (`api/routers/activities.py`) make merges inspectable and
  reversible — split re-parses that one source's already-archived raw bytes via `reparse.py`
  and `adapters/fit_folder.py::insert_new_activity`, without deleting anything or re-running
  merge-matching (which could just re-merge it right back). See ADR 0012.
- **Insight engine (`insights/`, Phase 8)**: rules-based, deterministic, no LLM involved (that's
  Phase 9's narrative layer, not this). Pure rule modules (`rules_efforts.py`,
  `rules_streaks.py`, `rules_pb.py`, `rules_load.py`, `rules_health.py`) take already-assembled
  plain-Python inputs and return `Insight` objects with no DB access of their own;
  `insights/engine.py::refresh_insights` does the one DB-touching assembly step and a full
  delete-and-reinsert into the `insight` table per athlete per run — same precedent as
  `fitness_daily_rollup`'s full recompute. Wired into every ingest entry point's touched-dates
  block, plus unconditionally into `garmin_connect.py`'s own daily-scheduled sync (no separate
  APScheduler job — that function already runs once/day regardless of new data, which is
  exactly what a "last 30 days"-style window needs). `GET /api/v1/insights` is a plain read,
  same rollup-mandate discipline as everything else. See `docs/adr/0012-phase-8-strava-merge-
  insights.md` for the exact windows/dimensions and the deliberately-adjustable thresholds.
- **`garmin_connect` safety rules, non-negotiable**: it never constructs a credentialed client
  automatically — `authenticate()` only loads the token store (`data/garmin_tokens/`), and if
  that fails, raises `GarminAuthRequired` rather than falling back to credentials. The *only*
  place credentials are ever used is `sync auth login`, run interactively by a human. A 429
  (`GarminConnectTooManyRequestsError`) aborts the current run immediately — no retry, ever,
  anywhere. Garmin's SSO 429-locks per account with no recovery path; see
  `docs/adr/0003-phase-2-garmin-adapters.md` for how this is enforced structurally, not just
  by convention.
- **Staleness is a first-class signal, not an afterthought.** `perseverer/staleness.py` checks
  (a) whether `garmin_connect` has succeeded recently — escalating from "warning" to "critical"
  past `PERSEVERER_GARMIN_STALE_ESCALATE_DAYS` (default 7) — and (b) whether
  `athlete.last_full_export_at` is fresh enough (`PERSEVERER_EXPORT_FRESHNESS_DAYS`, default
  90). Both fire a generic JSON webhook (`PERSEVERER_STALENESS_WEBHOOK_URL`) if configured.
  The worker container (`worker/main.py`, APScheduler) runs `garmin_connect` sync + this check
  daily (default 04:15 local, jittered); `garmin_export` is never scheduled, it's a manual
  CLI action.
- **Never drop a field, concretely**: the activity FIT parser (`fit/parser.py`), health FIT
  parser (`health/fit_parser.py`), and health JSON parser (`health/json_parser.py`) all
  register every field they see into `metric_definition`, even ones they don't materialize a
  value for (a field on a known message/object they don't map to a column, or a field of an
  entirely unrecognized message type/nested JSON structure). See each module's docstring for
  exactly which fields get values stored where versus cataloged-only.
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

# Database (PERSEVERER_DATA_DIR in .env controls where — defaults to ./data for host tooling)
uv run alembic upgrade head              # create/migrate schema, seeds the one athlete row
uv run alembic revision --autogenerate -m "..."   # after changing db/schema.py

# Ingestion (the `sync` console script — see cli.py)
uv run sync import fit-folder <path>     # one-shot import of every .fit (+ daily_summary/hydration .json) in <path>
uv run sync import garmin-export <path>  # backfill from a Garmin export archive (dir or .zip)
uv run sync import garmin-connect        # on-demand incremental sync (--days to override window)
uv run sync import eufy                  # body-composition sync (always full history, see adapters/eufy.py)
uv run sync watch fit-folder <path>      # continuously poll <path> (--interval seconds, default 30)
uv run sync auth login                   # interactive Garmin login (MFA prompt) — run this yourself
uv run sync auth status                  # token store presence + age
uv run sync report counts                # per-source activity counts + unmatched (single-source)
uv run sync rebuild                      # wipe derived tables, replay the entire raw archive
uv run sync athlete set-password         # interactive: set an athlete's username/password
uv run sync athlete create-key           # generate a per-athlete API key (printed once)

# Frontend
cd frontend && npm install
npm run typecheck            # tsc --noEmit
npm run build                # tsc --noEmit && vite build
npm run dev                  # Vite dev server w/ HMR — needs PERSEVERER_CORS_ALLOWED_ORIGINS
                              # in .env to include its origin (default http://localhost:5173)

# Full stack (Windows/Podman Desktop — compose.override.yml auto-merges)
podman compose up --build
curl http://localhost:8008/api/v1/healthz
# Every route except /healthz, /version, and /auth/login needs X-API-Key or
# Authorization: Bearer <jwt> — presenting neither fails closed (503) only when nothing is
# configured at all, never open. See docs/adr/0008-phase-5-frontend.md.
curl -H "X-API-Key: $PERSEVERER_API_KEY" http://localhost:8008/api/v1/calendar?start_date=2025-01-01&end_date=2025-01-31
curl -X POST http://localhost:8008/api/v1/auth/login -H "Content-Type: application/json" -d '{"username":"...","password":"..."}'

# bercy deploy — rootless Podman + systemd Quadlet units (quadlet/), no Compose; never build
# on bercy, see docs/DEPLOY.md
podman quadlet install quadlet/perseverer-api.container quadlet/perseverer-worker.container \
  quadlet/perseverer-frontend.container
systemctl --user enable --now perseverer-api perseverer-worker perseverer-frontend
```

## Platform constraints that shape every decision

- **Historical: DS1019+ / Celeron J3455 (Goldmont) had no AVX/AVX2, only up to SSE4.2.**
  bercy's i5-6260U has full AVX2, so this no longer binds the deploy target — but the
  x86-64-v2 CFLAGS cap in the Dockerfiles and CI's AVX-masked (QEMU `Westmere` CPU model)
  import smoke test are still in place as of this writing (not yet removed; see
  `docs/DEPLOY.md`'s environments table). Harmless to keep, and still useful if the NAS is ever
  pressed back into service for something else — a deliberate "cost nothing, don't rip out
  opportunistically" call, not an oversight.
- **32GB RAM on bercy, not meaningfully budget-constrained** — unlike the DS1019+'s 8GB shared
  with DSM, which is what originally forced the ~1.5GB whole-stack budget and 2-worker uvicorn
  cap. Those specific numbers haven't been revisited post-move; SQLite (not Postgres) remains
  the storage choice regardless — that's a raw-first/provenance-model decision this project is
  built around at this point, not something the old RAM ceiling alone justified.
- **Never build on bercy.** Images are built on Windows or in CI, pushed to GHCR, pulled by
  bercy's Quadlet units (`AutoUpdate=registry` + `podman-auto-update.timer`).
- **Precomputed rollups.** Originally mandatory because the Celeron couldn't aggregate a decade
  of activities per request; bercy's i5 likely could, but the pattern stays because it's good
  design regardless of hardware — every dashboard/calendar/recap view reads a `*_rollup` table
  (`day_rollup`, `health_metric_daily_rollup`, `rollups.py`) refreshed on ingest, never scans at
  request time. Every ingest entry point (`fit_folder`, `garmin_export`, `garmin_connect`,
  `rebuild`) accumulates which `local_date`s it touched and calls `refresh_daily_rollup` once
  per distinct date after its loop — bounded by dates touched, not files processed. A future
  adapter must honor this same contract. See `docs/adr/0006-phase-3-read-api-and-rollups.md`.
- **Windows dev, Linux prod.** LF enforced via `.gitattributes`. `pathlib` everywhere. The
  `fit_folder` watcher polls (no reliance on inotify — SMB/rsync-written files don't reliably
  fire inotify events inside a container).

## When a vendor library breaks

It will. `python-garminconnect` has already been rebuilt once (garth → curl_cffi) as of this
writing. The contract: **the fix stays inside one adapter file.** If you find yourself
changing the schema, the API contract, or the frontend because a vendor changed a JSON key,
stop — that's a sign the abstraction leaked, not a sign the schema was wrong.

**Verify a vendor library's API by introspecting the installed package, not from memory.**
`docs/adr/0003-phase-2-garmin-adapters.md` decision 1-2 is the example: introspecting
`garminconnect`'s actual `login()` source (not recalling its shape) surfaced a real
credential-fallback behavior that directly shaped the auth-safety design. Training data on
fast-moving vendor libraries is exactly what this project's brief warns is stale.

## Docs

- `docs/DEPLOY.md` — Windows → bercy handoff runbook.
- `docs/DATA_DICTIONARY.md` — grows every phase; the source of truth for what every stored
  field means and where it came from.
- `docs/API.md` — the REST API reference for third-party integration: every endpoint, param,
  and response shape, generated from the live OpenAPI schema and cross-checked against router
  source. A polished HTML twin of the same content is served by the frontend container itself
  at `/api-docs.html` (`frontend/public/api-docs.html`) — update both when an endpoint changes.
- `docs/adr/` — one ADR per phase, written before implementation starts.
