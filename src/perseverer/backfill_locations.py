"""Pre-warms geocoding.py's activity_metric cache for every already-ingested, GPS-bearing
activity that doesn't have a location yet -- so in normal use, GET /activities/{id}/location
almost always answers from cache, and the background-fetch-on-miss path in
routers/activities.py only ever has to cover activities ingested *after* this last ran.

Groups activities by rounded start coordinate (3 decimal places, ~111m) before hitting
Nominatim: an athlete with hundreds of runs starting from the same front door would otherwise
cost hundreds of individual reverse-geocode calls for an identical answer every time, needlessly
slow (this project's own 1-req/s throttle, see geocoding.py) and inconsiderate of Nominatim's
public, shared service. One activity per group is actually looked up; every other activity in
that group reuses its result via a direct store, no network call.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Connection, select

from perseverer.db.schema import activity, activity_metric, route_geom
from perseverer.geocoding import (
    METRIC_LOCATION_NAME,
    SOURCE,
    _store,
    get_or_fetch_activity_location,
)

_COORD_ROUND_NDIGITS = 3


def backfill_locations(conn: Connection, archive_root: Path, *, athlete_id: str) -> int:
    """Returns the number of activities newly given a cached location name (whether via a real
    Nominatim lookup or reused from another activity's nearby result)."""
    already_cached = {
        row.activity_id
        for row in conn.execute(
            select(activity_metric.c.activity_id).where(
                activity_metric.c.athlete_id == athlete_id,
                activity_metric.c.source == SOURCE,
                activity_metric.c.metric_key == METRIC_LOCATION_NAME,
            )
        )
    }

    rows = conn.execute(
        select(activity.c.id, route_geom.c.start_lat, route_geom.c.start_lng)
        .select_from(activity.join(route_geom, activity.c.id == route_geom.c.activity_id))
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            route_geom.c.start_lat.is_not(None),
            route_geom.c.start_lng.is_not(None),
        )
        .order_by(activity.c.start_time_utc)
    ).fetchall()

    backfilled = 0
    # Rounded (lat, lon) -> the location name already resolved for that neighborhood this run,
    # so later activities in the same group skip Nominatim entirely.
    resolved_by_group: dict[tuple[float, float], str | None] = {}

    for row in rows:
        if row.id in already_cached:
            continue
        group = (
            round(row.start_lat, _COORD_ROUND_NDIGITS),
            round(row.start_lng, _COORD_ROUND_NDIGITS),
        )

        if group in resolved_by_group:
            location_name = resolved_by_group[group]
            if location_name is not None:
                _store(conn, athlete_id, row.id, location_name)
                conn.commit()
                backfilled += 1
            continue

        location_name = get_or_fetch_activity_location(
            conn,
            archive_root,
            athlete_id=athlete_id,
            activity_id=row.id,
            lat=row.start_lat,
            lon=row.start_lng,
        )
        conn.commit()
        resolved_by_group[group] = location_name
        if location_name is not None:
            backfilled += 1

    return backfilled
