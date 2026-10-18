"""GET/POST/PUT/DELETE /goals -- distance goals for a calendar year, month or 7-day week, and their
progress lines. Any number may share a period, one per sport (or one for every sport combined),
like the duration goals. See api/schemas/goals.py for the request/response shapes,
db/schema.py::goal for storage, and goals.py for the progress computation itself.
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

_DUPLICATE = "a distance goal for this period and sport already exists"


def _to_goal_out(row: Row) -> GoalOut:  # type: ignore[type-arg]
    return GoalOut(
        id=row.id,
        period_type=row.period_type,
        period_start=row.period_start,
        sport=row.sport,
        target_distance_m=row.target_distance_m,
    )


def _validate_period(period_type: str, period_start: str) -> None:
    try:
        period_bounds(period_type, period_start)
    except InvalidPeriod as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


def _same_goal_id(
    conn: Connection,
    athlete_id: str,
    period_type: str,
    period_start: str,
    sport: str | None,
    exclude_id: int | None = None,
) -> int | None:
    """The id of the goal for the same period AND sport (or the every-sport goal), if there is one
    -- a different sport is a different goal."""
    query = select(goal_table.c.id).where(
        goal_table.c.athlete_id == athlete_id,
        goal_table.c.period_type == period_type,
        goal_table.c.period_start == period_start,
        goal_table.c.sport.is_(None) if sport is None else goal_table.c.sport == sport,
    )
    if exclude_id is not None:
        query = query.where(goal_table.c.id != exclude_id)
    return conn.execute(query).scalar_one_or_none()


@router.get("/goals")
def list_goals(
    period_type: Annotated[str, Query()],
    period_start: Annotated[str, Query()],
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[GoalProgressOut]:
    """Every distance goal set for exactly this period, each with its own progress line -- an empty
    list (never a fabricated goal) when none is set."""
    rows = conn.execute(
        select(goal_table)
        .where(
            goal_table.c.athlete_id == athlete_id,
            goal_table.c.period_type == period_type,
            goal_table.c.period_start == period_start,
        )
        .order_by(goal_table.c.sport.is_(None), goal_table.c.sport, goal_table.c.id)
    ).fetchall()

    out: list[GoalProgressOut] = []
    for row in rows:
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
            # router's own validated GoalIn, only via direct DB manipulation, but still surfaced
            # as a real error rather than silently swallowed.
            raise HTTPException(status_code=500, detail=str(e)) from e
        out.append(
            GoalProgressOut(
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
        )
    return out


def _insert_goal(
    conn: Connection, athlete_id: str, payload: GoalIn, period_start: str, now: datetime
) -> GoalOut:
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
    return GoalOut(
        id=result.inserted_primary_key[0],
        period_type=payload.period_type,
        period_start=period_start,
        sport=payload.sport,
        target_distance_m=payload.target_distance_m,
    )


def _upsert_goal(
    conn: Connection, athlete_id: str, payload: GoalIn, period_start: str, now: datetime
) -> GoalOut:
    """Insert, or replace, the goal for `(athlete, period_type, period_start, sport)` -- does not
    commit, so a repeat can write every week in one transaction."""
    existing_id = _same_goal_id(conn, athlete_id, payload.period_type, period_start, payload.sport)
    if existing_id is None:
        return _insert_goal(conn, athlete_id, payload, period_start, now)
    conn.execute(
        goal_table.update()
        .where(goal_table.c.id == existing_id)
        .values(target_distance_m=payload.target_distance_m, updated_at=now)
    )
    return GoalOut(
        id=existing_id,
        period_type=payload.period_type,
        period_start=period_start,
        sport=payload.sport,
        target_distance_m=payload.target_distance_m,
    )


@router.post("/goals", status_code=201)
def create_goal(
    payload: GoalIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> GoalOut:
    """A new distance goal; refused (409) when the period already has one for the same sport."""
    _validate_period(payload.period_type, payload.period_start)
    if (
        _same_goal_id(conn, athlete_id, payload.period_type, payload.period_start, payload.sport)
        is not None
    ):
        raise HTTPException(status_code=409, detail=_DUPLICATE)
    now = datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage
    out = _insert_goal(conn, athlete_id, payload, payload.period_start, now)
    conn.commit()
    return out


@router.put("/goals")
def set_goal(
    payload: GoalIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> GoalOut:
    """Set the goal for this period and sport: creates it, or replaces the target of the one that
    already exists (other sports' goals in the period are untouched)."""
    _validate_period(payload.period_type, payload.period_start)
    now = datetime.now(UTC).replace(tzinfo=None)
    out = _upsert_goal(conn, athlete_id, payload, payload.period_start, now)
    conn.commit()
    return out


@router.put("/goals/{goal_id}")
def update_goal(
    goal_id: int,
    payload: GoalIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> GoalOut:
    """Change one goal (its period, sport or target); 409 if that would duplicate another goal."""
    _validate_period(payload.period_type, payload.period_start)
    exists = conn.execute(
        select(goal_table.c.id).where(
            goal_table.c.id == goal_id, goal_table.c.athlete_id == athlete_id
        )
    ).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="goal not found")
    if (
        _same_goal_id(
            conn,
            athlete_id,
            payload.period_type,
            payload.period_start,
            payload.sport,
            exclude_id=goal_id,
        )
        is not None
    ):
        raise HTTPException(status_code=409, detail=_DUPLICATE)
    conn.execute(
        goal_table.update()
        .where(goal_table.c.id == goal_id)
        .values(
            period_type=payload.period_type,
            period_start=payload.period_start,
            sport=payload.sport,
            target_distance_m=payload.target_distance_m,
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    conn.commit()
    return GoalOut(id=goal_id, **payload.model_dump())


@router.post("/goals/repeat")
def repeat_weekly_goal(
    payload: GoalRepeatIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> GoalRepeatOut:
    """The same weekly distance goal for `weeks` consecutive weeks from `period_start`. Same
    upsert as PUT /goals per week: a week that already has a goal for this sport has it REPLACED
    by this one (other sports' goals are untouched); every week is written in one transaction
    (all or none)."""
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
