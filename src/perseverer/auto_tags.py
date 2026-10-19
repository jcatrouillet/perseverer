"""Tags the app puts on activities by itself, so the athlete doesn't have to.

* **Commute** -- an activity that starts near the athlete's home and ends near their work, or the
  other way round (`athlete_commute_config`, Settings -> Commute), is tagged `is_commute`.  Both
  addresses are needed.  A start or end counts as "at" an address within `COMMUTE_RADIUS_M`.
* **Virtual** -- an activity with "Zwift" in its title is tagged `is_virtual`.

Neither ever beats the athlete: a tag the athlete set by hand (an `activity_sport_override` row
with that flag recorded, whether on or off) is left alone.  Every other activity is re-evaluated
on each run, in both directions, so changing an address or a title takes effect, and a vendor
never sets these two flags, so there is nothing else to protect.  Pure function of the database:
run after an import, a rebuild (after the athlete's corrections are re-applied), a rename, or a
change of addresses.  Does not commit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import Connection, func, select

from perseverer.db.schema import (
    activity,
    activity_sport_override,
    athlete_commute_config,
    route_geom,
)

# How close to an address a start or finish must be.  Wide enough for GPS drift and "the door to
# the building" vs "the street the address resolves to", narrow enough not to tag a ride that
# merely passes through the neighbourhood.
COMMUTE_RADIUS_M = 400.0
ZWIFT_WORD = "zwift"


@dataclass(frozen=True)
class AutoTagResult:
    commutes: int  # activities now tagged commute by detection
    virtual: int  # activities now tagged virtual by detection
    changed: int  # rows whose flag actually changed


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (haversine)."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _near(lat: float | None, lon: float | None, point: tuple[float, float] | None) -> bool:
    if lat is None or lon is None or point is None:
        return False
    return _distance_m(lat, lon, point[0], point[1]) <= COMMUTE_RADIUS_M


def is_commute_route(
    start: tuple[float | None, float | None],
    end: tuple[float | None, float | None],
    home: tuple[float, float] | None,
    work: tuple[float, float] | None,
) -> bool:
    """Home to work or work to home."""
    if home is None or work is None:
        return False
    return (_near(*start, home) and _near(*end, work)) or (
        _near(*start, work) and _near(*end, home)
    )


def is_zwift_title(name: str | None) -> bool:
    return bool(name) and ZWIFT_WORD in (name or "").lower()


def _points(conn: Connection, athlete_id: str) -> tuple[tuple[float, float] | None, ...]:
    row = conn.execute(
        select(athlete_commute_config).where(athlete_commute_config.c.athlete_id == athlete_id)
    ).fetchone()
    if row is None:
        return (None, None)
    home = (
        (row.home_lat, row.home_lon)
        if row.home_lat is not None and row.home_lon is not None
        else None
    )
    work = (
        (row.work_lat, row.work_lon)
        if row.work_lat is not None and row.work_lon is not None
        else None
    )
    return (home, work)


def apply_auto_tags(conn: Connection, *, athlete_id: str) -> AutoTagResult:
    home, work = _points(conn, athlete_id)
    rows = conn.execute(
        select(
            activity.c.id,
            activity.c.name,
            activity.c.is_commute,
            activity.c.is_virtual,
            route_geom.c.start_lat,
            route_geom.c.start_lng,
            route_geom.c.end_lat,
            route_geom.c.end_lng,
            activity_sport_override.c.is_commute.label("commute_override"),
            activity_sport_override.c.is_virtual.label("virtual_override"),
        )
        .select_from(
            activity.outerjoin(route_geom, route_geom.c.activity_id == activity.c.id).outerjoin(
                activity_sport_override,
                (activity_sport_override.c.athlete_id == activity.c.athlete_id)
                & (activity_sport_override.c.start_time_utc == activity.c.start_time_utc),
            )
        )
        .where(activity.c.athlete_id == athlete_id, activity.c.deleted_at.is_(None))
    ).fetchall()

    commutes = virtual = changed = 0
    for r in rows:
        values: dict[str, bool] = {}
        if r.commute_override is None:
            detected = is_commute_route(
                (r.start_lat, r.start_lng), (r.end_lat, r.end_lng), home, work
            )
            commutes += detected
            if bool(r.is_commute) != detected:
                values["is_commute"] = detected
        if r.virtual_override is None:
            detected = is_zwift_title(r.name)
            virtual += detected
            if bool(r.is_virtual) != detected:
                values["is_virtual"] = detected
        if values:
            conn.execute(activity.update().where(activity.c.id == r.id).values(**values))
            changed += 1
    return AutoTagResult(commutes=commutes, virtual=virtual, changed=changed)


def detected_commute_count(conn: Connection, athlete_id: str) -> int:
    """Activities currently tagged as a commute that the athlete didn't tag by hand."""
    return int(
        conn.execute(
            select(func.count())
            .select_from(
                activity.outerjoin(
                    activity_sport_override,
                    (activity_sport_override.c.athlete_id == activity.c.athlete_id)
                    & (activity_sport_override.c.start_time_utc == activity.c.start_time_utc),
                )
            )
            .where(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.is_commute.is_(True),
                activity_sport_override.c.is_commute.is_(None),
            )
        ).scalar_one()
    )
