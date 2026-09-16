"""One-time backfill: re-fetches an already-cached activity's Open-Meteo weather with
force_refresh=True so the fields added alongside dew_point_2m/shortwave_radiation/cloud_cover
(dew_point_min_c/max_c, solar_radiation_max_wm2/mean_wm2, cloud_cover_min_pct/max_pct,
apparent_temperature_min_c/max_c, sunrise_utc/sunset_utc, precipitation_mm, and the broader
hourly= request that also backs GET /activities/{id}/weather's own hourly[] trajectory) land for
real on activities whose weather was cached before those variables were ever requested.

Precedent: weather_titles.py originally shipped with an identical one-time force_refresh pass (to
backfill feels_like_c/wind onto activities cached under this project's very first five-field
weather schema) before that migration's own job was done and it settled back into a plain,
non-forcing cache read. This module is that same shape, run once against a real archive.

Idempotent and cheap to re-run, unlike a naive "force-refresh everything every time": an
activity's own archived raw response is read back first (`read_archived_open_meteo_response`, no
network call) and inspected for whether its `hourly` block already has `_NEW_FIELD_MARKER` at
all -- a structural marker for "this was fetched under the newer hourly= param set," independent
of whether Open-Meteo actually had a non-null reading for every hour (the same reason
`_read_cached` doesn't require the newer fields to be non-None: a real historical date can
legitimately lack that data on Open-Meteo's own side). An activity that already carries this
marker is skipped with zero further work, so a second run over an already-backfilled athlete
costs one archive read per activity and no network calls at all.

`_NEW_FIELD_MARKER` names whichever hourly= key was added most recently -- `precipitation`, as of
this writing, superseding the earlier `dew_point_2m` marker (a response carrying `precipitation`
was necessarily fetched under a request that already included `dew_point_2m` too, since both
land in the same joint hourly= param list -- see weather.py's own request construction). Moving
the marker forward like this means re-running this command after a field is added does one more
real backfill pass over every activity, even ones an earlier pass already backfilled for a prior
marker -- the correct, if slightly redundant, behavior, since there's no cheaper way to know
which activities are missing only the newest field without checking for it directly.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from sqlalchemy import Connection, select

from perseverer.api.schemas.common import to_utc
from perseverer.db.schema import activity, route_geom
from perseverer.weather import get_or_fetch_activity_weather, read_archived_open_meteo_response

# The presence of this key in an archived response's own `hourly` block (not any particular
# hour's value, which may genuinely be null) is what marks an activity as already fetched under
# the newer hourly= request -- see this module's own docstring.
_NEW_FIELD_MARKER = "precipitation"


def _already_backfilled(
    conn: Connection, archive_root: Path, *, athlete_id: str, activity_id: str
) -> bool:
    raw = read_archived_open_meteo_response(
        conn, archive_root, athlete_id=athlete_id, activity_id=activity_id
    )
    if raw is None:
        return False
    hourly = raw.get("hourly") or {}
    return _NEW_FIELD_MARKER in hourly


def backfill_weather_fields(
    conn: Connection, archive_root: Path, *, athlete_id: str, dry_run: bool = False
) -> list[str]:
    """Returns the activity ids actually re-fetched from Open-Meteo -- or that *would* be, when
    `dry_run=True` (nothing is written to the database in that case, not even the refreshed
    cache, since a dry run never calls `get_or_fetch_activity_weather` at all).

    Every GPS-bearing, duration-bearing activity is a candidate (not gated on already having a
    title emoji or any other unrelated marker) -- an activity's weather cache existing at all
    says nothing about which fields it was fetched with.
    """
    rows = conn.execute(
        select(
            activity.c.id,
            activity.c.start_time_utc,
            activity.c.duration_s,
            route_geom.c.start_lat,
            route_geom.c.start_lng,
        )
        .select_from(activity.join(route_geom, activity.c.id == route_geom.c.activity_id))
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            route_geom.c.start_lat.is_not(None),
            route_geom.c.start_lng.is_not(None),
            activity.c.duration_s.is_not(None),
        )
        .order_by(activity.c.start_time_utc)
    ).fetchall()

    refreshed: list[str] = []
    with httpx.Client(timeout=15.0) as client:
        for row in rows:
            if _already_backfilled(conn, archive_root, athlete_id=athlete_id, activity_id=row.id):
                continue
            if dry_run:
                refreshed.append(row.id)
                continue

            summary = get_or_fetch_activity_weather(
                conn,
                archive_root,
                athlete_id=athlete_id,
                activity_id=row.id,
                start_time_utc=to_utc(row.start_time_utc),
                duration_s=row.duration_s,
                lat=row.start_lat,
                lon=row.start_lng,
                client=client,
                force_refresh=True,
            )
            conn.commit()
            if summary is not None:
                refreshed.append(row.id)

    return refreshed
