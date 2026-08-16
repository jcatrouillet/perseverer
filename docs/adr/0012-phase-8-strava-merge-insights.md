# ADR 0012: Phase 8 — strava_export importer, merge visibility/split, rules-based insight engine

## Status

All four milestones (strava_export adapter, merge visibility/split, insight engine backend,
InsightsPage frontend) are shipped and verified against the real archive, not just unit tests.
The phase's actual shape ended up narrower than the brief's own wording suggested — see decision
1 — because the merge-matching engine built in Phase 1 turned out to already be source-agnostic.

**Milestone A verification**: the real Strava export archive (2256 activity files, 2336 CSV
rows) was imported in full: 2268 of 2270 non-empty rows succeeded (2 manual-entry rows had an
unparseable date and were skipped, not guessed), zero errors on the clean run. **1245 activities
now have more than one distinct source linked** — the literal acceptance criterion — out of 1874
total activities (down from what would be ~3500 if fit_folder/garmin_export/strava_export never
deduplicated). One real bug was found and fixed mid-import: a minority of older (2020-era)
Strava-exported GPX/TCX files have literal whitespace before their `<?xml ...?>` declaration,
which Python's expat parser rejects outright — fixed with `content.lstrip()` in both new parsers,
confirmed against the actual failing file before shipping the fix, with a regression test.

**Milestone B verification**: `GET/POST .../sources[/split]` exercised in the browser against a
real merged activity (`01KZG784HKD6B5B0MWDYF31PVJ`, `garmin_export` + `strava_export`) — the
sources list, the real merge-decision reasons ("start_time delta 0s <= 180s; sport family 'run'
vs 'run': match; duration delta 0s <= allowed 188s"), and the split confirm/cancel flow all
confirmed via DOM inspection against the live dev build. One real bug found by the endpoint's own
tests before shipping: `insert_new_activity` (extracted from `ingest_canonical_batch`) didn't
register extra-metric keys into `metric_definition` before inserting `activity_metric` rows,
which is fine when called from the normal ingest path (registration already happened via the
caller) but a real FK failure when called standalone from the split endpoint — fixed by moving
the registration into `insert_new_activity` itself, idempotent either way.

**Milestone C verification**: `sync refresh-insights` run against the real database computed 275
real insights. One cross-checked independently: "Longest distance (run), 30d" (26,015.47 m,
`01KZG9MPWPB8RMEG3JAYWNGRY1`, 2026-08-02) matches a manual `SELECT ... ORDER BY distance_m DESC
LIMIT 1` exactly. Zero `load`/`health` insights fired on the real data as of this writing —
confirmed as a correct "nothing to report" outcome, not a bug: TSB is currently positive
(well-rested), and neither resting-HR nor sleep-score data has a reading for "today" in the real
archive (last ingested 2026-08-02/03), so both rules correctly refuse to report a signal without
a fresh reading rather than fabricate one against stale data.

**Milestone D verification**: `InsightsPage` browser-verified against the real backend — the
default 30-day window shows real streak/effort insights with working activity links; switching
the window selector to 180 days correctly reveals the "Personal bests" section (absent at 30d
because no all-time PB happens to fall in the last 30 days right now), matching the real
`pb`-kind rows already confirmed via direct query.

## Decisions

### 1. The phase's actual scope, and why it's narrower than it first read

The project brief describes Phase 8 as "`strava_export` importer, merge conflict UI,
rules-based insight engine." Before writing any code, `merge/engine.py` (built in Phase 1) was
read in full: `is_same_activity()` already compares only start time, sport family (via a
vendor-string → family map), and duration — nothing source-specific — and it's already wired
into the one shared ingest path every adapter funnels through (`fit_folder.py::
_find_merge_match` → `ingest_canonical_batch`), with every match/no-match decision already
logged to a `merge_decision` table with human-readable reasons. Cross-source deduplication
therefore worked automatically the moment `strava_export` called that same shared path — no new
matching logic was needed, and this was proven, not assumed: a dedicated test seeds a
`fit_folder` activity and confirms a same-real-world-event Strava import links into the same
`activity` row (`tests/adapters/test_strava_export.py::
test_strava_activity_merges_into_existing_activity_from_another_source`), and the real 1245
cross-source activities above confirm it at scale.

What was actually new: the Strava adapter itself (zero prior code existed), a way to *see* and
*undo* merges (nothing like this existed — merges were previously silent and permanent), and the
insight engine (entirely new).

### 2. Strava export archive shape (confirmed against a real archive, not docs)

`activities.csv` (one row per activity, Strava's own numeric `Activity ID` as the stable join
key) plus `activities/<file>`, one of: `.fit`/`.fit.gz` (1269 of 2256 files, 56% — literally the
same Garmin FIT bytes already reachable via `garmin_export`/`garmin_connect` when Garmin
auto-uploads to Strava, confirmed by decoding a sample: a real `fr955` device, real serial
number, real session data), `.gpx`/`.gpx.gz` (888 files, 39% — bare `trkpt` lat/lon/ele/time,
some carrying Garmin's own `gpxtpx:TrackPointExtension` for heart rate when a Garmin device
produced the file), or `.tcx.gz` (99 files, 4% — real lap structure, HR, device-dependent
altitude, e.g. absent for a real Polar BEAT sample with no barometric altimeter). ~0.6% of rows
have no file at all (manually logged activities) and are reconstructed from the CSV row alone.

A real, non-obvious quirk found by comparing rows: the number embedded in a *compressed* file's
name (e.g. `activities/20702145836.fit.gz`) is **not** the same as the CSV's own `Activity ID`
(e.g. `19576832815`) — a different internal id. Only uncompressed files' filename stem happens to
equal `Activity ID`. External IDs are therefore always taken from the CSV's `Activity ID` column,
never derived from a filename, avoiding a real would-be idempotency bug.

`activities.csv` itself has a real quirk: five column names (`Elapsed Time`, `Distance`, `Max
Heart Rate`, `Relative Effort`, `Commute`) each appear twice — a "summary" block and a more
precise "detail" block. Every real duplicate observed happens to want the *last* occurrence
(Python's own `dict(zip(header, values))` behavior), which is what the few materialized fields
use — but the raw row is archived *positionally* (a list of `[header, value]` pairs, not a dict)
so the duplication itself is never lost to the raw archive, honoring "never drop a field" even
for a column-naming quirk this specific.

`_STRAVA_SPORT_MAP` (Strava's `Activity Type` → this project's `(sport, sub_sport)` vocabulary,
which is actually the Garmin FIT SDK's own enum strings) was built by decoding a real FIT file of
each Strava-CSV type from the same real archive and reading its real `sport`/`sub_sport` —
e.g. confirming Strava's "Yoga" maps to FIT's `sport="training", sub_sport="yoga"`, not a
guessed `sport="yoga"`. Getting this right is what makes cross-source sport-family matching
actually recognize a GPX/TCX-only Strava yoga session as the same activity as its Garmin-FIT twin.

### 3. Split endpoint: non-destructive by construction, not by convention

`POST /activities/{id}/sources/{link_id}/split` re-parses that one source's already-archived raw
bytes (via a new `reparse.py`, dispatching on `raw_object.kind`) and calls a new
`insert_new_activity` helper — extracted out of `ingest_canonical_batch`'s own "no match found"
branch, so the exact same insert logic (activity/metrics/laps/splits/route/stream) is reused
rather than duplicated, and deliberately does **not** re-run merge-matching (which could just
merge the split-off activity right back into where it came from). GPX/TCX-sourced activities
carry no session totals of their own (see `gpx/parser.py`/`tcx/parser.py`'s own docstrings), so a
faithful split re-fetches that row's sibling `strava_export_csv_row` raw object and re-applies
the same CSV-totals overlay the original ingest used. The original activity's other sources are
untouched; nothing is ever deleted.

### 4. `insight` table: full delete-and-reinsert, dual refresh trigger

Same precedent as `fitness_daily_rollup`'s full CTL/ATL/TSB recompute: cheap at this data volume
(275 real rows), and avoids stale rows silently lingering after a rule or threshold changes.
Refreshed from all five ingest entry points (matching every other rollup) *and* unconditionally
from `garmin_connect.py`'s own daily-scheduled sync — not a new APScheduler job. That function
already runs once/day via the worker regardless of whether anything new was found (confirmed by
reading `worker/main.py`), which is exactly the "must keep advancing even with zero new
ingests" property a window like "last 30 days" needs, and `refresh_fitness_rollup` already
established the same unconditional-call precedent there for the same reason.

### 5. Load/recovery and health-anomaly thresholds (proposed defaults, not validated)

Per the user's own instruction to ask rather than guess on threshold-like decisions: these are
implemented as clearly-labeled, easily-adjustable constants, not derived from any
this-athlete-specific validation this project has a way to perform.

- `TSB_SUSTAINED_LOW_THRESHOLD = -20.0` for `>= 5` consecutive days (Coggan's commonly-cited
  "high risk of overreaching" line), flagged only if the streak ends within the last 14 days.
- `LOAD_JUMP_PCT_THRESHOLD = 0.30` — a week-over-week `training_load` increase of 30%+.
- `RESTING_HR_ELEVATED_PCT = 0.10` / `SLEEP_SCORE_DROP_PCT = 0.15` vs. a trailing 30-day
  baseline (minimum 7 observations), only evaluated when there's an actual reading for "today"
  (never fabricated from a stale baseline alone).

### 6. PB insights: a Python port, not a shared implementation

`insights/rules_pb.py` re-implements `frontend/src/runningStats.ts::personalRecords`'s band
logic (`STANDARD_DISTANCES`, a 0.9×–1.3× tolerance band, fastest-effective-pace-in-band wins)
rather than sharing code across the Python/TypeScript boundary — the acceptance criterion
("insights are deterministic and unit-tested against fixture activities") needs a real backend
implementation regardless, and the existing frontend version is left untouched (a later dedup
opportunity, not committed scope here).

## Deliberately out of scope (stretch items, not committed)

- **PB proximity percentage** for a window's own best when it *isn't* the all-time best (only
  "still the all-time best" is surfaced; "how close" was cut to keep Milestone C bounded).
- **Non-running cadence** (`rules_efforts.py`'s cadence dimension only covers foot sports via
  `fit.session.avg_running_cadence`; a cyclist's cadence field was not unified in).
- **A dedicated manual-entry (no-file) split path's CSV-date ambiguity**: the ~0.6% of Strava
  rows with no backing file use `activities.csv`'s own `Activity Date` text field as a
  naive-UTC fallback timestamp — its real timezone is undocumented by Strava and wasn't
  independently verifiable, a small, explicitly-flagged limitation (see `strava_export.py::
  _parse_activity_date`'s own docstring).
