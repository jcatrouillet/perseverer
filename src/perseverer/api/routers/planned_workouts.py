"""POST /planned-workouts (create), GET/PUT/DELETE /planned-workouts/{workout_id} (id-keyed --
a day can hold any number of workouts, so there's no date-keyed single-object route any more),
GET /planned-workouts/by-date/{local_date} (every workout on one date), GET /planned-workouts
(date-range summary list for the calendar grid), POST /planned-workouts/{workout_id}/push,
POST /planned-workouts/{workout_id}/complete and .../uncomplete (the athlete's own manual
completion marker, independent of push_status -- see db/schema.py::planned_workout's own
docstring), POST /planned-workouts/recurring -- scheduled (future) workouts authored on the
calendar and pushed to the Garmin watch. See db/schema.py::planned_workout for the storage
shape, workout_syntax.py for the text syntax, and planned_workouts.py for the parse/save/push
orchestration this router stays a thin layer over (same balance goals.py/api/routers/goals.py
already strikes).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import Connection, Row, select
from sqlalchemy.engine import Engine

from perseverer.adapters.garmin_connect import GarminConnectAdapter, RateLimiter, RateLimitSettings
from perseverer.api.dependencies import SettingsDep, get_conn, get_engine, require_api_key
from perseverer.api.schemas.planned_workouts import (
    ParseErrorOut,
    PlannedWorkoutCreateIn,
    PlannedWorkoutIn,
    PlannedWorkoutListItemOut,
    PlannedWorkoutOut,
    PlannedWorkoutSegmentOut,
    PlannedWorkoutStepIn,
    PlannedWorkoutStepOut,
    RecurringWorkoutIn,
    RecurringWorkoutOut,
)
from perseverer.api.schemas.settings import JobTriggerOut
from perseverer.db.schema import (
    athlete_hr_zone_config,
    athlete_running_load_config,
    planned_workout,
    planned_workout_step,
)
from perseverer.planned_workout_stats import WorkoutEstimate, estimate_workout
from perseverer.planned_workouts import (
    PlannedStepLike,
    activities_by_local_date,
    compute_recurrence_dates,
    matching_activity_id,
    push_planned_workout,
    save_planned_workout,
)
from perseverer.workout_syntax import ParsedStep, ParseError, parse_workout_syntax

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/planned-workouts")
def list_planned_workouts(
    start_date: Annotated[str, Query()],
    end_date: Annotated[str, Query()],
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[PlannedWorkoutListItemOut]:
    rows = conn.execute(
        select(
            planned_workout.c.local_date,
            planned_workout.c.id,
            planned_workout.c.sport,
            planned_workout.c.name,
            planned_workout.c.scheduled_time,
            planned_workout.c.push_status,
            planned_workout.c.completed_at,
        )
        .where(
            planned_workout.c.athlete_id == athlete_id,
            planned_workout.c.local_date >= start_date,
            planned_workout.c.local_date <= end_date,
        )
        .order_by(planned_workout.c.local_date)
    ).fetchall()
    activities = activities_by_local_date(conn, athlete_id, start_date, end_date)
    return [
        PlannedWorkoutListItemOut(
            local_date=r.local_date,
            id=r.id,
            sport=r.sport,
            name=r.name,
            scheduled_time=r.scheduled_time,
            push_status=r.push_status,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            matched_activity_id=matching_activity_id(activities.get(r.local_date, []), r.sport),
        )
        for r in rows
    ]


def _steps_in_to_planned_step_like(steps: list[PlannedWorkoutStepIn]) -> list[PlannedStepLike]:
    """Converts the exercise picker's own request shape into the plain dataclass
    `save_planned_workout` stores -- pace/HR-only fields (target_type/target_low/target_high/
    target_hr_zone/cadence_low/cadence_high, duration_distance_m) never apply to a hiit/
    strength_training step, so they're always None here."""
    return [
        PlannedStepLike(
            step_index=s.step_index,
            duration_type=s.duration_type,
            duration_time_s=s.duration_time_s,
            duration_distance_m=None,
            target_type=None,
            target_low=None,
            target_high=None,
            target_hr_zone=None,
            cadence_low=None,
            cadence_high=None,
            intensity=s.intensity,
            repeat_from_step=s.repeat_from_step,
            repeat_count=s.repeat_count,
            duration_reps=s.duration_reps,
            exercise_category=s.exercise_category,
            exercise_name=s.exercise_name,
            weight_kg=s.weight_kg,
            comment=s.comment,
        )
        for s in steps
    ]


def _fetch_running_load_thresholds(
    conn: Connection, athlete_id: str
) -> tuple[float | None, float | None, float | None, float | None]:
    """(threshold_pace_sec_per_km, threshold_hr_bpm, max_hr_bpm, resting_hr_bpm) -- `None` for
    any value the athlete hasn't configured yet, same "one row, all-null means not configured"
    contract both `athlete_running_load_config`/`athlete_hr_zone_config` already use elsewhere
    (settings.py)."""
    load_row = conn.execute(
        select(athlete_running_load_config.c.threshold_pace_sec_per_km).where(
            athlete_running_load_config.c.athlete_id == athlete_id
        )
    ).fetchone()
    hr_row = conn.execute(
        select(
            athlete_hr_zone_config.c.threshold_hr_bpm,
            athlete_hr_zone_config.c.max_hr_bpm,
            athlete_hr_zone_config.c.resting_hr_bpm,
        ).where(athlete_hr_zone_config.c.athlete_id == athlete_id)
    ).fetchone()
    return (
        load_row.threshold_pace_sec_per_km if load_row is not None else None,
        hr_row.threshold_hr_bpm if hr_row is not None else None,
        hr_row.max_hr_bpm if hr_row is not None else None,
        hr_row.resting_hr_bpm if hr_row is not None else None,
    )


def _running_estimate(
    conn: Connection, athlete_id: str, sport: str, parsed_steps: list[ParsedStep]
) -> WorkoutEstimate | None:
    """`None` for every sport but running (planned_workout_stats.py is running-only -- no
    pace/HR targets exist for yoga/bouldering/hiit/strength_training to build a zone/load
    estimate from)."""
    if sport != "running":
        return None
    threshold_pace, threshold_hr, max_hr, resting_hr = _fetch_running_load_thresholds(
        conn, athlete_id
    )
    return estimate_workout(
        parsed_steps,
        threshold_pace_sec_per_km=threshold_pace,
        threshold_hr_bpm=threshold_hr,
        max_hr_bpm=max_hr,
        resting_hr_bpm=resting_hr,
    )


def _to_out(
    row: Row,  # type: ignore[type-arg]
    steps: list[Row],  # type: ignore[type-arg]
    parse_errors: list[ParseError],
    estimate: WorkoutEstimate | None = None,
    matched_activity_id: str | None = None,
) -> PlannedWorkoutOut:
    return PlannedWorkoutOut(
        available=True,
        id=row.id,
        local_date=row.local_date,
        sport=row.sport,
        name=row.name,
        source_text=row.source_text,
        scheduled_time=row.scheduled_time,
        comment=row.comment,
        estimated_duration_s=row.estimated_duration_s,
        steps=[
            PlannedWorkoutStepOut(
                step_index=s.step_index,
                duration_type=s.duration_type,
                duration_time_s=s.duration_time_s,
                duration_distance_m=s.duration_distance_m,
                target_type=s.target_type,
                target_low=s.target_low,
                target_high=s.target_high,
                target_hr_zone=s.target_hr_zone,
                cadence_low=s.cadence_low,
                cadence_high=s.cadence_high,
                intensity=s.intensity,
                repeat_from_step=s.repeat_from_step,
                repeat_count=s.repeat_count,
                duration_reps=s.duration_reps,
                exercise_category=s.exercise_category,
                exercise_name=s.exercise_name,
                weight_kg=s.weight_kg,
                comment=s.comment,
            )
            for s in steps
        ],
        parse_errors=[ParseErrorOut(line_no=e.line_no, message=e.message) for e in parse_errors],
        push_status=row.push_status,
        push_error=row.push_error,
        garmin_workout_id=row.garmin_workout_id,
        garmin_scheduled_at=row.garmin_scheduled_at.isoformat()
        if row.garmin_scheduled_at
        else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
        matched_activity_id=matched_activity_id,
        estimated_distance_m=estimate.distance_m if estimate is not None else None,
        estimated_load=estimate.load if estimate is not None else None,
        segments=[
            PlannedWorkoutSegmentOut(
                duration_s=s.duration_s, zone=s.zone, intensity_factor=s.intensity_factor
            )
            for s in estimate.segments
        ]
        if estimate is not None
        else [],
    )


def _fetch_full_out(conn: Connection, athlete_id: str, workout_id: int) -> PlannedWorkoutOut:
    """Shared by every id-keyed GET (direct fetch, and the response of POST/PUT once saved) --
    fetches the row + its steps + current parse errors and assembles one PlannedWorkoutOut.
    404s if `workout_id` doesn't exist or belongs to a different athlete."""
    row = conn.execute(
        select(planned_workout).where(
            planned_workout.c.id == workout_id, planned_workout.c.athlete_id == athlete_id
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="planned workout not found")

    steps = conn.execute(
        select(planned_workout_step)
        .where(planned_workout_step.c.planned_workout_id == row.id)
        .order_by(planned_workout_step.c.step_index)
    ).fetchall()
    # The stored source_text is re-parsed here purely to surface any parse errors it currently
    # has (e.g. from direct DB manipulation, or a future stricter parser version) -- the actual
    # steps served are always the already-parsed/stored planned_workout_step rows, not a fresh
    # parse, so this never risks the response drifting from what a push would actually send. The
    # same parse also feeds the distance/duration/load estimate below (one parse, two uses).
    # Running only: yoga/bouldering's source_text is freeform athlete notes (PLACEHOLDER_SPORTS,
    # planned_workouts.py), never workout syntax -- parsing it here would surface bogus
    # "unrecognized duration" errors for plain prose.
    parsed = parse_workout_syntax(row.source_text or "") if row.sport == "running" else None
    parse_errors = parsed.errors if parsed is not None else []
    estimate = (
        _running_estimate(conn, athlete_id, row.sport, parsed.steps) if parsed is not None else None
    )
    activities = activities_by_local_date(conn, athlete_id, row.local_date, row.local_date)
    matched = matching_activity_id(activities.get(row.local_date, []), row.sport)
    return _to_out(row, list(steps), list(parse_errors), estimate, matched)


@router.post("/planned-workouts")
def post_planned_workout(
    payload: PlannedWorkoutCreateIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> PlannedWorkoutOut:
    saved = save_planned_workout(
        conn,
        athlete_id=athlete_id,
        local_date=payload.local_date,
        sport=payload.sport,
        name=payload.name,
        source_text=payload.source_text,
        scheduled_time=payload.scheduled_time,
        duration_minutes=payload.duration_minutes,
        steps=_steps_in_to_planned_step_like(payload.steps) if payload.steps else None,
        comment=payload.comment,
    )
    conn.commit()
    return _fetch_full_out(conn, athlete_id, saved.id)


@router.get("/planned-workouts/by-date/{local_date}")
def list_planned_workouts_for_date(
    local_date: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[PlannedWorkoutOut]:
    """Every workout scheduled on one date -- 0, 1, or many, now that a day isn't capped at one.
    Ordered by scheduled_time (nulls last) then id, the same order the calendar's own day
    panel/month cell display them in."""
    rows = conn.execute(
        select(planned_workout)
        .where(
            planned_workout.c.athlete_id == athlete_id, planned_workout.c.local_date == local_date
        )
        .order_by(
            planned_workout.c.scheduled_time.is_(None),
            planned_workout.c.scheduled_time,
            planned_workout.c.id,
        )
    ).fetchall()

    activities = activities_by_local_date(conn, athlete_id, local_date, local_date)
    activities_that_day = activities.get(local_date, [])
    out: list[PlannedWorkoutOut] = []
    for row in rows:
        steps = conn.execute(
            select(planned_workout_step)
            .where(planned_workout_step.c.planned_workout_id == row.id)
            .order_by(planned_workout_step.c.step_index)
        ).fetchall()
        parsed = parse_workout_syntax(row.source_text or "") if row.sport == "running" else None
        parse_errors = parsed.errors if parsed is not None else []
        estimate = (
            _running_estimate(conn, athlete_id, row.sport, parsed.steps)
            if parsed is not None
            else None
        )
        matched = matching_activity_id(activities_that_day, row.sport)
        out.append(_to_out(row, list(steps), list(parse_errors), estimate, matched))
    return out


@router.get("/planned-workouts/{workout_id}")
def get_planned_workout(
    workout_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> PlannedWorkoutOut:
    return _fetch_full_out(conn, athlete_id, workout_id)


@router.put("/planned-workouts/{workout_id}")
def put_planned_workout(
    workout_id: int,
    payload: PlannedWorkoutIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> PlannedWorkoutOut:
    existing = conn.execute(
        select(planned_workout.c.local_date).where(
            planned_workout.c.id == workout_id, planned_workout.c.athlete_id == athlete_id
        )
    ).fetchone()
    if existing is None:
        raise HTTPException(status_code=404, detail="planned workout not found")

    save_planned_workout(
        conn,
        athlete_id=athlete_id,
        local_date=existing.local_date,
        sport=payload.sport,
        name=payload.name,
        source_text=payload.source_text,
        scheduled_time=payload.scheduled_time,
        duration_minutes=payload.duration_minutes,
        steps=_steps_in_to_planned_step_like(payload.steps) if payload.steps else None,
        workout_id=workout_id,
        comment=payload.comment,
    )
    conn.commit()
    return _fetch_full_out(conn, athlete_id, workout_id)


@router.delete("/planned-workouts/{workout_id}")
def delete_planned_workout(
    workout_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
) -> None:
    row = conn.execute(
        select(planned_workout.c.id, planned_workout.c.garmin_workout_id).where(
            planned_workout.c.id == workout_id, planned_workout.c.athlete_id == athlete_id
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="planned workout not found")

    if row.garmin_workout_id is not None:
        # Best-effort: never let a Garmin-side failure block deleting the local row -- an orphan
        # workout template left in the athlete's Garmin library is harmless clutter, unlike a
        # planned_workout row the athlete can no longer delete because Garmin is unreachable.
        try:
            rate_limiter = RateLimiter(
                settings.garmin_request_interval_s, settings.garmin_max_requests_per_hour
            )
            adapter = GarminConnectAdapter(
                settings.garmin_tokenstore_dir_for(athlete_id), rate_limiter
            )
            adapter.authenticate()
            adapter.delete_workout(row.garmin_workout_id)
        except Exception:
            logger.warning(
                "best-effort Garmin workout delete failed for planned_workout %s "
                "(garmin_workout_id=%s) -- local row is still deleted",
                row.id,
                row.garmin_workout_id,
                exc_info=True,
            )

    conn.execute(
        planned_workout_step.delete().where(planned_workout_step.c.planned_workout_id == row.id)
    )
    conn.execute(planned_workout.delete().where(planned_workout.c.id == row.id))
    conn.commit()


def _set_completed(
    conn: Connection, athlete_id: str, workout_id: int, completed_at: datetime | None
) -> PlannedWorkoutOut:
    existing = conn.execute(
        select(planned_workout.c.id).where(
            planned_workout.c.id == workout_id, planned_workout.c.athlete_id == athlete_id
        )
    ).fetchone()
    if existing is None:
        raise HTTPException(status_code=404, detail="planned workout not found")
    conn.execute(
        planned_workout.update()
        .where(planned_workout.c.id == workout_id)
        .values(completed_at=completed_at)
    )
    conn.commit()
    return _fetch_full_out(conn, athlete_id, workout_id)


@router.post("/planned-workouts/{workout_id}/complete")
def post_complete_planned_workout(
    workout_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> PlannedWorkoutOut:
    """The athlete's own manual "I did this" marker -- entirely independent of push_status, so
    it works for a workout that was never pushed (or failed to push) at all: a manual session, a
    watch that didn't record, or just checking off the plan. Never linked to a recorded
    `activity` row here -- completion stays a separate, athlete-asserted fact this column alone
    tracks; see PlannedWorkoutOut.matched_activity_id for the read-time-only companion signal a
    synced Garmin activity provides instead, without ever touching this column. Idempotent:
    calling this again just refreshes completed_at to now."""
    now = datetime.now(UTC).replace(tzinfo=None)
    return _set_completed(conn, athlete_id, workout_id, now)


@router.post("/planned-workouts/{workout_id}/uncomplete")
def post_uncomplete_planned_workout(
    workout_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> PlannedWorkoutOut:
    return _set_completed(conn, athlete_id, workout_id, None)


@router.post("/planned-workouts/{workout_id}/push")
def post_push_planned_workout(
    workout_id: int,
    background_tasks: BackgroundTasks,
    athlete_id: Annotated[str, Depends(require_api_key)],
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
    engine: Engine = Depends(get_engine),
) -> JobTriggerOut:
    """Manual "Push now" override (the plan's own coming-week automatic push is
    `worker/main.py::run_daily_workout_push`) -- runs in the background, same pattern as
    `POST /settings/garmin/sync`; poll `GET /planned-workouts/{workout_id}` afterward for the
    updated `push_status`, since that status lives on the row itself, not a generic job log."""
    row = conn.execute(
        select(planned_workout.c.id).where(
            planned_workout.c.id == workout_id, planned_workout.c.athlete_id == athlete_id
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="planned workout not found")

    def _run() -> None:
        with engine.connect() as bg_conn:
            push_planned_workout(
                bg_conn,
                athlete_id=athlete_id,
                planned_workout_id=workout_id,
                tokenstore_dir=settings.garmin_tokenstore_dir_for(athlete_id),
                rate_limits=RateLimitSettings(
                    request_interval_s=settings.garmin_request_interval_s,
                    max_requests_per_hour=settings.garmin_max_requests_per_hour,
                ),
            )

    background_tasks.add_task(_run)
    return JobTriggerOut(triggered=True)


@router.post("/planned-workouts/recurring")
def post_recurring_planned_workout(
    payload: RecurringWorkoutIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> RecurringWorkoutOut:
    """Materializes one independent `planned_workout` row per occurrence date -- not a
    recurring-rule object (see docs/adr/0015-scheduled-workouts.md's own "Copy/paste ... and
    repeating a schedule" section). Always creates a new row, even on a date that already has a
    workout scheduled -- a day can hold more than one now, so there's nothing to skip."""
    try:
        start = date.fromisoformat(payload.local_date)
        until = date.fromisoformat(payload.until) if payload.until else None
        dates = compute_recurrence_dates(
            start,
            payload.frequency,
            interval_days=payload.interval_days,
            count=payload.count,
            until=until,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    created: list[str] = []
    for d in dates:
        iso = d.isoformat()
        save_planned_workout(
            conn,
            athlete_id=athlete_id,
            local_date=iso,
            sport=payload.sport,
            name=payload.name,
            source_text=payload.source_text,
            scheduled_time=payload.scheduled_time,
            duration_minutes=payload.duration_minutes,
            steps=_steps_in_to_planned_step_like(payload.steps) if payload.steps else None,
            comment=payload.comment,
        )
        created.append(iso)
    conn.commit()
    return RecurringWorkoutOut(created_dates=created)
