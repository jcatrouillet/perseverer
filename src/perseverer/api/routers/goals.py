"""GET/PUT/DELETE /goals -- a distance goal for a whole calendar year or month, and its progress
line. See api/schemas/goals.py for the request/response shapes, db/schema.py::goal for storage,
and goals.py for the progress computation itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, Row, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.goals import (
    GoalIn,
    GoalOut,
    GoalProgressOut,
    GoalProgressPoint,
    GoalRepeatIn,
    GoalRepeatOut,
)
from perseverer.db.schema import goal as goal_table
from perseverer.goals import InvalidPeriod, compute_progress, period_bounds, repeated_week_starts

router = APIRouter()


def _to_goal_out(row: Row) -> GoalOut:  # type: ignore[type-arg]
    return GoalOut(
        id=row.id,
        period_type=row.period_type,
        period_start=row.period_start,
        sport=row.sport,
        target_distance_m=row.target_distance_m,
    )


@router.get("/goals")
def get_goal_progress(
    period_type: Annotated[str, Query()],
    period_start: Annotated[str, Query()],
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> GoalProgressOut:
    row = conn.execute(
        select(goal_table).where(
            goal_table.c.athlete_id == athlete_id,
            goal_table.c.period_type == period_type,
            goal_table.c.period_start == period_start,
        )
    ).fetchone()
    if row is None:
        return GoalProgressOut(available=False)

    try:
        progress = compute_progress(
            conn,
            athlete_id=athlete_id,
            period_type=row.period_type,
            period_start=row.period_start,
            sport=row.sport,
            target_distance_m=row.target_distance_m,
        )
    except InvalidPeriod as e:
        # A row already in the DB failed to parse as a real period -- can't happen via this
        # router's own validated GoalIn, only via direct DB manipulation, but still surfaced as
        # a real error rather than silently swallowed.
        raise HTTPException(status_code=500, detail=str(e)) from e

    return GoalProgressOut(
        available=True,
        goal=_to_goal_out(row),
        period_end=progress.period_end,
        daily=[
            GoalProgressPoint(
                local_date=p.local_date, cumulative_distance_m=p.cumulative_distance_m
            )
            for p in progress.daily
        ],
        target_per_day_m=progress.target_per_day_m,
        current_distance_m=progress.current_distance_m,
        target_distance_as_of_today_m=progress.target_distance_as_of_today_m,
        ahead_behind_m=progress.ahead_behind_m,
        pct_complete=progress.pct_complete,
    )


def _upsert_goal(
    conn: Connection, athlete_id: str, payload: GoalIn, period_start: str, now: datetime
) -> GoalOut:
    """Insert, or replace, the one goal for `(athlete, period_type, period_start)` -- does not
    commit, so a repeat can write every week in one transaction."""
    existing_id = conn.execute(
        select(goal_table.c.id).where(
            goal_table.c.athlete_id == athlete_id,
            goal_table.c.period_type == payload.period_type,
            goal_table.c.period_start == period_start,
        )
    ).scalar_one_or_none()

    if existing_id is None:
        result = conn.execute(
            goal_table.insert().values(
                athlete_id=athlete_id,
                period_type=payload.period_type,
                period_start=period_start,
                sport=payload.sport,
                target_distance_m=payload.target_distance_m,
                created_at=now,
                updated_at=now,
            )
        )
        assert result.inserted_primary_key is not None
        goal_id = result.inserted_primary_key[0]
    else:
        conn.execute(
            goal_table.update()
            .where(goal_table.c.id == existing_id)
            .values(
                sport=payload.sport,
                target_distance_m=payload.target_distance_m,
                updated_at=now,
            )
        )
        goal_id = existing_id

    return GoalOut(
        id=goal_id,
        period_type=payload.period_type,
        period_start=period_start,
        sport=payload.sport,
        target_distance_m=payload.target_distance_m,
    )


@router.put("/goals")
def set_goal(
    payload: GoalIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> GoalOut:
    try:
        period_bounds(payload.period_type, payload.period_start)
    except InvalidPeriod as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    now = datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage (ADR 0002)
    out = _upsert_goal(conn, athlete_id, payload, payload.period_start, now)
    conn.commit()
    return out


@router.post("/goals/repeat")
def repeat_weekly_goal(
    payload: GoalRepeatIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> GoalRepeatOut:
    """The same weekly distance goal for `weeks` consecutive weeks from `period_start`. Same
    upsert as PUT /goals per week: a week that already has a goal has it REPLACED by this one,
    every week is written in one transaction (all or none)."""
    try:
        starts = repeated_week_starts(payload.period_start, payload.weeks)
    except InvalidPeriod as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    now = datetime.now(UTC).replace(tzinfo=None)
    goals = [_upsert_goal(conn, athlete_id, payload, start, now) for start in starts]
    conn.commit()
    return GoalRepeatOut(goals=goals)


@router.delete("/goals/{goal_id}")
def delete_goal(
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
