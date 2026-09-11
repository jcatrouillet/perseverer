"""GET/POST/PUT/DELETE /planned-races -- a single upcoming race on the calendar. See
api/schemas/planned_races.py for the request/response shapes, db/schema.py::planned_race for
storage, and planned_races.py for the target-vs-predicted-finish-time lookup. Route shape
mirrors /planned-workouts (id-keyed, a `by-date` list route, a range route for the calendar
grid) -- see that router's own docstring for why: any number of rows per date, never one upsert
keyed off the date alone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, Row, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.planned_races import PlannedRaceIn, PlannedRaceOut
from perseverer.db.schema import planned_race
from perseverer.planned_races import predicted_duration_s_for_distance

router = APIRouter()


def _to_out(conn: Connection, athlete_id: str, row: Row) -> PlannedRaceOut:  # type: ignore[type-arg]
    today = datetime.now(UTC).date()
    return PlannedRaceOut(
        id=row.id,
        local_date=row.local_date,
        name=row.name,
        sport=row.sport,
        distance_m=row.distance_m,
        scheduled_time=row.scheduled_time,
        target_duration_s=row.target_duration_s,
        days_until=(date.fromisoformat(row.local_date) - today).days,
        predicted_duration_s=predicted_duration_s_for_distance(
            conn, athlete_id=athlete_id, distance_m=row.distance_m
        ),
    )


@router.get("/planned-races")
def list_planned_races(
    start_date: Annotated[str, Query()],
    end_date: Annotated[str, Query()],
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[PlannedRaceOut]:
    rows = conn.execute(
        select(planned_race)
        .where(
            planned_race.c.athlete_id == athlete_id,
            planned_race.c.local_date >= start_date,
            planned_race.c.local_date <= end_date,
        )
        .order_by(planned_race.c.local_date, planned_race.c.id)
    ).fetchall()
    return [_to_out(conn, athlete_id, row) for row in rows]


@router.get("/planned-races/by-date/{local_date}")
def list_planned_races_for_date(
    local_date: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[PlannedRaceOut]:
    rows = conn.execute(
        select(planned_race)
        .where(planned_race.c.athlete_id == athlete_id, planned_race.c.local_date == local_date)
        .order_by(
            planned_race.c.scheduled_time.is_(None),
            planned_race.c.scheduled_time,
            planned_race.c.id,
        )
    ).fetchall()
    return [_to_out(conn, athlete_id, row) for row in rows]


@router.get("/planned-races/{race_id}")
def get_planned_race(
    race_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> PlannedRaceOut:
    row = conn.execute(
        select(planned_race).where(
            planned_race.c.id == race_id, planned_race.c.athlete_id == athlete_id
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="race not found")
    return _to_out(conn, athlete_id, row)


@router.post("/planned-races")
def post_planned_race(
    payload: PlannedRaceIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> PlannedRaceOut:
    now = datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage (ADR 0002)
    result = conn.execute(
        planned_race.insert().values(
            athlete_id=athlete_id,
            local_date=payload.local_date,
            name=payload.name,
            sport=payload.sport,
            distance_m=payload.distance_m,
            scheduled_time=payload.scheduled_time,
            target_duration_s=payload.target_duration_s,
            created_at=now,
            updated_at=now,
        )
    )
    assert result.inserted_primary_key is not None
    race_id = result.inserted_primary_key[0]
    conn.commit()
    return get_planned_race(race_id, athlete_id, conn)


@router.put("/planned-races/{race_id}")
def put_planned_race(
    race_id: int,
    payload: PlannedRaceIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> PlannedRaceOut:
    existing = conn.execute(
        select(planned_race.c.id).where(
            planned_race.c.id == race_id, planned_race.c.athlete_id == athlete_id
        )
    ).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="race not found")

    conn.execute(
        planned_race.update()
        .where(planned_race.c.id == race_id)
        .values(
            local_date=payload.local_date,
            name=payload.name,
            sport=payload.sport,
            distance_m=payload.distance_m,
            scheduled_time=payload.scheduled_time,
            target_duration_s=payload.target_duration_s,
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    conn.commit()
    return get_planned_race(race_id, athlete_id, conn)


@router.delete("/planned-races/{race_id}")
def delete_planned_race(
    race_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> None:
    result = conn.execute(
        planned_race.delete().where(
            planned_race.c.id == race_id, planned_race.c.athlete_id == athlete_id
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="race not found")
    conn.commit()
