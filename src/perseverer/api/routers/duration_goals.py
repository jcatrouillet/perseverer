"""GET/POST/PUT/DELETE /duration-goals -- targets for the time spent on a sport (or every sport) in
a week, month or year; several may share one period, one per sport. See
api/schemas/duration_goals.py for the shapes, db/schema.py::duration_goal for storage and
duration_goals.py for progress.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, Row, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.duration_goals import (
    DurationGoalIn,
    DurationGoalOut,
    DurationGoalProgressOut,
    DurationGoalProgressPoint,
    DurationGoalRepeatIn,
    DurationGoalRepeatOut,
)
from perseverer.db.schema import duration_goal as goal_table
from perseverer.duration_goals import compute_progress
from perseverer.goals import InvalidPeriod, period_bounds, repeated_week_starts

router = APIRouter()

_DUPLICATE = "a duration goal for this period and sport already exists"


def _to_out(row: Row) -> DurationGoalOut:  # type: ignore[type-arg]
    return DurationGoalOut(
        id=row.id,
        period_type=row.period_type,
        period_start=row.period_start,
        sport=row.sport,
        target_duration_s=row.target_duration_s,
    )


def _validate_period(period_type: str, period_start: str) -> None:
    try:
        period_bounds(period_type, period_start)
    except InvalidPeriod as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


def _duplicate_exists(
    conn: Connection,
    athlete_id: str,
    period_type: str,
    period_start: str,
    sport: str | None,
    exclude_id: int | None,
) -> bool:
    """A goal for the same period AND sport (or two every-sport goals) already exists -- a second
    could only conflict with it. A different sport is a different goal."""
    query = select(goal_table.c.id).where(
        goal_table.c.athlete_id == athlete_id,
        goal_table.c.period_type == period_type,
        goal_table.c.period_start == period_start,
        goal_table.c.sport.is_(None) if sport is None else goal_table.c.sport == sport,
    )
    if exclude_id is not None:
        query = query.where(goal_table.c.id != exclude_id)
    return conn.execute(query).first() is not None


@router.get("/duration-goals")
def list_duration_goals(
    period_type: Annotated[str, Query()],
    period_start: Annotated[str, Query()],
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[DurationGoalProgressOut]:
    """Every duration goal set for exactly this period, each with its own progress line -- an
    empty list (never a fabricated goal) when none is set."""
    rows = conn.execute(
        select(goal_table)
        .where(
            goal_table.c.athlete_id == athlete_id,
            goal_table.c.period_type == period_type,
            goal_table.c.period_start == period_start,
        )
        .order_by(goal_table.c.sport.is_(None), goal_table.c.sport, goal_table.c.id)
    ).fetchall()

    out: list[DurationGoalProgressOut] = []
    for row in rows:
        try:
            progress = compute_progress(
                conn,
                athlete_id=athlete_id,
                period_type=row.period_type,
                period_start=row.period_start,
                sport=row.sport,
                target_duration_s=row.target_duration_s,
            )
        except InvalidPeriod as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        out.append(
            DurationGoalProgressOut(
                goal=_to_out(row),
                period_end=progress.period_end,
                daily=[
                    DurationGoalProgressPoint(
                        local_date=p.local_date, cumulative_duration_s=p.cumulative_duration_s
                    )
                    for p in progress.daily
                ],
                target_per_day_s=progress.target_per_day_s,
                current_duration_s=progress.current_duration_s,
                target_as_of_today_s=progress.target_as_of_today_s,
                ahead_behind_s=progress.ahead_behind_s,
                pct_complete=progress.pct_complete,
            )
        )
    return out


@router.post("/duration-goals", status_code=201)
def create_duration_goal(
    payload: DurationGoalIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> DurationGoalOut:
    _validate_period(payload.period_type, payload.period_start)
    if _duplicate_exists(
        conn, athlete_id, payload.period_type, payload.period_start, payload.sport, None
    ):
        raise HTTPException(status_code=409, detail=_DUPLICATE)
    now = datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage
    result = conn.execute(
        goal_table.insert().values(
            athlete_id=athlete_id,
            period_type=payload.period_type,
            period_start=payload.period_start,
            sport=payload.sport,
            target_duration_s=payload.target_duration_s,
            created_at=now,
            updated_at=now,
        )
    )
    conn.commit()
    assert result.inserted_primary_key is not None
    return DurationGoalOut(id=result.inserted_primary_key[0], **payload.model_dump())


@router.post("/duration-goals/repeat", status_code=201)
def repeat_duration_goal(
    payload: DurationGoalRepeatIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> DurationGoalRepeatOut:
    """The same weekly duration goal for `weeks` consecutive weeks from `period_start`. A week that
    already holds a goal for the same sport is skipped (reported in `skipped_period_starts`), not
    an error. Every new row is written in one transaction."""
    try:
        starts = repeated_week_starts(payload.period_start, payload.weeks)
    except InvalidPeriod as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    now = datetime.now(UTC).replace(tzinfo=None)
    created: list[DurationGoalOut] = []
    skipped: list[str] = []
    for start in starts:
        if _duplicate_exists(conn, athlete_id, "week", start, payload.sport, None):
            skipped.append(start)
            continue
        result = conn.execute(
            goal_table.insert().values(
                athlete_id=athlete_id,
                period_type="week",
                period_start=start,
                sport=payload.sport,
                target_duration_s=payload.target_duration_s,
                created_at=now,
                updated_at=now,
            )
        )
        assert result.inserted_primary_key is not None
        created.append(
            DurationGoalOut(
                id=result.inserted_primary_key[0],
                period_type="week",
                period_start=start,
                sport=payload.sport,
                target_duration_s=payload.target_duration_s,
            )
        )
    conn.commit()
    return DurationGoalRepeatOut(created=created, skipped_period_starts=skipped)


@router.put("/duration-goals/{goal_id}")
def update_duration_goal(
    goal_id: int,
    payload: DurationGoalIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> DurationGoalOut:
    _validate_period(payload.period_type, payload.period_start)
    exists = conn.execute(
        select(goal_table.c.id).where(
            goal_table.c.id == goal_id, goal_table.c.athlete_id == athlete_id
        )
    ).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="goal not found")
    if _duplicate_exists(
        conn, athlete_id, payload.period_type, payload.period_start, payload.sport, goal_id
    ):
        raise HTTPException(status_code=409, detail=_DUPLICATE)
    conn.execute(
        goal_table.update()
        .where(goal_table.c.id == goal_id)
        .values(
            period_type=payload.period_type,
            period_start=payload.period_start,
            sport=payload.sport,
            target_duration_s=payload.target_duration_s,
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    conn.commit()
    return DurationGoalOut(id=goal_id, **payload.model_dump())


@router.delete("/duration-goals/{goal_id}")
def delete_duration_goal(
    goal_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> None:
    result = conn.execute(
        goal_table.delete().where(goal_table.c.id == goal_id, goal_table.c.athlete_id == athlete_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="goal not found")
    conn.commit()
