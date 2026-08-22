"""Prepends a weather-condition emoji to every outdoor activity's own title. Wired into every
ingest entry point's own touched-dates block (same precedent as refresh_vdot/refresh_pace_bands
-- see each adapter's own call site) so a newly-synced activity gets its emoji the same run it
first appears, not just whenever someone remembers to run the CLI command by hand. `sync
backfill-weather-titles` still exists for a one-off historical catch-up (e.g. the very first run
against an existing archive, or after restoring from raw bytes) and for its own `--dry-run`
preview, but isn't the only path that calls this anymore.

This mutates an athlete's own activity titles, which this project treats as durable user data
via `sport_override.py::set_name_override` (the same rebuild-safe override table `PATCH
/activities/{id}/name` writes to -- see that module's own docstring for why this isn't a raw
`activity.name` column mutation), not something to silently rewrite on every future sync the way
a derived metric (VDOT, pace bands) is -- the emoji is set once per activity and then left alone.

"Outdoor" has no dedicated concept in this codebase -- the only precedented proxy (the same one
weather.py's and geocoding.py's own callers already use) is "has a GPS start point"
(`route_geom`). An activity with no GPS start point has no location to fetch weather for at all,
so this is the natural gate, not an arbitrary choice.

Idempotent by inspecting the current title, not a separate "already processed" flag: an activity
whose title already starts with an emoji -- this feature's own, or one the athlete added by hand
before it existed -- is left alone. That's also what keeps every ingest run's call to this cheap
once an athlete's history is caught up: the SELECT below still scans every GPS-bearing activity
every run (there's no touched-dates filter on it, unlike the rollup/insight refreshes beside it,
since a newly-added activity from days ago is just as real a gap as one from today), but each
already-titled row is skipped before it ever reaches Open-Meteo again -- confirmed cheap in
practice against this athlete's own ~1050-activity history (a plain SQLite scan + Python string-
prefix check per row, milliseconds, not the per-row weather fetch).
"""

from __future__ import annotations

from pathlib import Path

import httpx
from sqlalchemy import Connection, select

from perseverer.api.schemas.common import to_utc
from perseverer.db.schema import activity, route_geom
from perseverer.sport_override import set_name_override
from perseverer.weather import get_or_fetch_activity_weather
from perseverer.weather_code import weather_code_info

# Broad enough to catch this feature's own emoji set and anything an athlete might have already
# typed by hand -- misc symbols/pictographs, weather/misc symbols & dingbats, arrows.
_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2190, 0x21FF),
    (0x2B00, 0x2BFF),
)


def _starts_with_emoji(title: str) -> bool:
    stripped = title.lstrip()
    if not stripped:
        return False
    return any(low <= ord(stripped[0]) <= high for low, high in _EMOJI_RANGES)


_VARIATION_SELECTOR = "️"


def strip_leading_emoji(title: str) -> str:
    """The inverse of `_starts_with_emoji`'s check -- returns `title` with a leading emoji (and
    the whitespace right after it) removed, or `title` unchanged if it doesn't start with one.
    Most of this feature's own weather emoji are two codepoints, not one -- a base symbol plus a
    trailing U+FE0F variation selector (confirmed against every entry in weather_code.py, e.g.
    "☀️" is U+2600 + U+FE0F) -- so a naive `title[1:]` leaves a dangling, invisible selector
    character behind instead of a clean match against the plain text after it.

    Public (not `_`-prefixed): reused by garmin_connect_activity_name.py, which needs to compare
    an already-emoji-titled activity's name against its own sport's generic default -- without
    this, an activity this module already touched would look like it has a real custom title to
    every OTHER correction pass, when it's really still just "{emoji} {generic default}"."""
    stripped = title.lstrip()
    if not stripped or not _starts_with_emoji(stripped):
        return title
    rest = stripped[1:]
    if rest.startswith(_VARIATION_SELECTOR):
        rest = rest[1:]
    return rest.lstrip()


def backfill_weather_titles(
    conn: Connection, archive_root: Path, *, athlete_id: str, dry_run: bool = False
) -> list[tuple[str, str, str]]:
    """Returns (activity_id, old_title, new_title) for every activity actually changed -- or that
    *would* change, when `dry_run=True` (nothing is written to the database in that case, not
    even the weather fetch's own cache, so a dry run is safe to run repeatedly).

    Reads the weather cache normally (no `force_refresh`) -- unlike the one-time migration this
    module originally shipped with (backfilling `feels_like_c`/wind onto activities cached under
    the old 5-field weather schema), every activity's weather is already fully cached by the time
    this runs automatically on every ingest, so forcing a fresh Open-Meteo fetch per activity per
    run would just be wasted, repeated network calls for no new data.
    """
    rows = conn.execute(
        select(
            activity.c.id,
            activity.c.name,
            activity.c.sport,
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

    changes: list[tuple[str, str, str]] = []
    with httpx.Client(timeout=15.0) as client:
        for row in rows:
            current_title = row.name or row.sport
            if _starts_with_emoji(current_title):
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
            )
            if not dry_run:
                conn.commit()
            if summary is None:
                continue

            emoji = weather_code_info(summary.weather_code).emoji
            new_title = f"{emoji} {current_title}"
            changes.append((row.id, current_title, new_title))

            if not dry_run:
                set_name_override(conn, athlete_id=athlete_id, activity_id=row.id, name=new_title)
                conn.commit()

    return changes
