# ADR 0015: Scheduled workouts — author on the calendar, push to the Garmin watch (running first)

## Status

Shipped and live-verified for running (the user's own scoping choice — see decision 1) — a real
push against the author's own Garmin account (2026-09-03) confirmed the full round trip: pace
range, absolute HR range, and cadence riding alongside pace on the same step all stored and
read back correctly (see Verification). Yoga and bouldering shipped as a second, deliberately
simpler tier — a name, a duration, and a display-only time of day, no structured syntax at all
(the user's own explicit scoping: "no structured text syntax needed, it's just to put placeholder
for those sports") — pushed as a single no-target Garmin step for the whole duration
(`build_placeholder_workout`, decision 9). `fitness` still has no builder.

This is a new capability, not a bug fix or a phase-plan item — the user asked directly ("can you
push workouts to my garmin watch?") for a full training-schedule feature: use the existing
calendar to plan *future* activities in an intervals.icu-inspired text syntax, and push the
running ones to a Garmin watch as real structured workouts.

**Data model** (`db/schema.py::planned_workout`/`planned_workout_step`): a new, sport-agnostic
pair of tables, deliberately *not* the same as `activity_workout`/`activity_workout_step` even
though the column shape (duration/target/repeat) closely mirrors them — those are retrospective,
parsed out of a completed activity's own recorded FIT `workout_mesgs`; these are prospective,
authored by the athlete as free text and need a push-to-Garmin lifecycle
(`push_status`/`garmin_workout_id`/`garmin_scheduled_at`/`push_error`) the recorded tables have no
concept of. One workout per athlete per day (v1) — Garmin's own `schedule_workout()` is itself
date-granular, so there's no finer grain to support yet.

**The text syntax** (`workout_syntax.py`/`workoutSyntax.ts`): a real subset of intervals.icu's own
workout-builder syntax — duration (time or distance), a pace or heart-rate target (an absolute
range, a single value, or a `Z<n>` zone resolved against the athlete's own configured
`athlete_hr_zone_config`), trailing running cadence, and a simple `Nx` repeat block. Implemented
twice, deliberately (same precedent as `gap.ts`/`gap.py`): an authoritative Python parse run
server-side on every save, and a TS twin for instant client-side preview as the athlete types.
Both are exercised against one shared JSON fixture table
(`tests/fixtures/workout_syntax_cases.json`) rather than two hand-maintained fixture lists trusted
to agree by inspection.

**Garmin push** (`adapters/garmin_connect.py::GarminConnectAdapter.push_planned_workout`,
`planned_workouts.py::build_running_workout`/`push_planned_workout`): the one place this app
writes to a third-party account rather than only reading from it. Follows the adapter's existing
safety contract exactly — never constructs its own credentialed client, rate-limits every real
HTTP call, aborts immediately (no retry) on a 429. Editing an already-pushed workout deletes the
stale Garmin copy and re-pushes fresh, rather than a partial update — matching this project's
general full-recompute-over-incremental-patch preference (fitness rollup, insights engine)
applied to a new domain.

**Push trigger**: automatic for anything due within the coming week
(`worker/main.py::run_daily_workout_push`, its own daily schedule right after the Garmin sync),
plus a manual "Push now" override (`POST /planned-workouts/{date}/push`) — the user's own explicit
choice over a fully-manual or fully-automatic scheme.

**Calendar UI**: `ScheduleWorkoutForm.tsx` — a "Planned workout" section — is shared by both
places a day is actually viewed: `MonthView.tsx`'s expanded-day card, and `DayViewPage.tsx`
(`/day/:date`, the page `DateNavigator`'s own day picker and `RunningStats`' heatmap cells link
to). Originally wired into `MonthView.tsx` alone; a user report ("no button for that" on
`/day/:date`) caught the gap before `DayViewPage.tsx` got the same section — see decision 8.
Plus a small month-grid day-cell indicator, a "Copy" action on a completed activity
(`CopyWorkoutButton.tsx`) that round-trips its recorded steps back into syntax text via a
localStorage clipboard (`workoutClipboard.ts`) a "Paste" action on any day picks up, a "Repeat
this schedule" recurrence control (`POST /planned-workouts/recurring`) that materializes N
independent rows rather than a live recurring-rule object, and `StepBuilderModal.tsx` — a GUI
wizard that *generates syntax text and inserts it at the textarea cursor* rather than maintaining
parallel structured state, so the textarea/parsed preview stays the single source of truth. Both
the copy/paste and recurrence additions were added in direct response to the user's own plan-
review feedback before implementation started (see decision 5).

Full test coverage: 927 backend tests (workout-syntax fixture parity, adapter push mechanics, the
`planned_workouts.py` orchestration, the API router, the worker job's window/status filtering) and
556 frontend tests (the TS parser against the same shared fixtures, the schedule form on both
`MonthView` and `DayViewPage`, the day-cell indicator, `StepBuilderModal`) — all green,
`ruff`/`mypy`/`tsc --noEmit` clean.

## Vendor facts verified directly (not assumed)

Confirmed by reading the installed `garminconnect` package's own source
(`.venv/Lib/site-packages/garminconnect/`), not recalled from memory — CLAUDE.md's own warning
about this exact class of fast-moving vendor library, and true here: PyPI is already several
patch releases ahead of whatever's locked at any given time.

- `garminconnect.workout` ships real Pydantic models (`RunningWorkout`, `ExecutableStep`,
  `RepeatGroup`, `WorkoutSegment`) and factory helpers (`create_warmup_step` etc.), but **the
  factory helpers only set the target *type*, never target *values*** — there's no parameter
  anywhere for `targetValueOne`/`targetValueTwo`/`zoneNumber`. `ExecutableStep.model_config =
  ConfigDict(extra="allow")` is what makes a real target possible: constructing `ExecutableStep`
  directly with those extra kwargs, not via the factory helpers, is how `planned_workouts.py`
  actually attaches a pace-range or HR-range target.
- Web-confirmed wire format (the library's own docstring only shows a no-target example): pace
  targets use `workoutTargetTypeKey: "pace.zone"` with `targetValueOne`/`targetValueTwo` in
  **m/s**; HR targets use `"heart.rate.zone"` with either `zoneNumber` or
  `targetValueOne`/`targetValueTwo` in **bpm** — mutually exclusive, never both.
- `Garmin.upload_running_workout(workout)` returns a dict with `workoutId`;
  `schedule_workout(workout_id, date_str)` schedules on a **date only, no time-of-day** — there is
  no reachable API for "start this at 6:00am." `delete_workout(workout_id)` removes a workout
  template from the library entirely.
- `pydantic` is already a project dependency (FastAPI needs it), so no new dependency was needed.
- Cadence riding alongside a pace target on the same step (a `secondaryTarget`) is **not
  confirmed** from static reading alone — see decision 4 and the Verification section below.

## Decisions

### 1. Scope: running first, sport-agnostic schema from day one

The user's own choice, from two explicit options offered: implement running fully (it exercises
every part of the design — calendar UI, the text parser, the Garmin push path) before yoga/
bouldering/fitness, which are simpler and will reuse the same plumbing. The schema
(`planned_workout.sport` is an open string, not an enum) needs zero migration to add a 5th sport
later — matching CLAUDE.md's own additive-schema-evolution principle.

### 2. Push trigger: automatic within the coming week, plus a manual override

The user's own answer to "how should pushing work?", given as free text rather than picking one
of the two offered options (fully automatic vs. fully manual) — "push it if it's within the
coming week." Implemented as a daily worker job scanning a rolling 7-day window
(`PERSEVERER_PLANNED_WORKOUT_PUSH_WINDOW_DAYS`, configurable) for anything not yet pushed, plus
`POST /planned-workouts/{date}/push` for wanting it on the watch immediately regardless of date.

### 3. `planned_workout`/`planned_workout_step` are new tables, not a reuse of `activity_workout`

Considered reusing `activity_workout`/`activity_workout_step` (Phase 1's own retrospective
workout-plan tables) directly. Rejected: those are keyed 1:1 on a completed `activity_id`, parsed
out of a device's own recorded FIT `workout_mesgs`, and only ever populate `target_type=="speed"`
(HR/cadence targets are recognized in the FIT format but never extracted — a real, confirmed gap
in the existing parser). A planned workout has no `activity_id` yet (it doesn't exist until run),
needs a push lifecycle those tables have no columns for, and needs to target HR/zones/cadence
which the existing extraction never populates. New tables, closely mirroring the old ones' shape
so `workoutSteps.ts`'s expand/group helpers work across both (see decision 6), cost one migration
and avoid retrofitting push-lifecycle columns onto a table with a different, load-bearing
identity contract.

### 4. Cadence-alongside-pace: a best-effort guess, live-verified correct

`ExecutableStep`'s `extra="allow"` lets arbitrary extra fields through, but there's no publicly
documented "secondary target" field for e.g. cadence riding alongside a primary pace target on
the same step. `planned_workouts.py::_cadence_extra` sends a plausible-shaped guess
(`secondaryTargetType`/`secondaryTargetValueOne`/`secondaryTargetValueTwo`, mirroring the primary
target's own shape) rather than omitting cadence entirely — deliberately isolated to one function
so it'd be the one place to fix if a real push showed it didn't reach the watch. **Live-verified
2026-09-03** (see Verification below): a real push against the author's own Garmin account
correctly stored both the pace target and the cadence secondary target on the same step;
`get_workout_by_id` read the pushed workout back with `secondaryTargetType.workoutTargetTypeKey:
"cadence"` (Garmin's own canonical key — the function originally guessed `"cadence.zone"`, which
the server tolerated on write but never echoes back on read; updated to send `"cadence"` directly
for round-trip fidelity) and the exact `170`/`180` spm values submitted.

### 5. Copy/paste and recurrence were added after the user rejected the first plan draft

The first plan draft's "Copy from…" design was an in-form search picker over past activities
*and* existing planned workouts, pre-filling the schedule form on selection. The user rejected
this directly: "no, just a copy from an activity and then we can pick a date and paste the
activity" — a plain clipboard model, not a picker embedded in the form. Implemented as
`workoutClipboard.ts` (localStorage, a per-viewer convenience like every other localStorage use
in this app) written to by a "Copy workout" button on `ActivityDetailPage.tsx` and read by a
"Paste copied workout" affordance on any calendar day's schedule form. The user's same rejection
also asked for recurrence ("create multiple copies of an activity to repeat it every week, every
other day or every month") and a GUI step builder ("a step button to help build the step with a
multiple choice popup") — both designed in directly rather than deferred, per this project's own
established fold-in-scope preference.

**Addendum (after hiit/strength_training shipped, decision 10)**: `WorkoutClipboardItem` had
only ever carried `sport`/`name`/`source_text` — fine for running (the only tier that existed
when Copy/Paste was built), but silently lossy for every tier added since: pasting a copied
yoga/bouldering workout dropped its `duration_minutes`/`scheduled_time`, and pasting a copied
hiit/strength_training workout dropped its exercise steps entirely (the clipboard had nowhere to
put them). Fixed by extending the clipboard item with optional `scheduled_time`/
`duration_minutes`/`steps` fields and a second "Copy" source: alongside `CopyWorkoutButton`
(a completed activity → clipboard, running-structured activities only, unchanged), `WorkoutSummary`
in `ScheduleWorkoutForm.tsx` now has its own "Copy" action on an *already-scheduled planned*
workout — the richer source, since a planned workout already carries every field a paste needs in
exactly the shape `apiStepsToEntries` (the same hydration helper Edit already uses) expects.
`handlePaste()` now restores all of it, branching on the pasted sport exactly like `startEditing`
already does. "Repeat this schedule" needed no fix — it already forwards whatever's currently in
the form (including `steps`/`duration_minutes`) regardless of sport tier, since it was built
against the same mutation payload from the start.

### 6. `workoutSteps.ts`'s expand/group helpers generalized to a shared `WorkoutStepLike`, not duplicated

`expandWorkoutSteps`/`groupWorkoutStepsForDisplay`/`consumedByRepeats` originally took
`ActivityWorkoutStepOut[]` only. Rather than writing a second copy for `PlannedWorkoutStepOut`
(the two step shapes share the same repeat-block/`step_index`/`intensity` structure by design —
see decision 3), these functions were made generic over a minimal structural interface both types
already satisfy. Target-label formatting stayed *separate* (`targetPaceRangeLabel` for recorded
speed-only steps vs. new `plannedTargetLabel`/`plannedCadenceLabel` for planned pace-or-HR-or-zone
steps) — the two step shapes' target semantics genuinely differ (m/s vs. bpm vs. zone number), and
one function trying to branch across both would obscure more than it'd share.

### 7. Recurrence materializes independent rows, not a recurring-rule object

`POST /planned-workouts/recurring` does the date math once and inserts N independent
`planned_workout` rows, each a full copy of the same content — not a template/rule other rows
reference. Matches this project's existing preference for concrete, fully-materialized data over
abstract rules needing their own invalidation logic (rollups, the insights engine's own full
recompute). A date that already has a planned workout is skipped, not overwritten, and reported
back so the athlete can see which dates didn't get the new content. Editing or deleting one
occurrence afterward is completely independent of the others.

### 8. A real user-reported gap: `ScheduleWorkoutForm` was missing from `DayViewPage.tsx`

The initial implementation added `ScheduleWorkoutForm.tsx` only to `MonthView.tsx`'s expanded-
day card — the calendar page's own inline day-detail view. It missed that `DayViewPage.tsx`
(`/day/:date`) is a *second*, separate page for viewing one day, and the one `DateNavigator`'s
own day-picker and `RunningStats`' heatmap cells actually link to — plausibly the more common
way to land on a future date at all. Caught by direct user report ("I went in calendar view at
a future day to create a workout but there's no button for that"), not by testing (this session's
own frontend tests exercised `MonthView` and `ScheduleWorkoutForm` in isolation, never a real
navigation path through `DateNavigator` into `DayViewPage`). Fixed by mounting the same
`ScheduleWorkoutForm` component in both places rather than writing two — it already fetches its
own data by `localDate` prop, so no parent-level wiring beyond the one `<ScheduleWorkoutForm
localDate={date} />` line was needed in either page.

### 9. Yoga/bouldering: a duration + time-of-day placeholder, still a real Garmin push

Added after shipping, per the user's own explicit follow-up scoping ("scope out yoga and
bouldering first, no structured text syntax needed, it's just to put placeholder for those
sports") plus a direct answer to the one genuinely open question (push to Garmin, or calendar-
only?): push it, as a trivial duration-only workout. `planned_workouts.py::PLACEHOLDER_SPORTS`
(`{"yoga", "bouldering"}`) skips `workout_syntax.py` parsing entirely in `save_planned_workout` —
`source_text`, if given, is freeform notes only — and takes `duration_minutes` directly instead
of deriving `estimated_duration_s` from a parse. `scheduled_time` (new `planned_workout` column,
"HH:MM") is orthogonal to that split and stored either way; it's Perseverer's own calendar
display metadata only, same "Garmin's `schedule_workout()` has no time-of-day API at all"
limitation documented for running from day one. `build_placeholder_workout` constructs a single
no-target step spanning the whole duration, wrapped in a plain `BaseWorkout` (no
`YogaWorkout`/`BoulderingWorkout` subclass exists in `garminconnect.workout`, unlike
`RunningWorkout` — constructing `BaseWorkout` directly with an explicit `sportType` works the
same way) — yoga gets a real Garmin sport type; bouldering, a real sub-discipline of rock
climbing (this app already knows that for *recorded* activities —
`garmin_activity_summary.py`'s own `sport="rock_climbing"`/`sub_sport="bouldering"` pair, Garmin's
real activity-type taxonomy), has no slot in the *Workout Builder*'s own, separate sport list —
confirmed live against Garmin's real `GET /workout-service/workout/types` response (not just the
`garminconnect` package's own hardcoded `SportType` class): the actual workout-service sport
types are running/cycling/swimming/strength_training/cardio_training/yoga/pilates/hiit/other/
multi_sport/mobility/rucking, no climbing entry at all. A real constraint of that one Garmin
subsystem, not a gap this app's own data model or the library introduced — falls back to
`SportType.OTHER`: the workout still pushes and schedules correctly, just shows as "Other" rather
than "Bouldering" in Garmin Connect/on the watch (the workout's own `name` field still says
"Bouldering" regardless).

This surfaced one real, pre-existing bug in `GarminConnectAdapter.push_planned_workout`: it
called the sport-specific `upload_running_workout`, which raises `TypeError` for anything but a
real `RunningWorkout` instance (confirmed live, not assumed, by reading the check inside the
installed `garminconnect` package itself) — unusable for yoga/bouldering's plain `BaseWorkout`.
Fixed by switching to the generic `upload_workout(workout.to_dict())`, which has no such
restriction; running's own push path is unaffected (same JSON either way).

It also surfaced a second bug in the router: `GET /planned-workouts/{date}` unconditionally
re-parsed `source_text` through `workout_syntax.py` to surface parse errors, regardless of
sport — for yoga/bouldering's freeform notes, this produced bogus "unrecognized duration" errors
on plain prose. Fixed by gating that re-parse to `sport == "running"` only, caught by a test
before it ever shipped.

### 10. hiit/strength_training: a real exercise picker, not a third placeholder or a text syntax

Added after shipping, per the user's own explicit follow-up ("let's add support for hiit and
strenth_training") and their choice among three offered authoring styles: a searchable picker
over Garmin's own real exercise catalog (`garminconnect.exercises`, 1,527 exercises across 47
categories), each step naming a real exercise with sets/reps or time, optional weight/rest —
matching what actually shows up on the watch, rather than a simpler yoga/bouldering-style
placeholder or a free-text syntax (there's no natural text grammar for naming a specific Garmin
exercise the way there is for a pace or HR target).

Confirmed live against Garmin's real `GET /workout-service/workout/types` response (not the
`garminconnect` package's own hardcoded `SportType` class) that hiit and strength_training are
both real Workout Builder sport types — unlike bouldering (decision 9), no fallback mapping is
needed. `planned_workouts.py::EXERCISE_SPORTS` is a third sport tier alongside running's parsed
syntax and yoga/bouldering's placeholders: `build_exercise_workout`/`_build_exercise_step` build
the real Garmin wire format directly from already-structured `PlannedStepLike` rows supplied by
the frontend's picker (never parsed from text) — `weightValue` is grams despite the app's own
`weight_kg` storage unit (kg × 1000, confirmed live), and `category`/`exerciseName` ride as extra
`ExecutableStep` fields (`ConfigDict(extra="allow")`) alongside a reps- or time-based end
condition. `build_workout_segment` (previously running-only, hardcoding `hr_boundaries`/
`max_hr_bpm`) was generalized to take a `StepBuilder` callback so running and hiit/
strength_training share one repeat-group/step-order implementation rather than duplicating it —
`build_running_workout` now passes a closure capturing its own HR-zone state, `build_exercise_
workout` passes the stateless `_build_exercise_step`.

Four new `planned_workout_step` columns (`duration_reps`, `exercise_category`, `exercise_name`,
`weight_kg`) are additive — running's own columns are untouched, and `save_planned_workout`'s
insert-dict builder uses `getattr(s, field, None)` per exercise-only field rather than forcing a
shared shape onto `workout_syntax.ParsedStep` (deliberately kept running-only) or writing a
second near-duplicate insert block. `exercise_name` is stored as `""` (not `None`) when a step
names just the category with no specific variant, matching Garmin's own catalog convention (an
entry's own `exercise` code sometimes equals its `category`, e.g. "Bench Press") — kept
distinguishable from "no exercise at all" (`exercise_category is None`), which a `duration_type
== "rest"` step legitimately has.

The frontend catalog (`frontend/src/data/exerciseCatalog.json`, regenerated from the installed
`garminconnect` package by `scripts/generate_exercise_catalog.py`) is loaded via a dynamic
`import()`, not a static one — a static import pushed the app-shell bundle from ~2.0MB to
2.24MB and broke the production build against vite-plugin-pwa's 2MB single-file precache limit.
Code-splitting it into its own ~230KB chunk keeps the shell lean for the common case;
`ScheduleWorkoutForm` calls `preloadExerciseCatalog()` unconditionally on mount so the fetch is
already in flight well before the athlete picks an exercise sport. `ExerciseStepEditor.tsx` is
itself the canonical authored content (unlike running's textarea + live-parsed preview) —
producing `PlannedWorkoutStepIn[]` directly, with no intermediate text representation, since
there's nothing to parse.

### 11. Exercise library page: three honestly-distinguished tiers, not one blended guess

Added after shipping, per the user's own explicit request for a browsable reference of every
exercise the hiit/strength_training picker's catalog supports — categories collapsed, each
expanding to its exercise list with a photo, a description, and a link to the exercise's own
Garmin page. The first draft (a standalone artifact, not yet in the app) fell back to one
representative photo per *category* for any exercise with no confident individual match; the
user rejected this directly ("the images you found are not good, you repeated the same image way
too many times") and asked for a real per-exercise photo, a description, and a Garmin link for
*every* exercise, added as a real section of the app rather than a one-off document.

Two real, live-verified data sources cover the catalog, and neither covers all of it:

- **Garmin's own "detailed" exercises** — confirmed live that
  `GET https://connect.garmin.com/web-data/exercises/en-US/<CATEGORY>/<EXERCISE>.json` needs no
  authentication and returns a real `heroImage` + `description` for roughly 160 of the 1,527
  exercises (~10%) — the same subset the community `GarminExercisesCollector` project's own
  spreadsheet independently calls "Detailed" (146/1207 in that project's own snapshot, close
  enough to confirm it's measuring the same real constraint, not a scraping bug on either side).
  The other ~90% genuinely have no Garmin-authored photo or description anywhere — not a gap this
  project's own scraping effort can close.
- **free-exercise-db** (github.com/yuhonas/free-exercise-db, public domain, ~870 exercises)
  covers a further ~150 by name-matching (stripping equipment words, folding singular/plural) —
  but this time **every fed photo is claimed by at most one Garmin exercise**, highest-confidence
  match first. Without that cap, matching alone found real photos for 574 exercises, but dozens
  of Garmin's own named variants (50+ push-up variations, dozens of curl/row/bench-press
  variants) all reduce to the same stripped name and would have all pointed at one shared fed
  photo — exactly the repetition the user had already rejected once, just moved down to
  per-exercise granularity instead of per-category. Capping each photo to a single claimant cut
  photo coverage to ~150 fed-sourced matches, but every remaining photo is genuinely that
  exercise's own, not a stand-in borrowed from a same-named sibling.
- Everything else (still real data, not a guess) falls back to Garmin's own master exercise list
  (`GET https://connect.garmin.com/web-data/exercises/Exercises.json`, no auth), which has
  primary/secondary muscle groups for every exercise in the catalog even when it has no photo —
  rendered as plain muscle-group text, with an honest "No photo" placeholder rather than a
  reused image implying it has one.

`garmin_url` (`https://connect.garmin.com/modern/exercises/<CATEGORY>/<EXERCISE>`) is
constructed for every exercise regardless of tier — confirmed live (302 redirect to a real
`/app/exercises/...` SPA route) that this resolves for any category/exercise pair, not just the
detailed ones, so the link is always real even when the page it lands on has little content for
a non-detailed exercise.

`scripts/generate_exercise_library.py` does all three live fetches (~1,527 requests to Garmin's
public endpoint plus one to free-exercise-db, ~15s total) and writes the merged, tier-labeled
result to `frontend/src/data/exerciseLibrary.json` — a ~1MB asset, dynamically imported by
`ExerciseLibraryPage.tsx` (same "keep it out of the app-shell bundle" pattern as
`exerciseCatalog.json`) since photos are hotlinked directly from Garmin's/free-exercise-db's own
CDNs rather than copied into this repo (the same "external resource, loaded at runtime" pattern
this app's CARTO basemap tiles already use, not a new architectural choice).

## Verification

`uv run pytest -q` (927 passed), `uv run ruff check .`, `uv run mypy` (clean), `cd frontend && npm
run typecheck && npm run build` (clean), `npx vitest run` (556 passed) — all green.

**Live push, with the user's explicit go-ahead (2026-09-03)**: scheduled a real running workout
("CLAUDE TEST — safe to delete", 2026-09-05) against the author's own Garmin account —
`Warmup 10m` (no target), a `3x` block combining `5:00-5:20/km Pace` *and* `170-180spm` cadence on
the same step, plus a `140-150 HR` interval, `Cooldown 5m`. `push_planned_workout` returned
`success=True` with a real `garmin_workout_id`; the local `planned_workout` row correctly recorded
`push_status="pushed"` and `garmin_scheduled_at`. Read back via `get_workout_by_id` (not just
trusted from the upload response) and diffed against what was sent: `targetValueOne`/
`targetValueTwo` for both the pace range (3.125–3.3333 m/s, exactly `5:00-5:20/km`) and the HR
range (140–150 bpm) round-tripped exactly, and — the one thing that couldn't be verified from
documentation alone — the cadence secondary target also round-tripped exactly (170/180 spm),
confirming decision 4's guess was correct in shape; the one adjustment made from this
verification was switching `_cadence_extra`'s `workoutTargetTypeKey` from the original guess
(`"cadence.zone"`, which the server tolerated on write) to `"cadence"` (what it actually echoes
back on read), for exact round-trip fidelity. Visual confirmation in the Garmin Connect app/
website itself (that the workout renders and would actually prompt correctly on a real device
during a run) is still up to the user to glance at — the API-level round-trip above is as far as
this session can verify directly.

**Yoga/bouldering (decision 9)**: `uv run pytest -q` (934 passed), `uv run ruff check .`,
`uv run mypy` (clean), `cd frontend && npm run typecheck && npm run build` (clean), `npx vitest
run` (562 passed) — all green, including the two real bugs decision 9 describes (both caught by
tests before shipping, not live).

Also live-verified against the author's own Garmin account (2026-09-04), prompted by a user
correction that bouldering's `SportType.OTHER` mapping needed checking against the real API
rather than trusted from the `garminconnect` package's own hardcoded `SportType` class — which is
exactly what surfaced the `GET /workout-service/workout/types` confirmation in decision 9's own
vendor-facts update. Pushed a real "CLAUDE TEST — bouldering — safe to delete" placeholder
(90 min, `scheduled_time="19:00"`, no `source_text`); `push_planned_workout` returned
`success=True` with a real `garmin_workout_id`, and `get_workout_by_id` confirmed the exact
structure sent: `estimatedDurationInSecs: 5400`, a single step with `endConditionValue: 5400.0`
and `targetType: "no.target"`, `sportType: {"sportTypeId": 3, "sportTypeKey": "other"}` — the
workout's own `workoutName` still reads "CLAUDE TEST — bouldering — safe to delete" regardless of
the generic sport type. Deleted afterward via the same `adapter.delete_workout` path
`DELETE /planned-workouts/{date}` uses.

**hiit/strength_training (decision 10)**: `uv run pytest -q` (944 passed), `uv run ruff check .`,
`uv run mypy` (clean), `cd frontend && npm run typecheck && npm run build` (clean, including the
PWA precache-size regression caught and fixed before it ever reached CI), `npx vitest run`
(578 passed) — all green.

Live-verified against the author's own Garmin account (2026-09-05), confirming both the wire
format and the sport-type mapping before writing the adapter code: pushed a real "CLAUDE TEST —
strength — safe to delete" workout (workout_id 1687437265, scheduled 2026-09-08) built with
`build_exercise_workout`/`_build_exercise_step` directly (not yet through the API/frontend, which
didn't exist at that point in the session), read back via `get_workout_by_id`, and confirmed the
exact structure sent: `weightValue` in grams (kg × 1000) and `weightUnit` matching
`WEIGHT_UNIT_KILOGRAM` exactly, `category`/`exerciseName` present as extra `ExecutableStep`
fields, a reps-based step using `ConditionType.REPS` and a rest step using `StepType.REST` with
no exercise fields at all. Deleted afterward via `adapter.delete_workout`, same as every other
live-verification push in this ADR.

**Exercise library page (decision 11)**: `cd frontend && npm run typecheck && npm run build`
(clean), `npx vitest run` (589 passed, including 7 new tests for `ExerciseLibraryPage`) — all
green. `scripts/generate_exercise_library.py` run for real (not mocked) against the live Garmin
and free-exercise-db endpoints: 1,527 exercises processed in ~15s, 0 request errors, tier counts
161/149/1,217 (Garmin photo+description / free-exercise-db photo+instructions / muscle-groups
only) — confirmed zero photo is shared by two different exercises (`len(set(image_urls)) ==
len(image_urls)` over every non-null `image_url`). Manually verified live in the browser at
`/exercises`: categories load collapsed with correct counts, expanding one shows real distinct
photos alongside Garmin's own prose descriptions for detailed exercises (e.g. "Barbell Bench
Press") and an honest "No photo" placeholder alongside muscle-group text for undetailed ones
(e.g. "Barbell Board Bench Press"), the tier-2 attribution note appears exactly when the matched
free-exercise-db exercise's name differs from the Garmin one, and searching ("kettlebell swing")
correctly narrows to and auto-expands only the 3 categories with a real match.
