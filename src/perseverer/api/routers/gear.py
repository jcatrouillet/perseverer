"""Shoes: create pairs, assign sport defaults, and expose live distance-limit alerts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, select
from ulid import ULID

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.gear import (
    ActivityShoeIn,
    ActivityShoeOut,
    DefaultShoeIn,
    GearAlertOut,
    ShoeIn,
    ShoeOut,
)
from perseverer.db.schema import activity, athlete_default_shoe, shoe
from perseverer.gear import over_limit_shoes, resolve_activity_shoe, shoe_mileages

router = APIRouter()


def _out(row: Any, mileage_m: float, sports: list[str]) -> ShoeOut:
    total_m = row.initial_distance_m + mileage_m
    return ShoeOut(
        id=row.id,
        brand=row.brand,
        model=row.model,
        size=row.size,
        comments=row.comments,
        initial_distance_km=row.initial_distance_m / 1000,
        max_distance_km=row.max_distance_m / 1000 if row.max_distance_m is not None else None,
        distance_km=total_m / 1000,
        remaining_km=max(0.0, (row.max_distance_m - total_m) / 1000)
        if row.max_distance_m is not None
        else None,
        over_limit=row.max_distance_m is not None and total_m >= row.max_distance_m,
        retired=row.retired_at is not None,
        default_sports=sorted(sports),
        created_at=row.created_at,
    )


@router.get("/gear/shoes")
def list_shoes(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    include_retired: bool = Query(default=False),
) -> list[ShoeOut]:
    miles = shoe_mileages(conn, athlete_id)
    statement = select(shoe).where(shoe.c.athlete_id == athlete_id)
    if not include_retired:
        statement = statement.where(shoe.c.retired_at.is_(None))
    rows = conn.execute(statement.order_by(shoe.c.created_at.desc())).fetchall()
    result: list[ShoeOut] = []
    for row in rows:
        mileage = miles.get(row.id)
        result.append(
            _out(
                row,
                mileage.distance_m if mileage else 0.0,
                mileage.default_sports if mileage else [],
            )
        )
    return result


@router.post("/gear/shoes", status_code=201)
def create_shoe(
    payload: ShoeIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ShoeOut:
    now = datetime.now(UTC).replace(tzinfo=None)
    shoe_id = str(ULID())
    conn.execute(
        shoe.insert().values(
            id=shoe_id,
            athlete_id=athlete_id,
            brand=payload.brand.strip(),
            model=payload.model.strip(),
            size=payload.size.strip() if payload.size else None,
            comments=payload.comments.strip() if payload.comments else None,
            initial_distance_m=payload.initial_distance_km * 1000,
            max_distance_m=payload.max_distance_km * 1000 if payload.max_distance_km else None,
            created_at=now,
            updated_at=now,
        )
    )
    conn.commit()
    row = conn.execute(select(shoe).where(shoe.c.id == shoe_id)).one()
    return _out(row, 0.0, [])


@router.put("/gear/defaults/{sport}")
def set_default_shoe(
    sport: str,
    payload: DefaultShoeIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ShoeOut:
    if not sport.strip():
        raise HTTPException(status_code=422, detail="sport must not be empty")
    row = conn.execute(
        select(shoe).where(
            shoe.c.id == payload.shoe_id,
            shoe.c.athlete_id == athlete_id,
            shoe.c.retired_at.is_(None),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="shoe not found")
    now = datetime.now(UTC).replace(tzinfo=None)
    existing = conn.execute(
        select(athlete_default_shoe.c.shoe_id).where(
            athlete_default_shoe.c.athlete_id == athlete_id, athlete_default_shoe.c.sport == sport
        )
    ).scalar_one_or_none()
    if existing is None:
        conn.execute(
            athlete_default_shoe.insert().values(
                athlete_id=athlete_id, sport=sport, shoe_id=row.id, assigned_at=now
            )
        )
    elif existing != row.id:
        conn.execute(
            athlete_default_shoe.update()
            .where(
                athlete_default_shoe.c.athlete_id == athlete_id,
                athlete_default_shoe.c.sport == sport,
            )
            .values(shoe_id=row.id, assigned_at=now)
        )
    conn.commit()
    miles = shoe_mileages(conn, athlete_id).get(row.id)
    return _out(row, miles.distance_m if miles else 0.0, miles.default_sports if miles else [])


@router.put("/gear/shoes/{shoe_id}/retire", response_model=ShoeOut)
def retire_shoe(
    shoe_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ShoeOut:
    row = conn.execute(
        select(shoe).where(shoe.c.id == shoe_id, shoe.c.athlete_id == athlete_id)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Shoe not found")
    now = datetime.now(UTC).replace(tzinfo=None)
    conn.execute(shoe.update().where(shoe.c.id == shoe_id).values(retired_at=now, updated_at=now))
    conn.execute(athlete_default_shoe.delete().where(athlete_default_shoe.c.shoe_id == shoe_id))
    conn.commit()
    mileage = shoe_mileages(conn, athlete_id).get(shoe_id)
    retired = conn.execute(select(shoe).where(shoe.c.id == shoe_id)).one()
    return _out(retired, mileage.distance_m if mileage else 0.0, [])


@router.get("/gear/alerts")
def gear_alerts(
    athlete_id: Annotated[str, Depends(require_api_key)], conn: Connection = Depends(get_conn)
) -> list[GearAlertOut]:
    return [
        GearAlertOut(
            shoe_id=row.id,
            brand=row.brand,
            model=row.model,
            distance_km=(row.initial_distance_m + mileage.distance_m) / 1000,
            max_distance_km=row.max_distance_m / 1000,
        )
        for row, mileage in over_limit_shoes(conn, athlete_id)
    ]


@router.get("/gear/activities/{activity_id}/shoe", response_model=ActivityShoeOut)
def get_activity_shoe(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivityShoeOut:
    row = conn.execute(
        select(activity.c.shoe_id, activity.c.sport, activity.c.start_time_utc).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    shoe_id, is_default = resolve_activity_shoe(
        conn, athlete_id, row.sport, row.start_time_utc, row.shoe_id
    )
    return ActivityShoeOut(shoe_id=shoe_id, is_default=is_default)


@router.put("/gear/activities/{activity_id}/shoe", response_model=ActivityShoeOut)
def set_activity_shoe(
    activity_id: str,
    payload: ActivityShoeIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivityShoeOut:
    target = conn.execute(
        select(activity.c.distance_m, activity.c.sport, activity.c.start_time_utc).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if target is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    if target.distance_m is None or target.distance_m <= 0:
        raise HTTPException(
            status_code=422, detail="Only activities with a distance can have shoes"
        )
    if (
        payload.shoe_id is not None
        and conn.execute(
            select(shoe.c.id).where(
                shoe.c.id == payload.shoe_id,
                shoe.c.athlete_id == athlete_id,
                shoe.c.retired_at.is_(None),
            )
        ).scalar_one_or_none()
        is None
    ):
        raise HTTPException(status_code=404, detail="Shoe not found")
    conn.execute(
        activity.update()
        .where(activity.c.id == activity_id)
        .values(shoe_id=payload.shoe_id, updated_at=datetime.now(UTC).replace(tzinfo=None))
    )
    conn.commit()
    # Clearing an explicit choice (payload.shoe_id is None) falls back to whatever the sport
    # default resolves to, same as a never-set activity -- the athlete shouldn't have to reload
    # the page to see the default reapply.
    shoe_id, is_default = resolve_activity_shoe(
        conn, athlete_id, target.sport, target.start_time_utc, payload.shoe_id
    )
    return ActivityShoeOut(shoe_id=shoe_id, is_default=is_default)
