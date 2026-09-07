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

**Current phase: 9 — backup + restore automation and hardening (dependency-vulnerability
scanning in CI, login brute-force lockout, container hardening for `api`/`worker`) are shipped;
see `docs/adr/0014-phase-9-backup-hardening.md`. This phase's third original thread, an LLM
narrative layer over the rules-based `insight` table, is deliberately deferred (not built) —
see that ADR's decision 1 for the reference design and why. Phase 8 (strava_export importer,
merge visibility/split, rules-based insight engine — see
`docs/adr/0012-phase-8-strava-merge-insights.md`), Phase 7 (map explorer, recaps, PWA/offline
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
  router query is scoped to the resolved athlete, not a hardcoded default. `sync athlete create
  --display-name ...` creates the row itself (the one athlete-provisioning step no other command
  does — `set-password`/`create-key` only `UPDATE` a row that already exists); credentials are
  then provisioned via `sync athlete set-password`/`create-key` — CLI-only, no self-service
  signup. See `docs/adr/0008-phase-5-frontend.md` and docs/DEPLOY.md's "Provisioning a second
  athlete" for the full second-athlete sequence.
- **Frontend** (`frontend/src/`): Vite + React 19 + TS-strict, `wouter` for routing,
  `@tanstack/react-query` for data fetching, a hand-rolled SVG chart (no charting library) for
  the one stream-chart need. The API base URL is runtime-configured
  (`frontend/public/config.js`, regenerated at container start from
  `PERSEVERER_API_BASE_URL` via nginx's own `docker-entrypoint.d` mechanism) — never baked
  into the Vite build, since the reverse-proxy hostname doesn't resolve the same way inside vs.
  outside the house (double-NAT/split-horizon DNS, see `docs/DEPLOY.md`). `AuthGate` offers
  either credential path (password or a pasted API key); a background 401 clears the stored
  credential and re-prompts automatically. Every map surface (`ActivityMap` thumbnail,
  `ActivityRouteMap` detail view, `MapExplorerPage`) renders CARTO's Positron **vector** basemap
  (`CartoBasemapLayer.tsx`, `@maplibre/maplibre-gl-leaflet` mounting a MapLibre GL layer inside
  the existing react-leaflet `MapContainer` — markers/polylines/popups all stay on Leaflet's own
  renderer, only the basemap layer is MapLibre) — a revision of ADR 0011 decision 1's original
  raster-tiles/OSM choice, made once a real CARTO API key was available. The key
  (`window.__PERSEVERER_CONFIG__.cartoApiKey`, `mapBasemap.ts`) follows the exact same
  runtime-config mechanism as `apiBaseUrl` above (`PERSEVERER_CARTO_API_KEY` env var), even
  though it isn't actually secret — CARTO's basemap product is designed for client-side
  embedding, like a Mapbox public token. `mapTiles.ts`'s canvas rasterizer for GIF export
  deliberately stays on CARTO's *raster* tiles (just now carrying the same key) — capturing a
  vector/WebGL basemap into a still frame needs an actual MapLibre render pass read back via
  `getCanvas()`, a materially different problem left for if it's ever actually needed.
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
- **Fitness & Form / Health tabs (`FitnessPage.tsx`/`HealthPage.tsx`)**: both pages are entirely
  metric-over-time trend charts, one metric selected at a time — not every chart stacked at
  once (an earlier version of this redesign did stack them; the user changed their mind once it
  shipped, since scrolling past a dozen charts to reach one was worse than the old flat list it
  replaced). `MetricExplorer.tsx` is the shared list+detail shell (a left-hand metric list, the
  selected one's chart on the right, falling back to the first available metric if the current
  selection has no data); each page builds its own `ExplorerMetric[]` filtered by
  `hasAnyRawValue` against the *unwindowed* daily series, so the list only ever offers a metric
  that actually has some data somewhere in history, not just in the current window. `TrendChart.tsx`
  (a generic Recharts `ComposedChart` over any number of named series, one or two Y axes,
  optional area fill) renders the active metric's chart; one navigator, `TrendControls.tsx`
  (Week/Month/Year/All-time resolution + prev/next), is hosted once via `MetricExplorer`'s
  `detailHeader` slot and drives whichever chart is currently selected. Switching resolution
  (Week → Month → Year) keeps the same `anchor` date rather than resetting it to today — a week
  in August 2025 switches to "August 2025", not the current month — since `computeWindow` only
  ever needs *a* date inside the target window to re-derive that window's own start/end, and the
  anchor already names one (the app's own state, not a value TrendControls hands back). The
  prev/next arrows themselves also stay at a fixed screen position across every click:
  `.trend-controls__label` (`layout.css`) is given a fixed `min-width` and centered text so the
  label's own varying width (`"May 2026"` vs `"September 2026"`, or a week's date-range label,
  the widest of any resolution) can't shift the whole nav block, which sits flush right via the
  parent's `justify-content: space-between`. `trendWindow.ts` computes the
  window (`computeWindow`) and buckets an already-fetched daily series into it
  (`bucketSeriesToWindow`): week buckets by day (one point per day, i.e. no averaging needed),
  month by ISO week, year and all-time by calendar month — averaging whichever real observations
  fall in each bucket, `null` (not zero, not omitted) where none do. Deliberately client-side:
  neither `fitness_daily_rollup` nor `health_metric_daily_rollup` has a week/month/year
  pre-aggregation (only `period_rollup`'s own week/month grain exists, and that's activity
  totals, not fitness/health), and a daily series over this app's real history (~1,400+ days) is
  cheap enough to fetch once (`EARLIEST_PLAUSIBLE_DATE` to today, one `GET /fitness` +
  one `GET /health/dashboard` call per page) and slice/average client-side on every navigation
  click with zero further network round-trips. Fitness & Form charts VO2max, HRV, Lactate
  threshold (pace + heart rate, dual-axis — see the `garmin_connect` bullet below for where this
  data comes from), and the combined Fitness (CTL)/Fatigue (ATL)/Form (TSB) triad (also
  dual-axis, TSB on its own axis with a zero reference line, same shape the old single-purpose
  `FitnessChart.tsx` used). Health charts all fifteen `LOGICAL_METRICS` this project surfaces
  for an athlete's own body/vitals — weight, BMI, SpO2, steps, and the ten Eufy body-composition
  fields each on their own chart, plus three combined two-line charts following the same pattern
  as CTL/ATL (Heart rate: max+resting; Respiration: waking+sleep; Blood pressure: systolic+
  diastolic, the latter two newly promoted to `LOGICAL_METRICS` — blood pressure had no logical
  metric before this, `apple_health.blood_pressure_{systolic,diastolic}` being its only source)
  — plus Sleep time, read from `GET /sleep` (`sleep_session`, not `health_observation`) since
  that's a dedicated table, not part of the EAV metrics pipeline. `FitnessChart.tsx`/`HealthTrendChart.tsx`/
  `HealthMetricTiles.tsx`/`SleepDurationChart.tsx` are untouched and still power the Fitness &
  Form/Health cards embedded in the calendar's own Month/Year/All-time views (a different,
  calendar-period-scoped use case this redesign didn't touch) — `CORE_METRICS`/`HRV_METRIC`/
  `WEIGHT_METRIC` stay exported from `HealthPage.tsx` unchanged since those views import them.
  `earliestDateForKeys` (`trendWindow.ts`) scopes "All time" (and `canGoPrevious`) to the
  *selected* metric's own keys, not a merge across every metric the page fetches — both pages
  fetch one combined series per data source (all of `LOGICAL_METRICS` in one `GET
  /health/dashboard` call, say), so an unscoped `earliestDate` over that whole merge meant
  every metric's "All time" silently started wherever the single oldest metric on the page
  began (Sleep's own 2022-onward data showing an x-axis stretching back to a 2016 weight
  reading it has nothing to do with) — a real bug, not a hypothetical one. Each page resolves
  its own currently-active metric (mirroring `MetricExplorer`'s own selected-or-first-available
  fallback, since the page needs that resolved key *before* handing `MetricExplorer` a window
  to bucket against) and computes `dataStart` from only that metric's keys. Heart rate's Max +
  Resting pairing is the one `series()` call in `HealthPage.tsx` that takes an explicit colour
  override rather than deriving it from `metricStyle.ts` — both are legitimately the same `"hr"`
  tone everywhere else in the app (that's metricStyle.ts's whole point, one hue per metric), but
  reusing it for both lines on this one *combined* chart made them indistinguishable, so Resting
  gets `toneColor("pace")` instead, scoped to just this chart.
- **Insights: independently-computed race predictions + threshold/max-HR**:
  `performance_daily_rollup` (`performance_rollup.py::refresh_performance_rollup`) is a second,
  separate rollup alongside `fitness_daily_rollup` — same full-history-recompute-on-every-ingest
  contract, same "shown alongside, never reconciled" posture toward Garmin's own precomputed
  fields (`garmin.daily_race_predictions.*`, `garmin.daily_lactate_threshold.*` stay untouched;
  `athlete_running_load_config`'s manually-configured threshold pace for rTSS is untouched too —
  this is a separate, display-only value). Everything derives from one **42-day trailing
  maximum** of the athlete's own rolling VDOT (`vdot.py`, already used by the Pace-trends tab and
  `fitness_daily_rollup`'s own training-load input) — a *maximum*, not an average/EWMA, because
  an easy run's VDOT reads low from intensity rather than fitness (`DATA_DICTIONARY.md`'s own
  VDOT section already warns about this; `runningStats.ts::bestVdot` already takes the highest
  for the same reason). From that rolling VDOT: threshold pace is a closed-form inversion of
  `vdot.py`'s own `VO2(v)` equation at 88% of vVO2max (`compute_threshold_pace_s_per_km` — a
  representative point within Daniels' cited 86-92% range, verified by hand against published
  VDOT tables); the four race times (5k/10k/half/marathon) come from `predict_race_time_s`, a
  bisection search over duration exploiting that `compute_vdot(distance, T)` is monotonically
  decreasing in `T` (verified numerically, not assumed) — chosen over Riegel's simpler power-law
  formula because research shows Riegel underestimates marathon time from shorter races for many
  runners, while VDOT stays close across the same gap. Max HR is a **365-day** trailing maximum,
  deliberately much longer than VDOT's 42-day window since a true max-HR effort is rare
  week-to-week and a shorter window would flicker based on incidental recent effort rather than
  physiology — and deliberately **all sports**, not running-only, since a max-HR effort from
  cycling or hiit is equally real and restricting to running would silently discard it. This
  project's own empirical max HR is used as the PRIMARY source in place of any age-based formula
  (220-age, Tanaka, HUNT) — research shows all of them carry ~10-15bpm error even in their best
  forms — and is never overridden by a formula once real data exists. A brand-new athlete has
  zero empirical max HR for weeks, though, so `max_hr_bpm` falls back to the Tanaka formula
  (`208 - 0.7×age`, via the athlete's own optional `athlete.birthdate` set through
  `GET/PUT /settings/profile`, and `athlete_age.py::age_years_as_of`) for just that gap —
  `max_hr_source` (`"empirical"|"formula_fallback"|null`) records which path fired. This also
  unblocks threshold HR's own existing 88%-of-max-HR fallback below for a new athlete, since that
  fallback needs a non-null `max_hr_bpm` to compute from. Threshold
  HR is **empirical-first**: the median HR among runs within ±5% of that day's own threshold pace
  (by grade-adjusted pace) over the same 365-day window, requiring at least 3 qualifying runs to
  resist a single outlier; below that it falls back to 88% of that day's max HR (a representative
  point within the 85-92%-of-max-HR range research cites for well-trained runners) —
  `threshold_hr_source` (`"empirical"|"fallback"`) records which path fired, same provenance
  instinct as storing `training_load` alongside CTL/ATL. Wired unconditionally alongside
  `refresh_fitness_rollup` in `garmin_connect.py` (its rolling windows are date-dependent, so it
  must keep advancing through rest days exactly like `fitness_daily_rollup` does), and inside the
  touched-dates guard everywhere else (`fit_folder.py`, `garmin_export.py`, `strava_export.py`,
  `rebuild.py`, plus the trim/merge cascade and threshold-pace-config save in
  `api/routers/activities.py`/`settings.py`). `GET /performance` mirrors `GET /fitness` exactly.
  Frontend: two more Insights tabs, `RacePredictionsChart.tsx` (4 *separate* single-series
  charts, not one combined chart — the four distances span a ~10x time range that would flatten
  5k/10k to near-invisibility on one shared axis, and `TrendChart` only supports two y-axes
  anyway) and `ThresholdMaxHrChart.tsx` (threshold pace as its own single-line chart; threshold HR
  paired with max HR on one shared-axis chart instead, not with pace — both are bpm and directly
  comparable, e.g. threshold HR always sitting below max HR, whereas pace+HR together would need
  two separate axes) — both reuse the exact `MetricExplorer`/`TrendControls`/`trendWindow.ts`
  wiring `FitnessPage.tsx` established. `TrendChart.tsx`'s Y-axis ticks now use each axis's own
  series `formatValue` too (previously only the tooltip and the "latest" note did) — a bare-number
  axis reading e.g. "1529" for a race-time-in-seconds series was confirmed confusing in practice,
  not just a cosmetic gap.
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
    window in a single call (`garmin.daily_race_predictions.*`), and likewise
    `get_lactate_threshold(latest=False, aggregation="daily")` (`garmin.daily_lactate_threshold.
    {speed,heart_rate,power}` — a real, live-verified endpoint feeding the Fitness & Form tab's
    Lactate threshold chart, found by introspecting the installed `garminconnect` package per
    this project's own "verify, don't assume" rule for fast-moving vendor libraries; confirmed
    live against the real account that Garmin only emits an entry for a day it actually
    recomputed the value on, not one per calendar day in range). `heart_rate`/`power` are
    already real units (bpm/W); `speed` is NOT plain m/s despite the name — the raw value needs
    ×10 first (confirmed empirically: the athlete's own real numbers only make physiological
    sense, sitting between their real VO2max-interval and easy-run paces, after that
    correction), applied at the point of use (`FitnessPage.tsx`), matching this same adapter's
    existing single-foot-cadence-doubling precedent of storing the raw vendor value and
    converting on read, not at ingest. A comprehensive metric-by-
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
    handling. `sync_eufy()` itself still just takes credentials as plain parameters, but where
    those come from is now per-athlete: `athlete_eufy_config` (one row per athlete, same
    "narrow config, upsert not history" shape as `athlete_hr_zone_config`) rather than one global
    `Settings.eufy_*` env-var set — `resolve_eufy_credentials` (also in `adapters/eufy.py`) picks
    a DB row when one exists and falls back to those original env vars only for
    `DEFAULT_ATHLETE_ID`, so the original single-athlete deployment needs no migration.
    `GET /health/dashboard`'s `bmr_kcal` gets a formula-computed FALLBACK (`bmr.py::
    compute_bmr_kcal`, Mifflin-St Jeor) for a day with a resolved weight but no real Eufy `bmr`
    reading — e.g. any athlete with no Eufy scale at all — using that day's weight plus the
    athlete's own optional `birthdate`/`height_cm`/`sex` profile (`GET/PUT /settings/profile`).
    Only fires when all three profile fields are set; a real Eufy reading always wins and is
    marked with a synthetic `source_metric_key = "computed.mifflin_st_jeor"` +
    `n_observations = 0` when it's the formula instead — same provenance instinct as
    `threshold_hr_source` above, reusing fields `HealthDashboardDayOut` already had rather than a
    schema change. `metabolic_age` gets no such fallback (no legitimate formula for a
    Eufy-proprietary population-comparison figure).
  - `apple_health_export` (`adapters/apple_health_export.py`) — historical backfill from an
    Apple Health "export.xml" archive (Settings > [Name] > Export All Health Data on iOS), zero
    network calls. Imports blood pressure (full history — nothing else in this project has any
    BP source) and body mass/BMI/body-fat percentage dated strictly before the athlete's earliest
    `eufy.scale.weight` reading (`weight_before`, auto-detected from the DB by the CLI unless
    overridden) — this fills the real gap Eufy itself can't (Eufy only has data from when that
    scale was bought), rather than duplicating what Eufy already covers. A record whose
    `sourceName` is `"eufy Life"` is excluded even when its date falls before the cutoff — that's
    Apple's own sync of the same first Eufy reading landing on the other side of a UTC/
    local-date boundary, not a distinct pre-Eufy data point. `BodyFatPercentage` values are a
    0–1 fraction despite carrying `unit="%"` (a real HealthKit quirk, confirmed against a real
    export) and are multiplied by 100 to match `eufy.scale.body_fat`'s already-established true-
    percent convention; `BodyMass` gets a defensive lb→kg conversion (not exercised by any real
    export seen so far — every one has been 100% kg). Unlike every other file-shaped adapter in
    this codebase, which archives raw bytes per *unit fetched* (one raw_object per FIT file, per
    Garmin JSON report, per Eufy API reading), this adapter archives the **entire** export.xml as
    a single `raw_object` (`kind="apple_health_export_xml"`) — a real export can run to
    gigabytes with millions of XML elements, so per-record archiving (no precedent anywhere in
    this codebase) would mean millions of tiny raw_object rows for a one-time import, with no
    benefit over archiving the source file once. A single streaming parse
    (`health/apple_health_parser.py`, the only `ET.iterparse`-based parser in this codebase —
    every other XML parser here is small-file, non-streaming) then extracts the ~5 record types
    this pass cares about out of the ~76 present in a real export; everything else (ECG,
    nutrition, mindfulness, Apple Watch-era vitals Garmin already covers, and 996 real
    clinical/medical records from a connected health-records account — a different, more
    sensitive category of data entirely) stays deliberately unparsed, not lost: because the whole
    file is archived, extending this parser later to pull more record types never requires the
    user to re-supply the original export, just a code change plus `sync rebuild`.
    `weight_kg`/`bmi`/`body_fat_pct` on `GET /health/dashboard` (`api/routers/health.py`) carry a
    second alias for these three `apple_health.*` metric keys alongside their Eufy ones, so the
    existing weight/BMI/body-fat charts extend back through the pre-Eufy era with no frontend
    changes — the two sources never share a date by construction, so the same sequential
    outlier-rejection walk (`_body_composition_daily`) that already guards against a shared
    bathroom scale picking up someone else's reading keeps working unmodified across the source
    boundary. Blood pressure itself has no dashboard chart yet (deliberately deferred — see
    `docs/DATA_DICTIONARY.md`), queryable via `GET /health/observations` only for now.
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
  that fails, raises `GarminAuthRequired` rather than falling back to credentials. Credentials
  are only ever used in two places, both human-initiated and one-shot: `sync auth login` (CLI,
  MFA-capable) and `POST /settings/garmin/login` (the Settings page's own login form —
  MFA-*incapable* by deliberate choice, since bercy's API container runs multiple uvicorn
  workers with no shared memory, and Garmin's MFA resume needs one in-process client object
  across two requests; an MFA-challenged account falls back to the CLI). Both funnel through
  `adapters/garmin_connect.py::login_with_credentials`, one shared function, so this invariant
  has one implementation to audit, not two. A 429 (`GarminConnectTooManyRequestsError`) aborts
  the current run immediately — no retry, ever, anywhere. Garmin's SSO 429-locks per account
  with no recovery path; see `docs/adr/0003-phase-2-garmin-adapters.md` for how this is enforced
  structurally, not just by convention.
- **Scheduled workouts (`planned_workout`/`planned_workout_step`)**: the *one* place this app
  writes to a third-party account rather than only reading from it. Three sport tiers: **running**
  gets a full text syntax the athlete authors on the calendar (a real subset of intervals.icu's
  own workout-builder syntax — duration, a pace/HR/zone target, cadence, a simple `Nx` repeat
  block), parsed by two independent implementations kept in sync via one shared fixture table —
  `workout_syntax.py` (authoritative, server-side) and `workoutSyntax.ts` (instant client-side
  preview), same `gap.ts`/`gap.py` precedent — pushed via `GarminConnectAdapter.
  push_planned_workout` uploading a real Garmin `RunningWorkout`
  (`planned_workouts.py::build_running_workout`). **Yoga/bouldering** (`PLACEHOLDER_SPORTS`) are
  deliberately simpler — a name, a `duration_minutes`, and a display-only `scheduled_time`
  ("HH:MM", Garmin's own `schedule_workout()` has no time-of-day API at all), no structured
  syntax at all, pushed as a single no-target step for the whole duration
  (`build_placeholder_workout`; yoga gets a real Garmin sport type. Bouldering is really a
  rock-climbing sub-discipline — this app already knows that for *recorded* activities
  (`garmin_activity_summary.py`'s own `sport="rock_climbing"`/`sub_sport="bouldering"` pair) —
  but has no slot in the Workout Builder's own separate sport list at all, confirmed live against
  Garmin's real `GET /workout-service/workout/types` response (not the `garminconnect` package's
  own hardcoded `SportType` class): falls back to `SportType.OTHER`, a real constraint of that one
  Garmin subsystem, not a gap this app introduced). **hiit/strength_training**
  (`EXERCISE_SPORTS`) get real, named Garmin exercises picked from a bundled 1,527-exercise
  catalog (`garminconnect.exercises`, regenerated by `scripts/generate_exercise_catalog.py`) via
  the frontend's own picker (`ExerciseStepEditor.tsx`) — steps arrive already-structured, never
  parsed from text, and push through `build_exercise_workout`/`_build_exercise_step`
  (`weightValue` in grams despite `weight_kg`'s own kg storage unit, `category`/`exerciseName` as
  extra `ExecutableStep` fields via `ConfigDict(extra="allow")`, both live-verified against a
  real push + read-back). `build_workout_segment` (the repeat-group/step-order assembly, both
  hiit's factory and bouldering's) is shared across all three tiers via a `StepBuilder` callback
  rather than duplicated, and already handles any number of independent repeat groups plus
  standalone steps in one step list — the same mechanism running's own multi-`Nx`-block text
  syntax relies on. `ExerciseStepEditor.tsx`'s own authoring model exposes this as a flat
  top-level list of *items*, each either a standalone exercise/rest or a *set* (several
  exercises/rests repeated together N times, e.g. "3 rounds of squat, push-up, rest") — added
  after the first version only supported one repeat wrapping the entire workout, a frontend-only
  gap since the backend needed no change at all. The frontend's own exercise catalog is a
  dynamically-imported
  (code-split) asset, not a static import — a static import once pushed the app-shell bundle past
  vite-plugin-pwa's 2MB single-file precache limit and broke the production build. All three
  tiers push via the same `GarminConnectAdapter.push_planned_workout`, following the adapter's
  existing safety contract exactly (never a credentialed client of its own, rate-limited per
  call, abort-no-retry on 429) — via the generic `upload_workout(workout.to_dict())`, not the
  sport-specific `upload_running_workout`, which raises `TypeError` for anything but a real
  `RunningWorkout` (confirmed live by reading the check inside the installed `garminconnect`
  package itself; a real bug this project shipped once and fixed once yoga/bouldering exposed
  it). Push is automatic for anything due within `PERSEVERER_PLANNED_WORKOUT_PUSH_WINDOW_DAYS`
  (default 7) days (`worker/main.py::run_daily_workout_push`, its own daily schedule), plus a
  manual `POST /planned-workouts/{date}/push` override. Running's own push is live-verified end
  to end (2026-09-03, a real push + read-back against the author's own Garmin account): a cadence
  target riding alongside a pace target on the same step (`_cadence_extra`, an
  initially-undocumented Garmin field) does reach and round-trip correctly — Garmin's server
  echoes it back as `workoutTargetTypeKey: "cadence"` on read. See
  `docs/adr/0015-scheduled-workouts.md`. A separate `planned_workout_step.comment` column (added
  later) attaches a freeform note to one specific step — for running, an inline trailing
  `# comment` token on that step's own `source_text` line (`workout_syntax.py`'s grammar, works
  on a repeat-marker `<N>x` line too, attaching to that block); for hiit/strength_training, a
  small input on that exercise/rest row in `ExerciseStepEditor.tsx` (and one for a "set"/group
  itself, mapping to its own repeat-marker row — lost if the set's repeat count stays at 1, since
  no marker row is ever emitted then). Yoga/bouldering get neither — they already have an
  equivalent via their own freeform `source_text`. Never parsed further, never pushed to Garmin.
- **Exercise library page (`/exercises`, `ExerciseLibraryPage.tsx`)**: a browsable reference for
  every exercise the hiit/strength_training picker's catalog supports — 47 categories collapsed
  by default (native `<details>`, same convention as `ActivitySourcesPanel.tsx`'s own "Why these
  were merged"), each expanding to its exercise list with a photo (click to enlarge, via
  `Modal.tsx`'s own `panelClassName` lightbox variant), description, and muscle groups. Sourced
  by `scripts/generate_exercise_library.py` into `frontend/src/data/exerciseLibrary.json`
  (dynamically imported, like `ExerciseStepEditor.tsx`'s own catalog) — three real, honestly
  distinguished tiers rather than one blended guess, after an earlier draft's category-level
  fallback images repeated the same photo across dozens of exercises and the user rejected it
  outright: (1) ~160 exercises Garmin itself calls "detailed" have a real Garmin-authored photo
  and description, confirmed live against `connect.garmin.com`'s public (no-auth) exercise-data
  endpoint; (2) ~200-300 more get a photo + instructions matched by name against free-exercise-db
  (public domain) — reuse of one photo across several Garmin exercises is fine when they're
  genuinely the same base exercise once equipment words are stripped (not position/tempo/
  laterality words, which stay — a decline push-up is never treated as the plain one), with the
  weaker fuzzy-similarity tier capped to a handful of claimants per photo so it can't reproduce
  the original complaint (unbounded reuse there once let one photo stand in for 50+ only
  loosely-related variants, and separately let one bad match slip a chest-press photo onto an
  overhead-press exercise before the threshold was tightened); the rest fall through to
  (3) muscle-group text only (still real data, from Garmin's own master exercise list, just no
  photo) rather than a misleading stand-in. `garmin_url` is set **only for tier 1** — confirmed
  live (2026-09, in the user's own logged-in Chrome) that Garmin's own exercise page hangs on an
  infinite loading spinner for anything but a detailed exercise, even signed in, while a detailed
  one loads its real video/steps/tips correctly; linking every exercise there regardless of tier
  was the original mistake this corrected.
- **Share links (`share_link`, `sharing.py`, `api/routers/share.py`)**: an athlete-issued token
  granting unauthenticated, read-only access to one activity or one summary period. `GET
  /share/{token}` is public by omission, same exemption mechanism `/healthz`/`/version`/
  `/auth/login` already use — a route is "public" purely by never taking
  `Depends(require_api_key)`. Both pages are server-rendered HTML (hand-escaped f-strings, no
  template engine) built to mirror the *authenticated* frontend's own activity-detail and
  period-summary views as closely as a public page reasonably can — full stat sections
  (distance/HR/elevation/power/training-effect/running-dynamics/temperature/respiration), the
  Intervals (laps) table with server-computed GAP plus (when the activity has a planned workout)
  Interval/"Exp. pace or dist."/Expected-pace columns (the header text is the athlete's own
  verbatim wording, deliberately terser than the authenticated app's own column label) —
  `_expand_workout_steps` is a
  straight Python port of `frontend/src/workoutSteps.ts::expandWorkoutSteps` (unrolling
  `repeat_until_steps_cmplt` groups so `laps[i] <-> expanded[i]` positionally, same alignment the
  real app relies on), per-second interactive charts, and — for bouldering — the routes-by-grade
  chart and routes table. Deliberately excludes anything health-related (weight/HRV/sleep — this
  app's own existing stance is that health metrics aren't vetted for public exposure) and the
  context/comparison sections (percentile-rank/similar-activity tables, already a second
  aggregate query each — not worth exposing to anonymous traffic). Weather and time-in-zone,
  once excluded for the same "don't build a second implementation" reasoning, are shown after
  all: weather via `_cached_weather`, a plain read-only `SELECT` mirroring `weather.py::
  _read_cached`'s own query (duplicated, not imported — that function is module-private) that
  never calls the live fetch path (`get_or_fetch_activity_weather`), so a public URL still can't
  trigger a paid API call — an activity nothing ever fetched weather for just shows no Weather
  section; time-in-zone via the *device-reported* fallback only (`fit.time_in_zone.*` metrics,
  already loaded), never the athlete's-own-configured-zones stream computation.
  `.time-in-zone__fill` needs an explicit `display: block` in this page's own `<style>` block —
  the exact same bug `TimeInZoneChart.tsx`'s own CSS already hit and documented once: a bare
  `<span>` is `display: inline` by default, which ignores a percentage `width` entirely, so the
  zone bars rendered as empty (0×0) tracks despite the correct percentage in their inline style,
  confirmed live via `getBoundingClientRect()`, not just by reading the CSS. The per-second
  charts (`_svg_series_chart`) now carry real axis units and are wrapped for
  `_CHART_HOVER_SCRIPT` — one shared script, emitted once, reading each chart's own sibling
  `<script type="application/json">` blob to show a crosshair + exact value/elapsed-time tooltip
  on hover/touch — because a static chart image with no way to read a value off it wasn't enough.
  Every color on both share pages (stat tiles, chart lines, badges, page background/text) now
  comes from the exact same tone tokens `frontend/src/styles/theme.css` defines (dark default,
  `prefers-color-scheme: light` override — this page has no JS theme toggle to persist an
  explicit choice, so it only ever follows the visitor's OS setting) — CSS custom properties
  resolve fine as literal SVG presentation-attribute values (`stroke="var(--color-heart-rate)"`)
  since this is inline SVG in the same HTML document, not a standalone `.svg` file. The activity
  page's own stat tiles (distance/HR/elevation/power/training-effect/running-dynamics/temperature/
  respiration, bouldering's time-and-calories) go one step further, reproducing
  `StatTile.tsx`'s exact icon-chip + tone mechanism rather than just its colors: `st(label,
  value, icon, tone)` builds a `_Stat`, `_stats_grid_iconed` renders each as `<div class="stat
  tone-{tone}">` with an `<span class="icon-chip">` wrapping an inlined copy of the matching
  hand-rolled icon's raw SVG path data from `Icon.tsx` (`_ICON_PATHS`/`_icon_svg` — copied
  directly rather than shared via a symbol/sprite, since this is a one-off static page with no
  other icon reuse need); `.icon-chip`'s `color`/`background` read the same `--tone` custom
  property `.tone-*` sets, `color-mix(in srgb, var(--tone) 15%, transparent)` and all, matching
  `layout.css`'s own rule byte-for-byte. Every icon/tone pairing was verified against
  `ActivityStatsGrid.tsx`'s real per-stat assignments, not guessed from the label text — the one
  deliberate exception is Fueling (`ActivityFueling.tsx`), which has no `StatTile`/icon usage at
  all in the authenticated app either, so its share-page section stays on the older, plain
  `_stats_grid`/tuple rendering rather than inventing a mapping that doesn't exist upstream. The
  period share's
  sport breakdown covers Running, Hiking, Climbing (bouldering, with the same grade chart as the
  activity page, aggregated across every session in range), and Fitness (hiit/strength_training —
  note `sport_family()` in `merge/engine.py` has no single bucket for either of these, so this
  page unions `sport_family(sport)=="strength"` with the literal `sport=="hiit"` fallback itself)
  — Fitness & Form (CTL/ATL) stays, health stays excluded. Every one of these sections, plus the
  top-level totals, now carries the same icon-chip + tone treatment as the activity page, matched
  against each section's *own* real authenticated-app component rather than one blanket mapping:
  the top-level stats mirror `PeriodStatsCard.tsx` (Month/Year/All-time's real card — Distance/
  Longest streak/Total elevation as heroes plus Activities/Moving time/Active days/Longest
  activity/Busiest week-or-month-or-year/Favorite day/Average distance-time-speed/Average+Max
  heart rate; `earliestDateForKeys`-style per-field Python ports of `runningStats.ts`/
  `yearStats.ts`'s streak/weekday/busiest/average logic feed these, straight from the same
  `activity`+`activity_metric` query this function already runs, plus one extra query resolving
  avg/max heart rate per activity via `AVG_HR_METRIC_KEYS`/`MAX_HR_METRIC_KEYS`, already imported
  here for the activity page's own HR stats). `WeekView.tsx` has a materially different "Week
  stats" card with no streak/busiest/favorite-day/averages concept at all for a single week, so a
  **week** share deliberately keeps the original six-stat set (now iconed, not expanded) rather
  than inventing stats the real week view doesn't have. Running/Hiking/Climbing's own tiles are
  tuned to `RunningStats.tsx`/`HikeStatsCard.tsx`/`ClimbingStatsCard.tsx` respectively — Climbing
  here is deliberately all-`"elevation"`, a different mapping than the activity page's own single-
  climb section (`ActivityStatsGrid.tsx`), because they're two different real components with
  their own real mappings, not one shared source of truth. `Activities by type` is new: a colored,
  iconed horizontal-bar breakdown by sport (count and time together, since a static page can't
  offer `PeriodStatsCard`'s own live count/time pie toggle) — `_SPORT_ICON_PATHS` holds ten
  pictogram paths copied verbatim from the installed `@phosphor-icons/react` package's own
  `dist/defs/<Name>.es.js` "fill" entries (MIT), since `Icon.tsx`'s sport icons are real Phosphor
  components, not the hand-rolled stroke glyphs every other icon on this page reproduces — a
  second `.icon.icon--filled` CSS override (`fill: currentColor; stroke: none`) renders them,
  matching `layout.css`'s own identically-named rule.

  `RunningStats.tsx`'s own distance-bucket bar chart, trailing-window line chart, calendar
  heatmap, and personal-records table are now ported too (Month/Year/All-time only — see below)
  — a second, later pass once "match everything, be precise" made clear that "Running" meaning
  just four stat tiles wasn't actually close enough. Exact `sport == "running"`/`"hiking"`
  matching (not `sport_family()`) is used throughout, confirmed against `GET /activities?sport=`
  itself doing a plain `==`, not a family grouping — trail_running/track_running and walking/
  snowshoeing/alpine_skiing are real, deliberate exclusions from these two sections, not an
  oversight; this corrected a latent inaccuracy in the *original*, four-stat version of this
  section from earlier the same session. Which of the three real RunningStats "modes" a period
  gets is derived exactly like the real component (`spanDays <= 31` / `> 366` / else) from the
  *overall* activity date range (every sport, matching AllTimeView.tsx passing
  its own all-sport `start`/`end` into RunningStats, not a running-only range) — Month gets daily
  buckets/a 7-day trailing window/a one-row "Daily distance" strip; Year gets monthly buckets/
  90-day trailing/a "Daily distance" week-grid (`.running-heatmap__grid`, one column per week,
  one row per weekday, month-boundary dividers); All-time gets yearly buckets/365-day trailing/a
  "Weekly distance" year-rows grid (one row per calendar year). The heatmap itself needed no SVG
  at all — `running-stats.css`'s own encoding is pure CSS (`color-mix()` for an ordinary day's
  intensity wash, `conic-gradient()` for a long run's proportional-circle overlay, a `:hover`-
  revealed absolutely-positioned tooltip) — so this page reuses the *exact* same class names and
  rules rather than an SVG approximation of them; the one real deviation is a plain `<span>`
  where the authenticated app has a `<Link>`, since there's no public per-day/week view for an
  anonymous visitor to navigate to. Personal records (`_personal_records`, a straight port of
  `personalRecords()`'s own 0.9x-1.3x-tolerance-band approach) get an all-time-PR badge for
  Month/Year (a second, unbounded `sport == "running"` query, mirroring `useAllActivities`) but
  not All-time, matching `AllTimeView.tsx` itself never passing `allTimeRecords` at all (every
  record there already *is* the all-time one). `HikeStatsCard.tsx`'s featured-hike cards
  (Longest hike / by time / most elevation gain / highest point reached, each only if it's a
  genuinely different hike from ones already featured) resolve their location name via
  `geocoding.py::read_cached_location` — the same cache-only guarantee weather already has,
  confirmed live: an uncached hike's card simply omits the location rather than triggering a
  live Nominatim lookup. `YearView.tsx`'s own 12-tile month grid closes out the page, reusing the
  exact `period_rollup` query the "Distance by month" bar chart above it already runs (extended
  for two more columns) rather than a second query for the same rows. hiit/strength_training
  keeps the older plain `_stats_grid` (no confirmed icon/tone mapping — the authenticated app has
  no dedicated card for these sports at all, only the generic totals and the Activities-by-type
  breakdown), same "don't invent a mapping that doesn't exist upstream" reasoning as Fueling
  above; the day-by-day calendar grid (WeekView/MonthView's own per-day activity list, a
  different kind of view than a summary page) stays out of scope. The route map is the one
  deliberate
  exception to this file's otherwise zero-JS pages: a real interactive MapLibre GL map (CARTO's
  Positron vector basemap, same style the authenticated frontend's `CartoBasemapLayer.tsx` uses),
  loaded from a CDN as a `type="module"` script — MapLibre v6 shipped no UMD/global build at all
  (confirmed against the real published package: `dist/` only has `.mjs` files), so this needs
  named ESM imports and an explicit `setWorkerUrl()` pointed at the CDN's own worker file, not a
  plain `<script src=...>` global. A Play/Pause route-playback marker rides along on top of the
  same map, driven by the activity's own `lat`/`lon` Parquet stream channels (yes, GPS position
  really is stored per-sample, one real Parquet column each, not just baked into `route_geom`'s
  polyline — confirmed directly against `fit/parser.py`) at the same "low" tier (200 points) the
  charts already fetch, so no second, heavier stream read is needed just for a smooth-looking
  marker. Replay always takes a fixed 20 real seconds regardless of the activity's own actual
  duration — a deliberately simplified, non-draggable version of `ActivityRouteMap.tsx`'s own
  scrubber. `PERSEVERER_CARTO_API_KEY` is read by the API's own `Settings`
  now too (previously frontend-only) — not actually a larger exposure, since it's the same
  client-embeddable key the authenticated frontend already ships to every visitor's browser.
  `PERSEVERER_PUBLIC_BASE_URL` must be the athlete-facing origin (bercy: the frontend's own
  `:443` reverse-proxy rule, which nginx forwards `/share/*` from to the api container — see
  `docker/nginx.conf`), **not** `PERSEVERER_API_BASE_URL` — those are two different origins
  whenever the reverse proxy fronts api/frontend on different ports, which is exactly bercy's own
  setup; leaving it unset falls back to the *incoming* request's own base URL, which is the api's
  own port when the create-share call arrives there, not the origin a browser should open.
- **Calendar feed (`calendar_feed.py`, `api/routers/calendar_feed.py`)**: a Google-Calendar-
  subscribable public iCalendar (.ics) feed of the athlete's own `planned_workout` calendar —
  deliberately a *parallel* mechanism to `share_link` above, not a third `target_type` grafted onto
  it: `share_link.target_type` is a closed two-way branch (`"activity"`|`"period"`) with a bespoke
  `target_id` encoding per kind, and the whole file only ever emits `HTMLResponse`; a calendar feed
  is one continuously-regenerated view (not one activity/period) needing `text/calendar`, not HTML.
  Instead mirrors `athlete.api_key_hash`/`api_key_created_at`'s own shape — two new nullable
  columns directly on `athlete` (`calendar_feed_token_hash`/`calendar_feed_created_at`), one
  standing secret per athlete, replace-on-rotate, not a growing history of one-off tokens.
  `GET /share/calendar/{token}.ics` (mounted with `prefix="/share"`) reuses the exact same
  `location /share/` nginx prefix rule `share.py`'s own docstring describes — zero infra change.
  Never includes completed activities, only the athlete's own authored planned workouts — Google
  polls a subscribed feed roughly every 8-24h, not live, so this is rebuilt fresh on every request
  (no caching, no rollup precedent needed — `planned_workout` has none of its own either). Event
  rendering per sport tier (`planned_workouts.py::EXERCISE_SPORTS`/`PLACEHOLDER_SPORTS`):
  running/yoga/bouldering already have a human-readable `source_text` (workout syntax or freeform
  notes respectively), used verbatim as the event `DESCRIPTION`; hiit/strength_training has no
  `source_text` at all (steps arrive already-structured, never parsed from text — ADR 0015), so a
  small purpose-built renderer lists each real exercise/rest step instead — deliberately not a
  reuse of `workout_syntax.py::steps_to_source_text`, which is shaped for *recorded* pace-only
  steps, a different domain — each step's own `comment` (see the scheduled-workouts bullet above)
  is appended to that step's own line here. Running/yoga/bouldering need no code of their own for
  this at all: an inline `# comment` the athlete typed is already part of `source_text`'s raw
  text, flowing straight through to `DESCRIPTION` verbatim. A workout with `scheduled_time` set
  becomes a timed event using the
  athlete's own stored `athlete.timezone` (`zoneinfo.ZoneInfo`, real VTIMEZONE block via
  `icalendar`'s own `add_missing_timezones()` — verified empirically against the installed
  version rather than assumed); one without becomes an honest all-day event rather than a guessed
  time. `GET/POST/DELETE /settings/calendar-feed` (authenticated, alongside hr-zones/running-load
  in `settings.py`) publish/rotate/unpublish; the frontend's `CalendarFeedCard.tsx` mirrors
  `RebuildCard.tsx`'s status-query-plus-mutation shape, showing the fresh URL inline (via the same
  `share-button__url-row` markup `ShareButton.tsx` already uses) only once per publish/rotate,
  never re-shown afterward — same "raw token never recoverable again" posture as every other
  hashed secret in this codebase.
- **Settings-page operational actions**: `api/routers/settings.py` adds the web
  counterparts of four CLI-only commands — Garmin login/status, `sync import garmin-connect`
  ("sync now"), `sync rebuild`, and `sync import garmin-export`/`strava-export` (bulk .zip
  upload, the first `UploadFile` endpoint in this codebase). No job-queue infrastructure exists
  or was added — sync/rebuild/import all run via FastAPI's `BackgroundTasks` (the one existing
  precedent, `activities.py::get_activity_location`) and are polled through one generic
  `GET /settings/jobs/latest?source=...`, reading the same `ingest_run` table every sync/import
  entrypoint already writes (a new `source="rebuild"` value, written by
  `rebuild.py::rebuild_database_tracked`, since the plain CLI-only `rebuild_database` itself
  stays untouched). A bulk-export upload is streamed to a per-upload-unique temp path, not the
  CLI's own fixed `extract_root` — that fixed path would collide across two concurrent web
  uploads (never a concern for the CLI's single-operator use).
- **Staleness is a first-class signal, not an afterthought.** `perseverer/staleness.py` checks
  (a) whether `garmin_connect` has succeeded recently — escalating from "warning" to "critical"
  past `PERSEVERER_GARMIN_STALE_ESCALATE_DAYS` (default 7) — and (b) whether
  `athlete.last_full_export_at` is fresh enough (`PERSEVERER_EXPORT_FRESHNESS_DAYS`, default
  90). Both fire a generic JSON webhook (`PERSEVERER_STALENESS_WEBHOOK_URL`) if configured.
  The worker container (`worker/main.py`, APScheduler) runs `garmin_connect` sync + this check
  daily (default 04:15, jittered, resolved against `PERSEVERER_SCHEDULE_TIMEZONE` -- an IANA
  name, default UTC, not the container's own system clock, so a wall-clock schedule like
  "20:55 Pacific" stays correct across DST instead of drifting with a fixed UTC offset);
  `garmin_export` is never scheduled, it's a manual CLI action.
- **Backup + restore automation (Phase 9)**: `perseverer/backup.py`'s `create_backup` snapshots
  the SQLite database via `VACUUM INTO` and rsyncs it plus the raw archive and Parquet trees to
  a second host over SSH (`PERSEVERER_BACKUP_HOST`/`USER`/`PATH`, all-or-nothing optional — skips
  with a log line when unset, same contract as the Eufy credentials), on its own daily worker
  schedule (`PERSEVERER_BACKUP_SCHEDULE_HOUR`/`MINUTE`, default 03:30 UTC) separate from the
  Garmin sync's. `destination`/`source` are a plain string (`user@host:path` or a bare local
  path) throughout — rsync treats both identically, which is what lets the CI
  restore-from-backup job exercise the exact same code against a local temp dir instead of
  needing a real SSH host. `sync backup restore` is deliberately CLI-only, never a Settings-page
  button — unlike `rebuild` (additive, replays the raw archive), a restore overwrites live data
  with an older snapshot, matching `sync auth login`'s own precedent for dangerous,
  human-initiated actions. See `docs/adr/0014-phase-9-backup-hardening.md`.
- **Login brute-force lockout (Phase 9)**: `auth/lockout.py`, DB-backed (not in-memory — `api`
  runs 2 uvicorn workers that don't share process memory, but do share the one SQLite file),
  keyed on username (not source IP, since only the trusted reverse-proxy IP is ever visible
  server-side). `MAX_FAILED_ATTEMPTS` (5) failures in `LOCKOUT_WINDOW` (15 min) locks a username
  out, returning the same generic 401 a wrong password would; `is_locked_out()` runs
  unconditionally before the credential check so a locked-out response costs the same amount of
  work as a wrong-password one, preserving `/auth/login`'s existing constant-time-comparison
  discipline (ADR 0008) rather than reopening that same timing side-channel.
- **Container hardening (Phase 9)**: `api`/`worker` Quadlet units run with
  `ReadOnly=true`/`ReadOnlyTmpfs=true`/`NoNewPrivileges=true` — verified every real write path
  each container has funnels through `/data` first (the bulk-upload endpoint's temp path,
  DuckDB's `temp_directory`, both explicitly pointed under `/data`). `frontend` is deliberately
  NOT hardened this way yet — its entrypoint rewrites `config.js` in place under
  `/usr/share/nginx/html` at every container start (the mechanism that makes the API base URL
  runtime-configurable, ADR 0008), which isn't one of Podman's auto-tmpfs'd paths and shares a
  directory with the real static assets, so read-only support there needs an nginx.conf change
  not yet made. See ADR 0014 decisions 6-7 (including a real historical Quadlet bug where
  `ReadOnly=` silently forced `--read-only-tmpfs=false`, verified fixed in current Podman via its
  own docs before relying on the default).
- **Never drop a field, concretely**: the activity FIT parser (`fit/parser.py`), health FIT
  parser (`health/fit_parser.py`), and health JSON parser (`health/json_parser.py`) all
  register every field they see into `metric_definition`, even ones they don't materialize a
  value for (a field on a known message/object they don't map to a column, or a field of an
  entirely unrecognized message type/nested JSON structure). See each module's docstring for
  exactly which fields get values stored where versus cataloged-only.
- Multi-tenant from day one: every data table carries `athlete_id` except `athlete` and
  `metric_definition` (shared catalogs, not personal data) — enforced by a schema test
  (`tests/db/test_schema.py`), not just convention. A second athlete is a real, exercised path,
  not just schema-level theory: `worker/main.py`'s daily jobs (`run_daily_sync`,
  `run_daily_workout_push`) loop over every row in `athlete` rather than one hardcoded id, each
  athlete gets their own Garmin token-store directory (`config.py::garmin_tokenstore_dir_for`,
  `<data_dir>/garmin_tokens/<athlete_id>/`) and their own optional Eufy credentials
  (`athlete_eufy_config`, one row per athlete — see `adapters/eufy.py::
  resolve_eufy_credentials`, which falls back to the original global `PERSEVERER_EUFY_*` env vars
  only for `DEFAULT_ATHLETE_ID` so that original setup needs no migration), and every ingestion/
  backfill CLI command takes a `--athlete-id` override (mirroring `sync rebuild`'s own
  pre-existing option). `sync athlete create --display-name ...` is what actually provisions a
  new row. See docs/DEPLOY.md's "Provisioning a second athlete" for the operator sequence.

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
uv run sync backup create                # VACUUM INTO snapshot + rsync to PERSEVERER_BACKUP_*
uv run sync backup restore <path>        # CLI-only, human-initiated -- overwrites live data

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
