"""GET/POST/PUT/DELETE /bouldering-goals -- targets for a number of completed bouldering routes in
a week, month or year, several of which may share one period. See api/schemas/bouldering_goals.py
for the shapes, db/schema.py::bouldering_goal for storage, and bouldering_goals.py for progress.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, Row, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.bouldering_goals import (
    BoulderingGoalIn,
    BoulderingGoalOut,
    BoulderingGoalProgressOut,
    BoulderingGoalProgressPoint,
    BoulderingGoalRepeatIn,
    BoulderingGoalRepeatOut,
)
from perseverer.bouldering_goals import compute_progress, period_bounds
from perseverer.db.schema import bouldering_goal as goal_table
from perseverer.goals import InvalidPeriod, repeated_week_starts

router = APIRouter()


def _to_out(row: Row) -> BoulderingGoalOut:  # type: ignore[type-arg]
    return BoulderingGoalOut(
        id=row.id,
        period_type=row.period_type,
        period_start=row.period_start,
        grade=row.grade,
        and_harder=bool(row.and_harder),
        target_count=row.target_count,
    )


def _validate_period(payload: BoulderingGoalIn) -> None:
    try:
        period_bounds(payload.period_type, payload.period_start)
    except InvalidPeriod as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


def _duplicate_exists(
    conn: Connection, athlete_id: str, payload: BoulderingGoalIn, exclude_id: int | None
) -> bool:
    """An identical goal (same period, grade and "or harder") already exists -- a second copy
    could only ever double-count the same routes, so it is refused. A DIFFERENT target for the
    same scope is the same goal edited, not a second goal."""
    query = select(goal_table.c.id).where(
        goal_table.c.athlete_id == athlete_id,
        goal_table.c.period_type == payload.period_type,
        goal_table.c.period_start == payload.period_start,
        goal_table.c.and_harder == payload.and_harder,
        goal_table.c.grade.is_(None)
        if payload.grade is None
        else goal_table.c.grade == payload.grade,
    )
    if exclude_id is not None:
        query = query.where(goal_table.c.id != exclude_id)
    return conn.execute(query).first() is not None


@router.get("/bouldering-goals")
def list_bouldering_goals(
    period_type: Annotated[str, Query()],
    period_start: Annotated[str, Query()],
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[BoulderingGoalProgressOut]:
    """Every bouldering goal set for exactly this period, each with its own progress line -- an
    empty list (never a fabricated goal) when none is set."""
    rows = conn.execute(
        select(goal_table)
        .where(
            goal_table.c.athlete_id == athlete_id,
            goal_table.c.period_type == period_type,
            goal_table.c.period_start == period_start,
        )
        .order_by(goal_table.c.grade.is_(None), goal_table.c.grade, goal_table.c.id)
    ).fetchall()

    out: list[BoulderingGoalProgressOut] = []
    for row in rows:
        try:
            progress = compute_progress(
                conn,
                athlete_id=athlete_id,
                period_type=row.period_type,
                period_start=row.period_start,
                grade=row.grade,
                and_harder=bool(row.and_harder),
                target_count=row.target_count,
            )
        except InvalidPeriod as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        out.append(
            BoulderingGoalProgressOut(
                goal=_to_out(row),
                period_end=progress.period_end,
                daily=[
                    BoulderingGoalProgressPoint(
                        local_date=p.local_date, cumulative_count=p.cumulative_count
                    )
                    for p in progress.daily
                ],
                target_per_day=progress.target_per_day,
                current_count=progress.current_count,
                target_as_of_today=progress.target_as_of_today,
                ahead_behind=progress.ahead_behind,
                pct_complete=progress.pct_complete,
            )
        )
    return out


@router.post("/bouldering-goals", status_code=201)
def create_bouldering_goal(
    payload: BoulderingGoalIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> BoulderingGoalOut:
    _validate_period(payload)
    if _duplicate_exists(conn, athlete_id, payload, exclude_id=None):
        raise HTTPException(
            status_code=409,
            detail="a goal for this period, grade and 'or harder' setting already exists",
        )
    now = datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage (ADR 0002)
    result = conn.execute(
        goal_table.insert().values(
            athlete_id=athlete_id,
            period_type=payload.period_type,
            period_start=payload.period_start,
            grade=payload.grade,
            and_harder=payload.and_harder,
            target_count=payload.target_count,
            created_at=now,
            updated_at=now,
        )
    )
    conn.commit()
    assert result.inserted_primary_key is not None
    return BoulderingGoalOut(id=result.inserted_primary_key[0], **payload.model_dump())


@router.post("/bouldering-goals/repeat", status_code=201)
def repeat_bouldering_goal(
    payload: BoulderingGoalRepeatIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> BoulderingGoalRepeatOut:
    """The same weekly bouldering goal for `weeks` consecutive weeks from `period_start`. Unlike a
    single create, a week that already holds an identical goal is skipped (reported in
    `skipped_period_starts`), not an error -- repeating a goal over a stretch that partly overlaps
    an earlier one is the normal case. Every new row is written in one transaction."""
    try:
        starts = repeated_week_starts(payload.period_start, payload.weeks)
    except InvalidPeriod as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    now = datetime.now(UTC).replace(tzinfo=None)
    created: list[BoulderingGoalOut] = []
    skipped: list[str] = []
    for start in starts:
        week = BoulderingGoalIn(
            period_type="week",
            period_start=start,
            grade=payload.grade,
            and_harder=payload.and_harder,
            target_count=payload.target_count,
        )
        if _duplicate_exists(conn, athlete_id, week, exclude_id=None):
            skipped.append(start)
            continue
        result = conn.execute(
            goal_table.insert().values(
                athlete_id=athlete_id,
                period_type="week",
                period_start=start,
                grade=week.grade,
                and_harder=week.and_harder,
                target_count=week.target_count,
                created_at=now,
                updated_at=now,
            )
        )
        assert result.inserted_primary_key is not None
        created.append(BoulderingGoalOut(id=result.inserted_primary_key[0], **week.model_dump()))
    conn.commit()
    return BoulderingGoalRepeatOut(created=created, skipped_period_starts=skipped)


@router.put("/bouldering-goals/{goal_id}")
def update_bouldering_goal(
    goal_id: int,
    payload: BoulderingGoalIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> BoulderingGoalOut:
    _validate_period(payload)
    exists = conn.execute(
        select(goal_table.c.id).where(
            goal_table.c.id == goal_id, goal_table.c.athlete_id == athlete_id
        )
    ).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="goal not found")
    if _duplicate_exists(conn, athlete_id, payload, exclude_id=goal_id):
        raise HTTPException(
            status_code=409,
            detail="a goal for this period, grade and 'or harder' setting already exists",
        )
    conn.execute(
        goal_table.update()
        .where(goal_table.c.id == goal_id)
        .values(
            period_type=payload.period_type,
            period_start=payload.period_start,
            grade=payload.grade,
            and_harder=payload.and_harder,
            target_count=payload.target_count,
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    conn.commit()
    return BoulderingGoalOut(id=goal_id, **payload.model_dump())


@router.delete("/bouldering-goals/{goal_id}")
def delete_bouldering_goal(
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
