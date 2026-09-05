"""GET/PUT/DELETE /planned-workouts/{local_date}, GET /planned-workouts (date-range list for the
calendar grid), POST /planned-workouts/{local_date}/push, POST /planned-workouts/recurring --
scheduled (future) workouts authored on the calendar and pushed to the Garmin watch. See
db/schema.py::planned_workout for the storage shape, workout_syntax.py for the text syntax, and
planned_workouts.py for the parse/save/push orchestration this router stays a thin layer over
(same balance goals.py/api/routers/goals.py already strikes).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import Connection, Row, select
from sqlalchemy.engine import Engine

from perseverer.adapters.garmin_connect import GarminConnectAdapter, RateLimiter, RateLimitSettings
from perseverer.api.dependencies import SettingsDep, get_conn, get_engine, require_api_key
from perseverer.api.schemas.planned_workouts import (
    ParseErrorOut,
    PlannedWorkoutIn,
    PlannedWorkoutListItemOut,
    PlannedWorkoutOut,
    PlannedWorkoutStepOut,
    RecurringWorkoutIn,
    RecurringWorkoutOut,
)
from perseverer.api.schemas.settings import JobTriggerOut
from perseverer.db.schema import planned_workout, planned_workout_step
from perseverer.planned_workouts import (
    compute_recurrence_dates,
    push_planned_workout,
    save_planned_workout,
)
from perseverer.workout_syntax import ParseError, parse_workout_syntax

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
        )
        .where(
            planned_workout.c.athlete_id == athlete_id,
            planned_workout.c.local_date >= start_date,
            planned_workout.c.local_date <= end_date,
        )
        .order_by(planned_workout.c.local_date)
    ).fetchall()
    return [
        PlannedWorkoutListItemOut(
            local_date=r.local_date,
            id=r.id,
            sport=r.sport,
            name=r.name,
            scheduled_time=r.scheduled_time,
            push_status=r.push_status,
        )
        for r in rows
    ]


def _to_out(row: Row, steps: list[Row], parse_errors: list[ParseError]) -> PlannedWorkoutOut:  # type: ignore[type-arg]
    return PlannedWorkoutOut(
        available=True,
        id=row.id,
        local_date=row.local_date,
        sport=row.sport,
        name=row.name,
        source_text=row.source_text,
        scheduled_time=row.scheduled_time,
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
    )


@router.get("/planned-workouts/{local_date}")
def get_planned_workout(
    local_date: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> PlannedWorkoutOut:
    row = conn.execute(
        select(planned_workout).where(
            planned_workout.c.athlete_id == athlete_id, planned_workout.c.local_date == local_date
        )
    ).fetchone()
    if row is None:
        return PlannedWorkoutOut(available=False)

    steps = conn.execute(
        select(planned_workout_step)
        .where(planned_workout_step.c.planned_workout_id == row.id)
        .order_by(planned_workout_step.c.step_index)
    ).fetchall()
    # The stored source_text is re-parsed here purely to surface any parse errors it currently
    # has (e.g. from direct DB manipulation, or a future stricter parser version) -- the actual
    # steps served are always the already-parsed/stored planned_workout_step rows, not a fresh
    # parse, so this never risks the response drifting from what a push would actually send.
    # Running only: yoga/bouldering's source_text is freeform athlete notes (PLACEHOLDER_SPORTS,
    # planned_workouts.py), never workout syntax -- parsing it here would surface bogus
    # "unrecognized duration" errors for plain prose.
    parse_errors = (
        parse_workout_syntax(row.source_text or "").errors if row.sport == "running" else []
    )
    return _to_out(row, list(steps), list(parse_errors))


@router.put("/planned-workouts/{local_date}")
def put_planned_workout(
    local_date: str,
    payload: PlannedWorkoutIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> PlannedWorkoutOut:
    save_planned_workout(
        conn,
        athlete_id=athlete_id,
        local_date=local_date,
        sport=payload.sport,
        name=payload.name,
        source_text=payload.source_text,
        scheduled_time=payload.scheduled_time,
        duration_minutes=payload.duration_minutes,
    )
    conn.commit()
    return get_planned_workout(local_date, athlete_id, conn)


@router.delete("/planned-workouts/{local_date}")
def delete_planned_workout(
    local_date: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
) -> None:
    row = conn.execute(
        select(planned_workout.c.id, planned_workout.c.garmin_workout_id).where(
            planned_workout.c.athlete_id == athlete_id, planned_workout.c.local_date == local_date
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
            adapter = GarminConnectAdapter(settings.garmin_tokenstore_dir, rate_limiter)
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


@router.post("/planned-workouts/{local_date}/push")
def post_push_planned_workout(
    local_date: str,
    background_tasks: BackgroundTasks,
    athlete_id: Annotated[str, Depends(require_api_key)],
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
    engine: Engine = Depends(get_engine),
) -> JobTriggerOut:
    """Manual "Push now" override (the plan's own coming-week automatic push is
    `worker/main.py::run_daily_workout_push`) -- runs in the background, same pattern as
    `POST /settings/garmin/sync`; poll `GET /planned-workouts/{date}` afterward for the updated
    `push_status`, since that status lives on the row itself, not a generic job log."""
    row = conn.execute(
        select(planned_workout.c.id).where(
            planned_workout.c.athlete_id == athlete_id, planned_workout.c.local_date == local_date
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="planned workout not found")
    workout_id = row.id

    def _run() -> None:
        with engine.connect() as bg_conn:
            push_planned_workout(
                bg_conn,
                athlete_id=athlete_id,
                planned_workout_id=workout_id,
                tokenstore_dir=settings.garmin_tokenstore_dir,
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
    repeating a schedule" section). A date that already has a planned workout is skipped, never
    overwritten -- reported back so the athlete can see which dates didn't get the new content."""
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

    existing_dates = set(
        conn.execute(
            select(planned_workout.c.local_date).where(
                planned_workout.c.athlete_id == athlete_id,
                planned_workout.c.local_date.in_([d.isoformat() for d in dates]),
            )
        ).scalars()
    )

    created: list[str] = []
    skipped: list[str] = []
    for d in dates:
        iso = d.isoformat()
        if iso in existing_dates:
            skipped.append(iso)
            continue
        save_planned_workout(
            conn,
            athlete_id=athlete_id,
            local_date=iso,
            sport=payload.sport,
            name=payload.name,
            source_text=payload.source_text,
            scheduled_time=payload.scheduled_time,
            duration_minutes=payload.duration_minutes,
        )
        created.append(iso)
    conn.commit()
    return RecurringWorkoutOut(created_dates=created, skipped_dates=skipped)
