# ADR 0009: Phase 6 — calendar grid, weekly/monthly rollups, Fitness & Form, health dashboard

## Status

Accepted. Grounded in the real ~1250-activity/4-year database throughout — every alias list,
every "does this data exist" question in this ADR was answered by direct SQL against
`data/perseverer.db`, not assumed from parser docstrings, per AGENTS.md's standing rule for
verifying rather than recalling.

## Context

Phase 5 delivered a "core dashboard" at daily grain only: a flat day-by-day calendar list,
activity list/detail with a stream chart, notes. The user scoped Phase 6 (the phase table lives
outside this repo, confirmed via question this session): a real year/month/week calendar grid,
weekly/monthly rollups that must reconcile exactly against Garmin Connect's own displayed
numbers (week starts **Monday**, confirmed against the user's account), a "Fitness & Form" view
computing an **independent** CTL/ATL/TSB model and comparing it against Garmin's own
precomputed training-status signals, and a health dashboard covering core daily summary, sleep,
and HRV/SpO2/stress.

Two research passes grounded every decision below: an Explore-agent survey of the real database,
and direct `sqlite3` queries run against it. Key findings that shaped this phase:

- Garmin's raw exports have **no CTL/ATL/TSB triplet at all** — only per-activity
  `fit.session.training_load_peak` (98% of 1250 activities) and Garmin's own aggregate signals
  (`TrainingReadinessDTO.score`/`.acuteLoad`, `TrainingHistory.trainingStatus`/
  `.fitnessLevelTrend`, `MetricsMaxMetData.vo2MaxValue`, `RunRacePredictions.*`). This is why
  "independently computed, compared against Garmin" was the right call — there is nothing to
  reconcile against directly.
- The same logical daily field lives under up to three `metric_key` namespaces depending on
  source/era (confirmed by direct query, not parser-docstring assumption) — see decision 5.
- `sync rebuild` silently skipped replaying `garmin_export_health_json` raw objects (flagged
  back in Phase 3's ADR 0006 Consequences, never fixed). Since this phase leans heavily on
  exactly that data, the user chose to fix it now rather than defer again — see decision 1.

## Decisions

### 1. Fixed `rebuild.py`'s `garmin_export_health_json` replay gap

`rebuild_database`'s dispatch had no branch for this raw-object kind — it fell through to the
catch-all `else: continue`, meaning a rebuild silently dropped every `garmin.export.*` health
observation (Training Readiness, VO2max, race predictions, daily summaries) with no error,
recoverable only via a manual `sync import garmin-export` re-run. Fixed by re-deriving
`report_kind` from `raw_object.source_locator` (the original export filename, already archived)
via `garmin_export.py`'s existing `report_kind_from_filename` helper — promoted from private to
a shared, public function since it's now used by two call sites. Verified two ways: a new
`tests/test_rebuild.py` case (seed → import → rebuild into a fresh DB → assert observations
match), and a real `sync rebuild` run against the host's populated database confirming
`garmin.export.*` row counts are preserved (241,146 rows across 288,939 total observations
before the fix — see Consequences for the post-rebuild verification numbers).

### 2. Weekly/monthly rollups: `period_rollup` + `health_metric_period_rollup`, a `period_type`-discriminated pair

Two new tables (not four separate week/month tables mirroring `day_rollup`/
`health_metric_daily_rollup` exactly) — the same shape at two grains, one refresh function
parameterized by `period_type`, one index for range queries across both. This is a deliberate
style deviation from `day_rollup`'s single-grain precedent, justified because week and month are
genuinely the same shape at two granularities, not two different concepts.

Computed as a **rollup of `day_rollup`/`health_metric_daily_rollup`, not of raw tables**:
sum-of-sums for fixed-column activity/sleep fields, `value_avg = value_sum / n_observations`
(weighted by each day's own observation count, not a naive average-of-daily-averages — a real
correctness bug the sum-of-sums approach makes explicit rather than accidental), `value_last`
from the day with the latest `local_date` among days with data. This reuses the daily grain's
already-solved judgment calls (longest-sleep-session-wins, etc.) and guarantees week/month
totals are structurally consistent with the day rows shown next to them in the calendar grid.

Every ingest entry point's existing `for local_date in touched_dates: refresh_daily_rollup(...)`
loop is replaced by a new shared helper, `rollups.refresh_daily_and_period_rollups`, which
refreshes each touched date's daily rollup, then derives the distinct `(period_type,
period_start)` pairs those dates fall in and refreshes each **once** — never once per touched
date, which would recompute the same week/month repeatedly. A retroactive correction to an
already-rolled-up day (a `garmin_connect` re-sync revising 3-month-old data) automatically
refreshes its containing week/month with no watermark or "closed period" concept needed — it
falls out of the existing accumulate-then-refresh contract for free (verified with a dedicated
test: seed a day, refresh, add a "correcting" second activity to the same day, refresh again,
assert the week total reflects the correction).

### 3. Fitness & Form: an independently-computed Coggan/Banister EWMA, full history recompute

- **Input**: `fit.session.training_load_peak` summed per `local_date` from `activity_metric`,
  zero on rest days. Two dedup rules were needed, both new (no existing precedent to copy):
  same-day multiple activities sum (mirrors `day_rollup`'s existing precedent exactly); the same
  activity ingested via two sources (a real risk — `activity_metric`'s unique constraint
  includes `source`) prefers the row whose `source` matches `activity.primary_source`, falling
  back to `MAX(value_num)` across whatever sources exist if the primary source lacks the metric
  for that activity.
- **Recurrence** (standard Coggan model): `CTL_t = CTL_(t-1) + (load_t - CTL_(t-1)) * (1 -
  exp(-1/42))`, `ATL_t` the same with `/7`, `TSB_t = CTL_(t-1) - ATL_(t-1)` (form entering day
  t, before that day's session — positive = fresh, negative = fatigued). `CTL = ATL = 0` at the
  series' first date; standard cold-start behavior, not engineered around.
- **Full recompute of the entire history on every relevant ingest run, not incremental**:
  ~1,400+ days is one bulk query plus a pure-Python linear pass — cheap even on the Goldmont
  Celeron, since the platform's "never aggregate at request time" constraint is about serving,
  not bounded ingest-time computation. A retroactive correction to old training-load data
  invalidates every subsequent day's EWMA forward from that point regardless of scheme, so an
  incremental "touched dates forward" approach wouldn't even save asymptotic work — only adds a
  watermark failure mode for no measurable win.
- **The series extends through today's local date**, not just the latest activity date, so TSB
  doesn't look artificially stale after a rest week. `garmin_connect`'s daily scheduled sync
  calls `refresh_fitness_rollup` **unconditionally** (even when `touched_dates` is empty) —
  the only ingest entry point that does, since it's the one that runs daily regardless of new
  data. `fit_folder`/`garmin_export`/`rebuild` call it only when `touched_dates` is non-empty.
- New table `fitness_daily_rollup` stores the deduplicated daily `training_load` input
  alongside the derived `ctl`/`atl`/`tsb` — raw-first/provenance instinct: the model is
  debuggable without re-deriving it.

### 4. Frontend "compared against Garmin" scoping

Since Garmin has no CTL/ATL/TSB at all, "compared against" necessarily means side-by-side
display of conceptually-adjacent-but-different signals, not a shared-axis overlay:
`TrainingReadinessDTO.score` as a secondary readout and `TrainingHistory.trainingStatus` as a
per-date label list — both read from the **existing** `/health/observations` endpoint (no new
backend endpoint needed; this is a handful of point-in-time values over a chart-sized range, not
an aggregation). The chart itself (`FitnessChart.tsx`) extends `StreamChart.tsx`'s exact
hand-rolled-SVG pattern (CTL/ATL sharing one axis, TSB on its own axis with a zero-line) —
justified again, not just defaulted: bounded point count, no zoom/pan/tooltip need, matching
ADR 0008's established lean-dependency call for the one prior chart.

### 5. Health dashboard: a hardcoded `LOGICAL_METRICS` alias-merge, verified field-by-field against real data

The same logical field (steps, resting heart rate, VO2max, ...) lives under up to three raw
`metric_key` namespaces depending on source and era — canonical FIT-derived, Connect-shaped JSON
via `fit_folder`, GDPR export JSON. Every alias list in `api/routers/health.py::LOGICAL_METRICS`
was confirmed by direct query against the real `metric_definition` table, not assumed from the
camelCase patterns visible in parser docstrings — this surfaced real traps that would have been
wrong by pattern-matching alone: `garmin.daily_summary.lastSevenDaysAvgRestingHeartRate` is a
distinct trailing-average metric, not an alias of the daily value; `.currentDayRestingHeartRate`
is a same-day-so-far variant, kept separate; VO2max turned out to have **three** real sources
(`fit.max_met_data.vo2_max`, `garmin.export.MetricsMaxMetData.vo2MaxValue` — the dense one,
treated as primary — and the sparse `garmin.export.ActivityVo2Max.vo2MaxValue`), not the two
originally hypothesized.

The merge itself is a small presentation-layer pick — first alias (in priority order) with data
for a given day wins — over already-rollup-backed `health_metric_daily_rollup` reads, **not** a
schema/rollup change (that would re-litigate ADR 0006 decision 1's explicit no-hardcoded-
metric-map rationale). `last_observed` is computed across all aliases, unbounded by the
requested date range, so the frontend can show "last observed: {date}" even when the visible
range has no data — necessary because `garmin_connect`'s daily incremental sync only ever
downloads activity FIT files (confirmed by reading `adapters/garmin_connect.py`), so daily
wellness data is only as fresh as the last manual `garmin_export` re-run, potentially stale for
months even while activities stay current. Sleep is deliberately **not** part of
`LOGICAL_METRICS` — `sleep_session` is its own dedicated table with its own endpoint
(`GET /sleep`, built in Phase 5), not part of the `health_observation` EAV metrics; the health
dashboard's Sleep section reads it directly rather than duplicating it through the alias-merge
machinery built for a different data shape.

### 6. Calendar grid: three views, client-side gap-fill, no backend shape change to the existing endpoint

Year view (12 month tiles from the new `GET /calendar/months`) → month view (Monday-first grid,
day cells from the **existing** `GET /calendar` endpoint, gap-filled client-side since a month
is at most 31 rows — cheap, and keeps `GET /calendar`'s sparse contract unchanged for its other
callers, the MCP tool built on it in Phase 4) → week view (7-column grid, closer to Phase 5's
old list density). Phase 5's in-place `NotesPanel` expand-on-click behavior is kept for day
cells rather than replaced for its own sake — it already worked. `GET /calendar/weeks`/`/months`
follow the exact per-endpoint `Depends(require_api_key)` pattern established in Phase 5, reusing
`_get_period_calendar` as shared logic between the two thin route handlers.

### 7. Introduced Vitest + React Testing Library

Phase 5 shipped frontend-test-free (manual browser verification only). This phase adds real
client-side logic worth automated coverage — calendar month-grid gap-fill (`dateUtils.ts`, pure
functions, directly unit-tested), `FitnessChart`'s axis-scaling math (component-rendered via
RTL, asserting path structure and summary text), and the health-dashboard alias-display
selection logic (`MetricSection`, exported from `HealthPage.tsx` specifically to be testable in
isolation from the page's data-fetching). User's explicit choice this session, weighed against
staying manual-only as Phase 5 did.

### 8. Post-ship reconciliation fix: `activity.local_date` is offset-adjusted, not a raw UTC date

The "reconcile exactly against Garmin Connect" acceptance criterion (see Context) wasn't fully
met at first ship — verified directly against the user's real intervals.icu account (open,
authenticated, in the user's own Chrome; used read-only to cross-check two real weeks), which
surfaced two distinct discrepancies:

- **Days-active undercounted by exactly 1** in both spot-checked weeks. Root cause: `local_date`
  was the raw UTC calendar date of `start_time_utc` (a Phase 1 decision, ADR 0002 decision 10,
  reasoned as "SQLite/SQLAlchemy don't round-trip `tzinfo`, so don't pretend they do"). This
  athlete's activities carry `utc_offset_s = -25200` (UTC−7); an evening session (e.g. a
  19:30-local yoga flow) has a `start_time_utc` that's already past UTC midnight, so it landed on
  the *next* UTC calendar day — one day later than the athlete (and Garmin Connect/intervals.icu,
  which both bucket by true local time) would call it. Confirmed activity-by-activity: an
  intervals.icu "Flow" session shown on a Monday matched, minute-for-minute, an activity this
  system had stored under the following Tuesday. Fixed by computing `local_date` as
  `(start_time_utc + utc_offset_s).date()` instead of `start_time_utc.date()`
  (`adapters/fit_folder.py::_local_date`, the one shared ingest function every adapter and
  `rebuild.py` already route through) — `start_time_utc` itself is untouched, still the raw,
  naive-UTC instant; only this derived column changed. Backfilled the real database directly
  (`utc_offset_s` was already correctly stored per activity, so this was a pure
  `UPDATE ... SET local_date = date(start_time_utc, utc_offset_s || ' seconds')`, no FIT
  re-parsing needed) — 388 of 1250 activities' `local_date` changed, confirming this wasn't a
  two-week edge case but a systemic ~31% mis-bucketing for this non-UTC athlete. Every
  `day_rollup`/`period_rollup`/`fitness_daily_rollup` row touched by a changed date was refreshed
  via the existing `refresh_daily_rollup`/`refresh_period_rollup`/`refresh_fitness_rollup`
  functions (563 distinct dates, 185 periods) — no schema change, no new migration.
- **Weekly duration overcounted by ~25-27 minutes** in both weeks. Not a bug: `period_rollup`
  summed `activity_duration_s` (elapsed/total time), while intervals.icu's headline "Total"
  figure is moving time. Re-summing this system's own already-stored `activity_moving_duration_s`
  for the same activities landed within a minute of intervals.icu's displayed total for one week,
  and within ~20 minutes for the other (traced further to two hikes where intervals.icu's own
  GPS-noise-filtered moving-time recompute is stricter than Garmin's device-reported value — a
  known third-party recomputation difference, not missing or wrong data on this side). User chose
  to switch the calendar/week UI's duration display to `activity_moving_duration_s` to match what
  Garmin Connect/intervals.icu show by default; both fields remain stored regardless.

**Known remaining inconsistency, not fixed here**: `health_observation`/`sleep_session.local_date`
are still the raw UTC calendar date. Activity FIT files carry a reliable per-file offset
(`activity_mesgs.local_timestamp` vs `.timestamp`); health FIT files' offset story is messier —
confirmed by direct introspection that `monitoring_info_mesgs`/`timestamp_correlation_mesgs`
*sometimes* carry the same `local_timestamp` trick, but not every health FIT message type was
checked, and JSON-sourced health data already carries Garmin's own pre-computed local calendar
date (a different, likely-already-correct mechanism). Making health data's `local_date`
consistent with the activity fix is a real follow-up, not a one-line change — it needs the same
kind of message-type-by-message-type verification this fix required for activities, not an
assumption that the same helper applies uniformly. Until then, a `day_rollup` row's activity data
and its health data may reference boundaries that differ by up to `utc_offset_s` for non-UTC
athletes.

## Consequences

- `sync rebuild` is now the correct general-purpose backfill path for `garmin_export_health_json`
  data — ADR 0006's Consequences section flagged the opposite (avoid `sync rebuild`, use a
  direct backfill) as a workaround for exactly this gap; that workaround is no longer necessary
  going forward, though it correctly described the state of the world at the time it was written.
- `period_rollup`/`health_metric_period_rollup`'s discriminated-pair shape is the first
  `period_type` column in this project's rollup tables — if a third grain is ever needed
  (quarterly? rolling-N-day?), it likely extends this same pair rather than adding a new table,
  but that's a call for whenever it's actually needed, not decided here.
- The `LOGICAL_METRICS` alias list is real, verified data as of this session — but is a living
  list, not a closed one. A future Garmin export format change or a new source (a hypothetical
  Strava adapter) could introduce a fourth namespace for an existing logical metric, or a
  logical metric this list doesn't yet cover. It should be revisited the same way it was built —
  direct query against the real catalog — not assumed to be complete.
- Fitness & Form's CTL/ATL/TSB is explicitly **not** validated against Garmin's own numbers
  (there's nothing to validate against) — its correctness rests on the recurrence matching the
  standard, well-established Coggan model exactly (verified via a hand-computed test sequence),
  not on agreement with any external source. This should be stated plainly in the frontend UI
  too, not just this ADR, so a user doesn't mistake it for a Garmin-sourced number.
