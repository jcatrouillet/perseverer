"""Bikes and tires: add bikes, choose defaults per cycling category, fit, swap, remove and retire
tires, and pick the bike of a ride.  Mileage is computed on read (perseverer.bikes)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, select
from ulid import ULID

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.bikes import (
    ActivityBikeIn,
    ActivityBikeOut,
    BikeIn,
    BikeOut,
    DefaultBikeIn,
    TireCreateIn,
    TireInstallIn,
    TireOut,
)
from perseverer.bikes import (
    CATEGORIES,
    Ledger,
    Ride,
    compute_ledger,
    load_defaults,
    resolve_bike,
)
from perseverer.db.schema import (
    activity,
    athlete_default_bike,
    bike,
    bike_tire,
    bike_tire_install,
)
from perseverer.merge.engine import sports_in_family
from perseverer.sport_override import set_bike_override

router = APIRouter()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage


def _at(day: date | None) -> datetime:
    return datetime.combine(day, time.min) if day is not None else _now()


def _bike_name(row: Any) -> str:
    return f"{row.brand} {row.model}"


def _open_installs(conn: Connection, athlete_id: str) -> list[Any]:
    return list(
        conn.execute(
            select(bike_tire_install).where(
                bike_tire_install.c.athlete_id == athlete_id,
                bike_tire_install.c.removed_at.is_(None),
            )
        ).fetchall()
    )


def _tire_out(row: Any, ledger: Ledger, install: Any | None, bikes: dict[str, Any]) -> TireOut:
    total_m = row.initial_distance_m + ledger.tire_distance_m.get(row.id, 0.0)
    owner = bikes.get(install.bike_id) if install is not None else None
    return TireOut(
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
        bike_id=install.bike_id if install is not None else None,
        bike_name=_bike_name(owner) if owner is not None else None,
        position=install.position if install is not None else None,
        installed_at=install.installed_at if install is not None else None,
        created_at=row.created_at,
    )


def _all_bikes(conn: Connection, athlete_id: str) -> dict[str, Any]:
    return {
        r.id: r
        for r in conn.execute(select(bike).where(bike.c.athlete_id == athlete_id)).fetchall()
    }


def _tire_outs(conn: Connection, athlete_id: str, include_retired: bool) -> list[TireOut]:
    ledger = compute_ledger(conn, athlete_id)
    bikes = _all_bikes(conn, athlete_id)
    installs = {i.tire_id: i for i in _open_installs(conn, athlete_id)}
    statement = select(bike_tire).where(bike_tire.c.athlete_id == athlete_id)
    if not include_retired:
        statement = statement.where(bike_tire.c.retired_at.is_(None))
    rows = conn.execute(statement.order_by(bike_tire.c.created_at.desc())).fetchall()
    return [_tire_out(r, ledger, installs.get(r.id), bikes) for r in rows]


def _bike_outs(conn: Connection, athlete_id: str, include_retired: bool) -> list[BikeOut]:
    ledger = compute_ledger(conn, athlete_id)
    bikes = _all_bikes(conn, athlete_id)
    installs = _open_installs(conn, athlete_id)
    tire_rows = {
        r.id: r for r in conn.execute(select(bike_tire).where(bike_tire.c.athlete_id == athlete_id))
    }
    by_slot: dict[tuple[str, str], TireOut] = {}
    for inst in installs:
        tire = tire_rows.get(inst.tire_id)
        if tire is not None and tire.retired_at is None:
            by_slot[(inst.bike_id, inst.position)] = _tire_out(tire, ledger, inst, bikes)
    rows = sorted(bikes.values(), key=lambda r: r.created_at, reverse=True)
    return [
        BikeOut(
            id=r.id,
            brand=r.brand,
            model=r.model,
            year=r.year,
            size=r.size,
            comments=r.comments,
            initial_distance_km=r.initial_distance_m / 1000,
            distance_km=(r.initial_distance_m + ledger.bike_distance_m.get(r.id, 0.0)) / 1000,
            retired=r.retired_at is not None,
            default_categories=ledger.default_categories.get(r.id, []),
            front_tire=by_slot.get((r.id, "front")),
            rear_tire=by_slot.get((r.id, "rear")),
            created_at=r.created_at,
        )
        for r in rows
        if include_retired or r.retired_at is None
    ]


def _one_bike(conn: Connection, athlete_id: str, bike_id: str) -> BikeOut:
    return next(b for b in _bike_outs(conn, athlete_id, True) if b.id == bike_id)


def _one_tire(conn: Connection, athlete_id: str, tire_id: str) -> TireOut:
    return next(t for t in _tire_outs(conn, athlete_id, True) if t.id == tire_id)


def _active_bike(conn: Connection, athlete_id: str, bike_id: str) -> Any:
    row = conn.execute(
        select(bike).where(
            bike.c.id == bike_id, bike.c.athlete_id == athlete_id, bike.c.retired_at.is_(None)
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="bike not found")
    return row


def _active_tire(conn: Connection, athlete_id: str, tire_id: str) -> Any:
    row = conn.execute(
        select(bike_tire).where(
            bike_tire.c.id == tire_id,
            bike_tire.c.athlete_id == athlete_id,
            bike_tire.c.retired_at.is_(None),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="tire not found")
    return row


def _close_install(conn: Connection, install: Any, at: datetime) -> None:
    if at < install.installed_at:
        raise HTTPException(
            status_code=422,
            detail="that date is before the tire it would replace was fitted",
        )
    conn.execute(
        bike_tire_install.update().where(bike_tire_install.c.id == install.id).values(removed_at=at)
    )


def _fit_tire(
    conn: Connection, athlete_id: str, tire_id: str, bike_id: str, position: str, at: datetime
) -> None:
    """Puts a tire on a bike: whatever it was fitted to before, and whatever tire currently sits
    in that slot, is taken off at `at`.  Does not commit."""
    for inst in _open_installs(conn, athlete_id):
        if inst.tire_id == tire_id or (inst.bike_id == bike_id and inst.position == position):
            _close_install(conn, inst, at)
    conn.execute(
        bike_tire_install.insert().values(
            athlete_id=athlete_id,
            tire_id=tire_id,
            bike_id=bike_id,
            position=position,
            installed_at=at,
        )
    )


@router.get("/gear/bikes")
def list_bikes(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    include_retired: bool = Query(default=False),
) -> list[BikeOut]:
    return _bike_outs(conn, athlete_id, include_retired)


@router.post("/gear/bikes", status_code=201)
def create_bike(
    payload: BikeIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> BikeOut:
    now = _now()
    bike_id = str(ULID())
    conn.execute(
        bike.insert().values(
            id=bike_id,
            athlete_id=athlete_id,
            brand=payload.brand.strip(),
            model=payload.model.strip(),
            year=payload.year,
            size=payload.size.strip() if payload.size else None,
            comments=payload.comments.strip() if payload.comments else None,
            initial_distance_m=payload.initial_distance_km * 1000,
            created_at=now,
            updated_at=now,
        )
    )
    conn.commit()
    return _one_bike(conn, athlete_id, bike_id)


@router.put("/gear/bikes/{bike_id}/retire")
def retire_bike(
    bike_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> BikeOut:
    """Retires a bike: it stops being any category's default and its tires come off (back in
    stock, not retired).  Its history stays."""
    row = conn.execute(
        select(bike).where(bike.c.id == bike_id, bike.c.athlete_id == athlete_id)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="bike not found")
    now = _now()
    for inst in _open_installs(conn, athlete_id):
        if inst.bike_id == bike_id:
            _close_install(conn, inst, max(now, inst.installed_at))
    conn.execute(athlete_default_bike.delete().where(athlete_default_bike.c.bike_id == bike_id))
    conn.execute(bike.update().where(bike.c.id == bike_id).values(retired_at=now, updated_at=now))
    conn.commit()
    return _one_bike(conn, athlete_id, bike_id)


@router.put("/gear/bike-defaults/{category}")
def set_default_bike(
    category: str,
    payload: DefaultBikeIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> BikeOut:
    """Makes a bike the default for a cycling category (`cycling` = every ride; or `road`,
    `mountain`, `gravel`, `ebike`, `emtb`, `commute`) from now on; never reassigns past rides."""
    if category not in CATEGORIES:
        raise HTTPException(status_code=422, detail=f"category must be one of {list(CATEGORIES)}")
    row = _active_bike(conn, athlete_id, payload.bike_id)
    existing = conn.execute(
        select(athlete_default_bike.c.bike_id).where(
            athlete_default_bike.c.athlete_id == athlete_id,
            athlete_default_bike.c.category == category,
        )
    ).scalar_one_or_none()
    now = _now()
    if existing is None:
        conn.execute(
            athlete_default_bike.insert().values(
                athlete_id=athlete_id, category=category, bike_id=row.id, assigned_at=now
            )
        )
    elif existing != row.id:
        conn.execute(
            athlete_default_bike.update()
            .where(
                athlete_default_bike.c.athlete_id == athlete_id,
                athlete_default_bike.c.category == category,
            )
            .values(bike_id=row.id, assigned_at=now)
        )
    conn.commit()
    return _one_bike(conn, athlete_id, row.id)


@router.delete("/gear/bike-defaults/{category}")
def clear_default_bike(
    category: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> None:
    """Stops using a bike for a category by default (rides without their own choice then fall to a
    more general default, or to no bike)."""
    result = conn.execute(
        athlete_default_bike.delete().where(
            athlete_default_bike.c.athlete_id == athlete_id,
            athlete_default_bike.c.category == category,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="no default for that category")
    conn.commit()


@router.get("/gear/tires")
def list_tires(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    include_retired: bool = Query(default=False),
) -> list[TireOut]:
    """Every tire with its live distance and where it is now (on a bike, or in stock)."""
    return _tire_outs(conn, athlete_id, include_retired)


@router.post("/gear/tires", status_code=201)
def create_tire(
    payload: TireCreateIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> TireOut:
    """Adds a tire; with `bike_id` and `position` it is fitted at once, replacing the tire that
    was in that slot."""
    if (payload.bike_id is None) != (payload.position is None):
        raise HTTPException(status_code=422, detail="bike_id and position go together")
    if payload.bike_id is not None:
        _active_bike(conn, athlete_id, payload.bike_id)
    now = _now()
    tire_id = str(ULID())
    conn.execute(
        bike_tire.insert().values(
            id=tire_id,
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
    if payload.bike_id is not None and payload.position is not None:
        _fit_tire(
            conn, athlete_id, tire_id, payload.bike_id, payload.position, _at(payload.installed_on)
        )
    conn.commit()
    return _one_tire(conn, athlete_id, tire_id)


@router.put("/gear/tires/{tire_id}/install")
def install_tire(
    tire_id: str,
    payload: TireInstallIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> TireOut:
    """Makes the tire active on a bike, front or rear: it comes off wherever it was, and the tire
    that was in that slot comes off the bike (it stays in stock, with its distance)."""
    _active_tire(conn, athlete_id, tire_id)
    _active_bike(conn, athlete_id, payload.bike_id)
    _fit_tire(
        conn, athlete_id, tire_id, payload.bike_id, payload.position, _at(payload.installed_on)
    )
    conn.commit()
    return _one_tire(conn, athlete_id, tire_id)


@router.put("/gear/tires/{tire_id}/remove")
def remove_tire(
    tire_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> TireOut:
    """Takes the tire off its bike without retiring it (it can be fitted again later)."""
    _active_tire(conn, athlete_id, tire_id)
    now = _now()
    for inst in _open_installs(conn, athlete_id):
        if inst.tire_id == tire_id:
            _close_install(conn, inst, max(now, inst.installed_at))
    conn.commit()
    return _one_tire(conn, athlete_id, tire_id)


@router.put("/gear/tires/{tire_id}/retire")
def retire_tire(
    tire_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> TireOut:
    """Retires a tire for good: it comes off its bike, stops alerting, and can't be fitted again.
    Its history stays."""
    row = conn.execute(
        select(bike_tire).where(bike_tire.c.id == tire_id, bike_tire.c.athlete_id == athlete_id)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="tire not found")
    now = _now()
    for inst in _open_installs(conn, athlete_id):
        if inst.tire_id == tire_id:
            _close_install(conn, inst, max(now, inst.installed_at))
    conn.execute(
        bike_tire.update().where(bike_tire.c.id == tire_id).values(retired_at=now, updated_at=now)
    )
    conn.commit()
    return _one_tire(conn, athlete_id, tire_id)


def _ride_row(conn: Connection, athlete_id: str, activity_id: str) -> Any:
    row = conn.execute(
        select(
            activity.c.id,
            activity.c.sport,
            activity.c.sub_sport,
            activity.c.is_commute,
            activity.c.bike_id,
            activity.c.start_time_utc,
            activity.c.distance_m,
        ).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    if row.sport not in sports_in_family("ride"):
        raise HTTPException(status_code=422, detail="Only cycling activities can have a bike")
    return row


def _resolved(conn: Connection, athlete_id: str, row: Any) -> ActivityBikeOut:
    ride = Ride(
        row.id,
        row.sport,
        row.sub_sport,
        bool(row.is_commute),
        row.bike_id,
        row.start_time_utc,
        float(row.distance_m or 0.0),
    )
    bike_id, is_default = resolve_bike(ride, load_defaults(conn, athlete_id))
    return ActivityBikeOut(bike_id=bike_id, is_default=is_default)


@router.get("/gear/activities/{activity_id}/bike")
def get_activity_bike(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivityBikeOut:
    return _resolved(conn, athlete_id, _ride_row(conn, athlete_id, activity_id))


@router.put("/gear/activities/{activity_id}/bike")
def set_activity_bike(
    activity_id: str,
    payload: ActivityBikeIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivityBikeOut:
    """The bike this one ride was made on; `bike_id: null` clears the choice (the category default
    applies again).  Durable: re-applied after a rebuild."""
    _ride_row(conn, athlete_id, activity_id)
    if payload.bike_id is not None:
        _active_bike(conn, athlete_id, payload.bike_id)
    set_bike_override(conn, athlete_id=athlete_id, activity_id=activity_id, bike_id=payload.bike_id)
    conn.commit()
    return _resolved(conn, athlete_id, _ride_row(conn, athlete_id, activity_id))
