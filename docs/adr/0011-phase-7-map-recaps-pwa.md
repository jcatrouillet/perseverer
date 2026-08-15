# ADR 0011: Phase 7 — map explorer, recaps, PWA/offline shell

## Status

All three milestones (map explorer, recaps, PWA shell) are shipped. Three scope decisions in
this ADR are deliberate, user-confirmed deviations from the original project brief's Phase 7
description, made with the brief's own reasoning in view, not by oversight — see decision 1.
Milestone B additionally dropped one of its own three planned ingredients (image export) after
verification showed the chosen library doesn't work on this app's real pages — see decision 5.
Milestone C caught and fixed a real bug before shipping (precaching `config.js` would have
broken the runtime API-base-URL mechanism across redeploys) — see decision 6 — and has one
honest verification gap: live service-worker registration could not be exercised in this
session's sandboxed browser tool (confirmed as a tool limitation, not an app bug — see Status
below and decision 6).

Milestone A verification: `GET /activities/map` returns the real archive's 904 GPS-bearing
activities (one direct join, no pagination) in ~0.22s measured end-to-end via `curl`; the
frontend additionally filters out 14 of those with a pre-1995 GPS-week-rollover clock glitch
(the same `EARLIEST_PLAUSIBLE_DATE` rule every other all-history view already applies), leaving
890 real points. Browser-verified: all 890 markers present within ~114ms of the map mounting,
the sport filter narrows to exactly 624 for "running" (matching a direct DB count), and clicking
a real marker's popup link navigates to that activity's real detail page.

Milestone B verification: the PBs-set cross-reference (`newAllTimePrs()`) was checked against
the real archive on both sides — May 2026 and 2026-year-to-date both correctly badge "20 km,
Half marathon" (the athlete's real 2026-05-31 all-time PRs, confirmed against the all-time
records table), while August 2026 correctly shows no badge despite having its own in-period
"personal records" table (a genuinely slower half-marathon that isn't the all-time best). The
same cross-reference was verified at Week and Day grain too (week-of-2026-05-25 and
2026-05-31 both badge correctly; 2026-08-02, a real but non-record run, correctly shows
nothing). Year-over-year deltas were checked against a direct API call: 2026 vs. 2025 showed
"vs 2025 -929km" against an independently fetched 2025 total of 2279.26km (1350 - 2279 = -929,
exact); July 2026 vs. July 2025 showed "vs Jul 2025 +5km" against an independently fetched July
2025 total of 213.49km (219 - 213.49 ≈ +5.5, rounds to +5). 184 backend tests / ruff / mypy
clean throughout (Milestone B is frontend-only); 179 frontend tests / typecheck / build clean.

Milestone C verification: the generated `dist/manifest.webmanifest` was fetched and checked
directly (name/short_name/description/start_url/display/theme_color/background_color/icons all
present and correct) and all three icon files were confirmed to load in a real browser context
at their declared pixel dimensions (192×192, 512×512, 512×512). The generated service-worker
precache manifest was inspected directly in `dist/sw.js` and confirmed to list exactly the app
shell files (JS bundle, CSS, `index.html`, icons, manifest) and, after the decision-6 fix,
explicitly *not* `config.js`. What could **not** be verified in this session: actual service
worker *registration* — `navigator.serviceWorker.register()` failed with "an unknown error
occurred when fetching the script" in the sandboxed browser tool used throughout this project's
verification work, for both the real `sw.js` and a trivial one-line throwaway service worker
used specifically to isolate the cause — confirming this is a limitation of that tool's network
proxying, not a defect in the generated service worker or manifest (regular `fetch()` of the
identical URL succeeds; only the browser's internal SW-registration fetch path fails). This
matches a limitation already on record for this tool (screenshots of the Browser pane are also
unreliable — see the Phase 6.1 ADR's verification notes). Real install/registration behavior on
an actual device is still owed before this milestone's "installability verified on a real
mobile browser" bar is fully met — tracked as a real, open gap, not silently waved through.

## Context

The project brief (kept outside this repo per `CLAUDE.md`) scopes Phase 7 as:

> **7** | Map explorer, recaps, PWA/offline | Full-history start-point map renders in under 1s

and its §8 frontend section describes the two views in more detail than the one-line table
entry:

> **6. Map explorer** — all start points clustered on one map, plus an all-routes heatmap.
> Filter by sport and date range, click through to the activity. **Privacy zones are mandatory
> here, not optional** — this endpoint is publicly reachable and a decade of start points draws
> a bright arrow at my front door. Configurable home/work radii, applied **server-side** by
> trimming route geometry and fuzzing start points before the data leaves the API. Never
> implement this as client-side masking of data you already sent.
>
> **7. Recaps** — week / month / year. Totals, sport split, elevation, PBs set, streaks,
> consistency, year-over-year deltas, and a "best of" reel. Exportable as an image.

and recommends, for the map specifically:

> **Map:** MapLibre GL JS with a self-hosted Protomaps `.pmtiles` basemap on the NAS. No API
> key, no bill, works offline, and my home location and route data never leave the NAS.

and for offline support, in the performance budget:

> ...service worker for offline reads of recent activities.

**Real-data inventory** (confirmed against `data/sporthealth.db`, not assumed): 904 of 1250
activities have real GPS route data (`route_geom` rows with a start point, bounding box, and
encoded polyline) — running (624), walking (200), hiking (50), cycling (22), alpine skiing (6),
snowshoeing (2). The rest are indoor/no-GPS activities (strength training, indoor yoga, etc.)
that correctly have none. Date range spans 1989-12-30 (a known GPS-week-rollover clock glitch,
already filtered elsewhere via `dateUtils.ts::EARLIEST_PLAUSIBLE_DATE`) through the present. 904
points is trivially fast to render — the phase's own "under 1s" acceptance criterion is not a
close call at this scale.

## Decisions

### 1. Three deliberate scope reductions from the brief, confirmed with the user in view of the brief's own reasoning

Presented to the user as an explicit three-way tradeoff (via `AskUserQuestion`, after the brief
itself was made available mid-session) rather than silently picked either direction:

- **Map tiles: public OpenStreetMap raster tiles via Leaflet, not self-hosted Protomaps vector
  tiles via MapLibre GL JS.** The brief's own stated reason for Protomaps was privacy (map
  viewport requests, not just raw GPS data, never leaving the NAS) — the user was shown that
  reasoning explicitly and still chose public OSM tiles, for far less setup (no `.pmtiles`
  extract to build/host, no MapLibre GL JS dependency, Leaflet's simpler API). The real,
  acknowledged cost: tile requests for whatever map area is being viewed go to OSM's public tile
  server. This does not send raw GPS routes or start-point coordinates anywhere — only which
  map *area* is currently in view — but if the map's default view centers on frequently-visited
  areas (home), that's a weaker privacy posture than the brief's zero-network-tile-request
  design. Documented here so it's a known, revisitable tradeoff, not a forgotten one.
- **Server-side privacy zones (fuzzing start points / trimming routes near home before they
  leave the API) are explicitly deferred, not built in this phase**, despite the brief calling
  them "mandatory... not optional." Real gap, tracked here: **this means the map explorer
  should not be exposed on the public reverse proxy without revisiting this decision first** —
  the whole reason the brief calls it mandatory (a decade of start points, including home,
  becoming publicly reachable) is unchanged by deferring the fix. `docs/DEPLOY.md` should note
  this constraint when the map explorer is wired into the deployed stack.
- **PWA/offline: an installable app shell only (manifest + service worker caching the built JS/
  CSS bundle), not the brief's "offline reads of recent activities."** Real offline data access
  (caching API responses so recently-viewed activities/health data are browsable with zero
  network) is a materially bigger effort — cache invalidation, storage limits, staleness UI —
  and is left for a future phase if it turns out to matter in practice.

### 2. Map explorer: Leaflet + `react-leaflet` + a new bounded `GET /activities/map` endpoint

- **Leaflet**, not MapLibre GL JS, per decision 1 — a ~40KB, MIT-licensed, zero-API-key library
  that's the standard pairing with public OSM raster tiles. `react-leaflet` (the React wrapper)
  is used rather than hand-rolling raw Leaflet DOM management inside React, matching this
  project's precedent from Recharts (ADR 0010 decision 1): adopt a real wrapper library once a
  view's actual complexity justifies it, rather than reinventing that integration layer by hand.
  One new runtime dependency, justified the same way Recharts was — verified to install and
  build clean with React 19 before anything else in this milestone depends on it.
- **New endpoint, not a reuse of `GET /activities`**: `ActivitySummary` doesn't carry lat/lng at
  all today (that lives in `route_geom`, joined only by `GET /activities/{id}`'s detail
  endpoint), and the map wants every GPS-bearing activity's start point in one response, not a
  paginated list. Given the real scale (904 rows, well inside "a bounded single query" territory
  the same way `/activities/{id}/context` already is — see ADR 0010 decision 5), this is one
  direct query against `activity` joined to `route_geom`, filterable by `sport` and date range,
  returning `{id, sport, local_date, name, start_lat, start_lng}` per point — enough for a map
  marker and a click-through to the activity detail page, nothing per-point that the frontend
  doesn't need (no full polyline; that stays on the existing per-activity detail endpoint).
- **All-routes heatmap** (the brief's second map-explorer element) is out of this milestone's
  scope — the phase's own acceptance criterion is specifically the start-point map's render
  time, and a heatmap is a genuinely separate rendering path (needs simplified polylines, not
  just start points). Tracked as a stretch item for a later pass at this same phase, not
  silently dropped.

### 3. Recaps: extend the existing Year/Month/Week/Day views, not a new dedicated page

User's explicit choice over a separate "Wrapped"-style single page. `YearView`/`MonthView`
already carry substantial stats (via `PeriodStatsCard`, `RunningStats`, heatmaps); `WeekView`
gained an equivalent depth this session (Week stats, a Running card with week-over-week deltas
and METs, a Wellness card). The brief's specific recap ingredients not yet covered anywhere —
**PBs set within the period** (distinct from the all-time personal-records table `RunningStats`
already has), **year-over-year deltas**, and **image export** — were Milestone B's concrete gap
list; the first two shipped, the third was dropped after verification — see decision 5.

**PBs set**: `runningStats.ts::newAllTimePrs(periodRecords, allTimeRecords)` — a pure function
that flags a period's own "fastest at this distance" as a genuine all-time PR only when it
shares both the distance label *and* the date with the athlete's true all-time best (computed
by calling the existing `personalRecords()` a second time over an unbounded `useAllActivities`
fetch, distinct from each view's period-scoped one). Wired into `RunningStats.tsx` (Year/Month,
with a trophy-badge column and a headline), `WeekRunningStats.tsx` (Week, headline only — no
existing table to badge), and `DayViewPage.tsx` (Day, headline only) — covering all four grains
the user's original scope answer named. **Year-over-year deltas**: a new `compareLabel`/
`compareDistanceM` pair of props on `PeriodStatsCard`, fed by a second, rollup-backed
`/calendar/months` fetch (summed for Year, single-period for Month) — no raw-activity refetch
needed, matching the project's "precomputed rollups are mandatory" rule.

### 4. PWA shell: `vite-plugin-pwa`

Standard, well-maintained tool for exactly "installable shell, cache the built assets" in a Vite
project — generates the manifest and a Workbox-based service worker from the existing build
output, no hand-rolled service worker code. Scoped per decision 1: precache the app shell only,
no runtime caching strategy for API responses (that's the deferred "real offline data" case).

### 5. Image export: dropped after verification, not shipped

`html-to-image` was installed and verified to install/build clean with React 19 (0
vulnerabilities), per this project's standing "verify a vendor library's actual behavior before
adopting" rule. It failed the *next* check — actually exporting a real page. `toPng()` hung
indefinitely (no error, no resolution after 20+ seconds) on every real recap page tested,
including the smallest one (`WeekView`), not just the large `YearView`. Root cause, confirmed by
direct DOM inspection: ~82% of a real `WeekView`'s exported subtree (1227 of 1488 nodes) sits
inside Recharts' rendered SVG output (`FitnessChart`, the four charts in `RunningStats`/
`WeekRunningStats`, `HealthTrendChart`) — `html-to-image`'s per-node computed-style inlining
does not handle that volume of nested SVG cleanly, producing multi-megabyte intermediate SVG
data URIs (23–27MB on `YearView`, 3.9MB even on `WeekView`) and, empirically, an unbounded hang
rather than just slowness. `skipFonts: true` and `pixelRatio: 1` were tried first (the two
standard mitigations for this class of library) and made no difference. Given every recap page
in this app uses Recharts throughout, this isn't a narrow-page problem to work around --
presented to the user as a three-way choice (drop it / narrow scope to stats-only, no charts /
try a different library), and the user chose to drop it from this phase rather than ship a
feature that hangs or gut it down to a chart-less image. The `html-to-image` dependency, the
`ExportImageButton` component, and its wiring in `YearView`/`MonthView`/`WeekView`/
`DayViewPage.tsx` were fully removed (not left half-wired) — confirmed via a build hash match
against the pre-experiment build. Revisiting this is a distinct future task, not a resumption of
this one: it would need either a library that handles SVG-heavy DOMs, or a fundamentally
different technique (e.g. browser-native `window.print()` to PDF via a print stylesheet, which
uses the browser's own renderer instead of a JS DOM-to-canvas library and wouldn't share this
failure mode).

### 6. PWA shell implementation: `vite-plugin-pwa` + a hand-generated icon set, with one real bug caught before shipping

`vite-plugin-pwa` (`generateSW` mode, the default) was installed and verified to build clean
with this Vite/React 19 setup before anything else depended on it, matching this project's
per-dependency verification rule. Configuration in `vite.config.ts`:

- `registerType: "autoUpdate"` — the built shell updates silently on next load rather than
  prompting the user, since there's no offline-data staleness UI here for a stale-shell prompt
  to hand off to (this app doesn't cache API responses at all per decision 1).
- **Icons**: no SVG-to-PNG conversion tool was available in this environment (no ImageMagick,
  Inkscape, `rsvg-convert`, or Python `PIL`/`cairosvg` installed) and round-tripping large PNG
  bytes through the browser tool as base64 text proved unreliable (silent truncation on the
  largest icon in transit — a real, reproducible data-corruption issue with that channel, not a
  one-off). Instead, `frontend/public/pwa-{192x192,512x512,maskable-512x512}.png` were generated
  by a small **pure-stdlib PNG encoder** (`zlib`+`struct`+`binascii`, no image library at all) --
  a dark-navy background (`#12151c`, matching `theme.css`'s dark `--color-bg`) with an
  accent-blue (`#4da3ff`) zigzag line echoing `Icon.tsx`'s hand-rolled "pulse" glyph, rasterized
  by direct point-to-segment distance per pixel. The maskable variant uses more center padding
  (per the maskable-icon safe-zone spec) than the two "any"-purpose icons. Verified by loading
  each file back through a real `Image()` element and confirming `naturalWidth`/`naturalHeight`
  match the manifest's declared `sizes`.
- **`workbox.globIgnores: ["config.js"]`** — a real bug caught during verification, not a
  precaution taken blindly: `config.js` is regenerated at **container start** from
  `SPORTHEALTH_API_BASE_URL` (`docker/frontend-entrypoint.d/20-generate-config.sh`), specifically
  so the same built image works across environments with different reverse-proxy topologies
  (`docs/DEPLOY.md`, ADR 0008 decision 6). Workbox's default `generateSW` glob matches every file
  under the build output, including `public/`-copied static files — precaching `config.js` would
  compute its revision hash from the *committed dev-default* file content at build time (a fixed
  value, identical across every image build regardless of what `SPORTHEALTH_API_BASE_URL` gets
  set to later), so the service worker would never recognize a redeployed container's differently
  regenerated `config.js` as "changed" and would keep serving whichever API base URL happened to
  be live the first time a given browser installed the PWA — silently breaking connectivity after
  a hostname/topology change, for exactly the class of deployment difference `config.js` exists
  to handle. Confirmed fixed: the precache entry count dropped from 9 to 8 and `config.js` no
  longer appears anywhere in the built `sw.js`.

## Milestones

- **A — Map explorer — done.** `GET /activities/map` (new, bounded, unpaginated endpoint) +
  `MapExplorerPage.tsx` (Leaflet + `react-leaflet` + public OSM tiles), sport/date filtering
  (client-side, against the one fetched set), click-through to activity detail via a marker
  popup. Verified against the real archive — see Status.
- **B — Recaps — done, minus image export (dropped, see decision 5).** In-period PBs
  (`newAllTimePrs()`, wired into Year/Month's `RunningStats` table, Week's `WeekRunningStats`
  headline, and Day's `DayViewPage` headline) and year-over-year deltas (`PeriodStatsCard`'s
  `compareLabel`/`compareDistanceM`, rollup-backed, on Year and Month). Verified against the
  real archive — see Status.
- **C — PWA shell — done, with one open verification gap.** `vite-plugin-pwa` (manifest +
  generated service worker precaching the app shell only), hand-generated icon set. Manifest
  content, icon files, and the precache manifest (including the `config.js` exclusion fix) were
  all verified directly against the real build output. Live service-worker registration and
  actual "Add to Home Screen" installability on a real device were **not** verified this
  session — the sandboxed browser tool used throughout this project's verification work cannot
  register any service worker at all (confirmed via a trivial throwaway one, isolating this as a
  tool limitation, not a defect in the generated shell) — see decision 6 and Status.

## Consequences

- The map explorer must not be exposed on the public reverse proxy until privacy zones (decision
  1) are revisited — this is a real, tracked constraint on deployment, not just documentation.
- Switching the map stack to MapLibre + self-hosted Protomaps later is possible but not free:
  it's a different rendering library and a new NAS-hosted tile asset, not a drop-in swap.
- The PWA shell alone does not make any view usable without network — "offline" here means
  "the app loads instantly and shows its own UI," not "recent data is browsable offline." If
  that gap matters in practice, it's a distinct, larger follow-up, not an extension of this
  milestone.
