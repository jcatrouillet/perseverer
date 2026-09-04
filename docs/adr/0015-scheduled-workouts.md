# ADR 0015: Scheduled workouts — author on the calendar, push to the Garmin watch (running first)

## Status

Shipped and live-verified for running (the user's own scoping choice — see decision 1) — a real
push against the author's own Garmin account (2026-09-03) confirmed the full round trip: pace
range, absolute HR range, and cadence riding alongside pace on the same step all stored and
read back correctly (see Verification). Yoga/bouldering/fitness
share the same `planned_workout` schema (additive, no migration needed to add them) and the same
push orchestration, but have no step-level syntax to parse yet — that's the next slice, not part
of this round.

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
