# ADR 0013: Activity detail/day-view/recap UX pass — map, charts, exports, Strava data completeness

## Status

All four milestones (Strava/GPX/TCX data completeness, activity-detail map/chart polish, PNG/GIF
route exports, day-view animation + recap click-through) are shipped, tested, and verified against
the real archive and the real dev build in a browser — not just unit tests. This work is UX/feature
polish requested ad hoc, not tied to the project brief's phase table, so it does not bump
`AGENTS.md`'s "Current phase" line.

**Milestone A verification**: `sync rebuild` was run against the real production database (real
archive, ~2,270 Strava CSV rows, 483 GPX + 42 TCX + 266 FIT files under `strava_export`) after
fixing two real bugs discovered mid-run (see decisions 2 and 3). Real GPX-sourced activity
`01M025YED9MDX2BMQ9X5GT0CC5` (2025-04-18 run) browser-verified afterward: Pace/GAP chart panels
render (previously blank), Avg/Max HR tiles show real values (134/142 bpm, previously absent),
elevation loss shows (48m), and per-km splits render on the map — all four symptoms the user
originally reported, confirmed fixed against one real activity that exhibited every one of them.

**Milestone B verification**: same real activity, browser-verified — map legend reads
"Faster"/"Slower" with red-fast/blue-slow (previously reversed), CARTO Positron tiles render (no
OSM building/POI clutter), lap bands and the "Avg" expected-pace reference line render on the Pace
chart panel, the "Fastest for this distance" table shows 30 real rows sorted pace-ascending with
the current activity's own row highlighted at its correct rank (25th of 30), and the "Weather"
heading + enlarged icon chip render above a real "Mainly Clear, 14–17°C, 58–65% RH" reading.

**Milestone C verification**: `gif.js`/`@types/gif.js` verified installable and buildable with
this project's React 19/Vite/TS-strict stack (decision 4) before being relied on. Both "Export
image" and "Export GIF" buttons exercised in the browser against the same real activity: the PNG
export completes with no console error; the GIF export's Web Worker asset
(`gif.worker-[hash].js`) is confirmed fetched twice (`workers: 2`) via network-request inspection,
and the button correctly returns from its "Encoding… N%" state to "Export GIF" once finished.

**Milestone D verification**: the day view for 2025-04-18 browser-verified — the activity card now
renders an interactive `ActivityRouteMap` (zoom controls, pace legend) as a sibling of the card's
own link, not nested inside it (decision 5), auto-playing once on mount. The Year 2025 recap view
browser-verified for click-through: every heatmap cell renders as a real `<a href="/day/...">`
link (e.g. `/day/2025-04-18`) against live data, and the personal-records table's rows are
confirmed wired to `/activities/:id` navigation.

**A second real bug found and fixed along the way**: `rebuild.py`'s `_REBUILDABLE_TABLES` list
(written before the `insight` table existed) never included it, so `DELETE FROM activity` during
any rebuild against a database that already had insight rows (i.e. every real rebuild after the
first `refresh_insights` ever ran) hit a live FK violation — caught by running the real rebuild,
not by the test suite, since every existing rebuild test happened to target a fresh, empty
database. Fixed by adding `insight` to the wipe list (child before parent) and covered by a new
regression test that rebuilds *in place* against a database with real insight rows, unlike the
pre-existing tests.

## Decisions

### 1. One root cause behind four "Strava activities are missing data" symptoms

The user's reports of blank Pace/GAP charts, missing avg/max HR, and missing elevation loss on
Strava-imported activities were confirmed (by reading the code, not assumed) to be one bug, not
four: `gpx/parser.py`/`tcx/parser.py` never emitted a per-point `speed_mps` channel (or, for GPX,
even cumulative `distance_m`), and three separate read sites (`routers/activities.py`,
`insights/engine.py`, `ActivityStatsGrid.tsx`) each hardcoded a single `fit.session.*` metric key
for avg/max HR and elevation loss — a key only the FIT parser ever emits, never the GPX/TCX path.
Fixed by (a) deriving `distance_m`/`speed_mps` at parse time via the haversine formula between
consecutive points (GPX) or consecutive `DistanceMeters` deltas (TCX), and (b) overlaying
`activities.csv`'s own real `Average Heart Rate`/`Max Heart Rate`/`Elevation Loss` columns as new,
source-honest `strava.session.*` metric keys, merged with `fit.session.*` at each of the three read
sites via the same alias-priority pattern `api/routers/health.py::LOGICAL_METRICS` already
established (`case()`-based SQL priority in the two backend sites, a small `metricValueAliased`
helper in the frontend).

### 2. `rebuild.py` silently dropped every GPX/TCX/manual-entry Strava raw object

Found while designing the backfill mechanism for decision 1's fix: `rebuild_database`'s replay
loop only dispatched `kind.startswith("fit")` plus three health-JSON kinds — every
`strava_export_gpx`/`strava_export_tcx` raw object fell into the catch-all `else: continue` and
was silently skipped on every `sync rebuild`, a real violation of this project's "raw first, must
be able to re-derive the entire database from the archive" mandate for roughly 500 real
GPX/TCX-sourced activities. A second, subtler instance of the same gap: a manual-entry (no
backing file) CSV row has **no raw_object of its own** distinguishable from its own
`strava_export_csv_row` sibling — `archive_raw_bytes` is idempotent purely on `(athlete_id,
sha256)`, not `kind`, so `_ingest_manual_entry`'s own archive call (identical bytes to the CSV row
already archived moments earlier in the same import) always collides and returns the existing
`strava_export_csv_row`-kinded id rather than creating a second row. Confirmed against the real
archive: zero `strava_export_manual_entry`-kind rows exist despite manual-entry activities being
present in the database. Fixed by adding `strava_export_gpx`/`strava_export_tcx` branches to the
replay dispatch, and by having the `strava_export_csv_row` branch itself detect and ingest a
file-less row (via its own `Filename` field) rather than waiting for a `strava_export_manual_entry`
branch that can never fire. The overlay/ingest logic itself was factored out of
`strava_export.py`'s live-import functions (`ingest_geometry_content`, `ingest_manual_entry_content`,
`csv_row_from_raw_json`) so the replay path reuses the exact same code, not a second
implementation.

### 3. `insight` missing from `rebuild.py`'s wipe list — a second, independently-discovered FK bug

Documented separately in Status above; noted here because it's a second real, previously-unknown
`rebuild.py` gap found in the same session, both stemming from the same root cause (the table/kind
lists in `rebuild.py` aren't mechanically kept in sync with schema/adapter additions — every prior
instance of this pattern, `garmin_export_health_json` in Phase 6 and this one, was found by
actually running a rebuild against real, non-empty state, not by the test suite alone).

### 4. `gif.js` verified before being relied on, matching this project's standing discipline

`gif.js` (encoder) + `@types/gif.js` (community types, since the package ships none) were chosen
over hand-rolling frame-to-GIF encoding. Verified, not assumed: `npm install` completed clean;
`npm run typecheck` passed with `import GIF from "gif.js"` (an `export =`-style CommonJS module) as
well as `import gifWorkerUrl from "gif.js/dist/gif.worker.js?url"` (Vite's `?url` suffix, resolved
via the project's existing `vite/client` type reference); and — the one check that actually
exercises the packaging, since an unused module is tree-shaken away — after wiring a real "Export
GIF" button, `npm run build` emits `gif.worker-[hash].js` and `routeGif-[hash].js` as their own
dynamically-imported chunks, confirming the worker asset resolves to a real fingerprinted URL at
build time rather than 404ing at runtime. The GIF export is dynamically imported
(`await import("../routeGif")`) specifically so gif.js's own weight and its worker chunk are never
part of the main bundle — only fetched once a user actually clicks "Export GIF".

### 5. Day view's animated route map lives outside the activity card's own link

`ActivityCard`'s whole surface was previously one `<Link>` to the activity detail page, safe only
because the static `ActivityMap` thumbnail inside it has every interaction explicitly disabled
(`dragging`/`scrollWheelZoom`/`zoomControl` all `false`) for exactly this reason. The day view's
new animated map reuses `ActivityRouteMap` as-is — fully interactive by design (zoom, drag,
playback) — so embedding it inside the same `<a>` would be a real click-handling hazard (a map
drag or a zoom-button click either navigating away or fighting the browser's own anchor-click
default action), not just a style nit. `ActivityCard` was restructured so the header/stats section
is its own `<Link className="activity-card__link">` and the optional `animatedRoute` slot (day
view only; `ActivityListPage` still passes only `encodedPolyline` and gets the static thumbnail)
renders as a sibling within the same outer `.activity-card` div. `ActivityRoute.tsx`'s own
`buildRouteData`/`ANIMATION_DURATION_MS` are exported and reused by the new
`DayViewActivityRoute.tsx` rather than reimplemented, so the day view's auto-play-once animation
runs at the exact same pace as the activity detail page's manual player.

### 6. Recap click-through: PR table/scatter points → activity, heatmap cells → day/week

Per the day-vs-activity distinction already established for `ActivityContextStrip`'s `recent` vs.
Milestone B's `fastest` table: a personal-record row and a "pace vs. distance" scatter point each
name one exact activity, so both navigate to `/activities/:id` (`PersonalRecord` gained an
`activityId` field, threaded from `personalRecords()`'s already-available `best.id`; the scatter
charts in `RunningStats.tsx` and `WeekRunningStats.tsx` gained an `id` field on each point plus an
`onClick` reading `point.payload.id`). A heatmap cell represents a whole day (or, in the all-time
view's week-grain rows, a whole week), not one activity, so those instead link to the already-
existing `/day/:date` or `/calendar/week/:date` routes — implemented by converting
`RunningStats.tsx`'s heatmap cells from bare `<span>`s to `wouter` `<Link>`s carrying the same
visual classes, rather than adding a second onClick-based navigation mechanism alongside the PR
table's.

## Deliberately out of scope (judgment calls made explicit, not asked about twice)

- **"Segments on top of the graph"** was clarified via the user (no strong preference given) to
  mean the activity's own laps as alternating background bands (`ReferenceArea`), not Strava's
  separate crowd-sourced Segments feature — that would need a whole new external-API integration
  this project has no scope for.
- **"Expected pace"** was clarified the same way to mean a flat reference line at the activity's
  own overall average pace (`ReferenceLine`), not a per-lap or route-planned target this project
  has no data model for.
- **Non-running-scatter click-through** (the bar/line "distance per day/month" charts,
  `ActivityContextStrip`'s own scatter) was left untouched — those are pre-existing aggregate/
  day-grain views already served by the heatmap's own day-link precedent, and expanding
  click-through to every chart type in the app was judged to be scope creep beyond "table or
  graph" as the user's request was reasonably read.
- **PNG/GIF export stats block styling** is minimal (three text lines, no themed card chrome) —
  intentionally simple since the artifact is meant to be laid over the user's own photo/background
  of choice, not to look like an in-app card.
