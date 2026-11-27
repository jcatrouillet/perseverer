"""The last forecast Open-Meteo answered, kept so a provider outage does not blank it.

`weather_forecast.py` fetches live on every request and deliberately archives nothing: a forecast
is superseded by reality, so it is never a record to re-derive. What a caller loses in an outage
is not a record but the *use* of a forecast a few hours old (tomorrow's best start time is still
good), so the last successful answer is kept per athlete and kind (`home` or `last_activity`) and
served, flagged stale with its age, only when the live call fails. It is never served instead of a
live answer, never for a different location, never for days already past, and not older than
`MAX_STALE_HOURS`.

Both functions work on the API's own response JSON (a dict of `days`, `upcoming`), so this module
knows nothing about the models. Writes are short and happen after the vendor call, never around it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, select
from sqlalchemy.dialects.sqlite import insert

from perseverer.db.schema import weather_forecast_cache

# Beyond this a forecast is no longer worth showing as "the forecast".
MAX_STALE_HOURS = 72

KIND_HOME = "home"
KIND_LAST_ACTIVITY = "last_activity"


def today_in(tz_name: str | None) -> str:
    """The ISO date now in `tz_name` (UTC when it is missing or not a zone, e.g. "auto")."""
    try:
        zone = ZoneInfo(tz_name) if tz_name else UTC
    except (KeyError, ValueError):
        zone = UTC
    return datetime.now(zone).date().isoformat()


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, as everywhere in storage


def _same_place(a: float, b: float) -> bool:
    return abs(a - b) < 0.0001  # the fetch itself rounds to 4 decimals


def _merge(
    old: list[dict[str, Any]], new: list[dict[str, Any]], today: str
) -> list[dict[str, Any]]:
    """Dates from `new` win; older dates survive (a 3-day fetch must not shrink a 16-day cache);
    days already past are dropped."""
    by_date = {d["local_date"]: d for d in old}
    by_date.update({d["local_date"]: d for d in new})
    return [by_date[k] for k in sorted(by_date) if k >= today]


def store_forecast(
    conn: Connection,
    athlete_id: str,
    kind: str,
    lat: float,
    lon: float,
    *,
    days: list[dict[str, Any]],
    upcoming: list[dict[str, Any]],
    today: str,
    now: datetime,
) -> None:
    """Remembers a forecast the provider just answered (naive UTC `now`; `today` is the ISO date in
    the forecast's own timezone). Merges into the previous one for the same place."""
    previous = load_forecast(conn, athlete_id, kind, lat, lon, today=today, now=now)
    old_days = previous[0] if previous else []
    old_upcoming = previous[1] if previous else []
    payload = json.dumps(
        {
            "days": _merge(old_days, days, today),
            "upcoming": _merge(old_upcoming, upcoming, today),
        }
    )
    stmt = insert(weather_forecast_cache).values(
        athlete_id=athlete_id, kind=kind, lat=lat, lon=lon, payload=payload, fetched_at=now
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["athlete_id", "kind"],
            set_={"lat": lat, "lon": lon, "payload": payload, "fetched_at": now},
        )
    )
    conn.commit()


def load_forecast(
    conn: Connection,
    athlete_id: str,
    kind: str,
    lat: float,
    lon: float,
    *,
    today: str,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], datetime] | None:
    """The remembered days, near-term detail and fetch time for this exact place, with past days
    removed; None when nothing usable is kept (none stored, another place, too old, or every day
    already past)."""
    row = conn.execute(
        select(weather_forecast_cache).where(
            weather_forecast_cache.c.athlete_id == athlete_id,
            weather_forecast_cache.c.kind == kind,
        )
    ).fetchone()
    if row is None or not (_same_place(row.lat, lat) and _same_place(row.lon, lon)):
        return None
    if now - row.fetched_at > timedelta(hours=MAX_STALE_HOURS):
        return None
    payload = json.loads(row.payload)
    days = [d for d in payload.get("days", []) if d["local_date"] >= today]
    upcoming = [d for d in payload.get("upcoming", []) if d["local_date"] >= today]
    if not days:
        return None
    return days, upcoming, row.fetched_at
