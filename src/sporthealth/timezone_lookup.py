"""Derives an activity's true local timezone/UTC offset from its own recorded GPS coordinates
-- the only way to get this right for GPX/TCX-sourced activities, which (unlike FIT) carry no
local-time field of their own: every <trkpt>/<Trackpoint> timestamp in both formats is always
UTC (Zulu). A single global default like the athlete's home timezone would be wrong for any
race or trip away from home -- this looks up the real IANA zone the GPS point falls inside, and
computes the true UTC offset for that zone at that exact instant (so DST is handled correctly,
not just a fixed offset).

`TimezoneFinder` builds its own internal spatial index once and reuses it (construction takes
~0.5s) -- a module-level singleton so every GPX/TCX file parsed in a run (or during `sync
rebuild`, which replays the entire archive) pays that cost once, not per activity.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from timezonefinder import TimezoneFinder

_finder = TimezoneFinder()


def offset_from_coordinates(lat: float, lon: float, at: datetime) -> tuple[int, str | None]:
    """Returns `(utc_offset_s, tz_name)` for the IANA timezone at `(lat, lon)`, evaluated at
    the UTC instant `at` -- so the DST-vs-standard-time question is answered correctly for that
    specific date, not just a fixed year-round offset. `at` may be naive (treated as UTC, this
    project's own convention) or timezone-aware.

    Falls back to `(0, None)` when no timezone is found (open ocean -- `timezone_at` can
    legitimately return `None` there) or the coordinates are otherwise invalid; this is a
    best-effort enrichment, not a hard requirement for ingestion to succeed.
    """
    try:
        tz_name = _finder.timezone_at(lat=lat, lng=lon)
    except (ValueError, OverflowError):
        return 0, None
    if tz_name is None:
        return 0, None

    aware_utc = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
    try:
        offset = aware_utc.astimezone(ZoneInfo(tz_name)).utcoffset()
    except ZoneInfoNotFoundError:
        return 0, None
    if offset is None:
        return 0, None
    return int(offset.total_seconds()), tz_name
