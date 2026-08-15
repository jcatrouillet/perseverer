"""Open-Meteo historical weather enrichment, per activity -- fetched lazily on first request for
a GPS-bearing activity, archived raw (CLAUDE.md's "raw first, always" rule applies to Open-Meteo
the same as any other vendor: every byte fetched is archived verbatim before anything is parsed),
then cached forever in `activity_metric` so no activity's weather is ever fetched twice.

The real Open-Meteo archive API response shape was confirmed by calling it directly
(https://archive-api.open-meteo.com/v1/archive) rather than assumed from memory: one `hourly`
block keyed by UTC-aligned ISO timestamps (`timezone=UTC` requested explicitly, so the response
doesn't need per-activity-timezone handling), with parallel `temperature_2m`,
`relative_humidity_2m`, and `weathercode` (WMO code) arrays.

No API key needed (Open-Meteo's public tier, no auth). WMO weather codes are stored as-is --
mapping a code to an icon is a presentation concern, done in the frontend.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import httpx
from sqlalchemy import Connection, delete, select

from sporthealth.archive import archive_raw_bytes
from sporthealth.db.schema import activity_metric
from sporthealth.metrics.registry import get_or_register_metric

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE = "open-meteo"

METRIC_TEMPERATURE_MIN_C = "weather.open_meteo.temperature_min_c"
METRIC_TEMPERATURE_MAX_C = "weather.open_meteo.temperature_max_c"
METRIC_HUMIDITY_MIN_PCT = "weather.open_meteo.humidity_min_pct"
METRIC_HUMIDITY_MAX_PCT = "weather.open_meteo.humidity_max_pct"
METRIC_WEATHER_CODE = "weather.open_meteo.weather_code"

_ALL_METRIC_KEYS = (
    METRIC_TEMPERATURE_MIN_C,
    METRIC_TEMPERATURE_MAX_C,
    METRIC_HUMIDITY_MIN_PCT,
    METRIC_HUMIDITY_MAX_PCT,
    METRIC_WEATHER_CODE,
)

_METRIC_META: dict[str, tuple[str, str | None]] = {
    METRIC_TEMPERATURE_MIN_C: ("Min temperature (activity window)", "degC"),
    METRIC_TEMPERATURE_MAX_C: ("Max temperature (activity window)", "degC"),
    METRIC_HUMIDITY_MIN_PCT: ("Min relative humidity (activity window)", "%"),
    METRIC_HUMIDITY_MAX_PCT: ("Max relative humidity (activity window)", "%"),
    METRIC_WEATHER_CODE: ("Weather condition (WMO code, at activity start)", None),
}


class WeatherSummary(NamedTuple):
    temperature_min_c: float
    temperature_max_c: float
    humidity_min_pct: float
    humidity_max_pct: float
    weather_code: int


def parse_open_meteo_response(
    raw: dict[str, Any], start_utc: datetime, end_utc: datetime
) -> WeatherSummary | None:
    """Pure parse: reduces the response's hourly arrays to a min/max summary over the activity's
    own time window, plus a representative weather code for the hour closest to the activity's
    start. Every hourly bucket [hour, hour+1h) that overlaps [start_utc, end_utc] contributes --
    a short activity still gets at least the one bucket it falls inside, which is the best an
    hourly-resolution API can offer (never interpolated to look more precise than it is).
    Returns None if the response has no usable data for this window -- never a fabricated range.
    """
    hourly = raw.get("hourly")
    if not hourly:
        return None
    times: list[str] = hourly.get("time") or []
    temps: list[float | None] = hourly.get("temperature_2m") or []
    humidity: list[float | None] = hourly.get("relative_humidity_2m") or []
    codes: list[int | None] = hourly.get("weathercode") or []
    if not times:
        return None

    hours = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in times]

    window_temps: list[float] = []
    window_humidity: list[float] = []
    for i, hour in enumerate(hours):
        if hour + timedelta(hours=1) < start_utc or hour > end_utc:
            continue
        if i < len(temps) and temps[i] is not None:
            window_temps.append(temps[i])  # type: ignore[arg-type]
        if i < len(humidity) and humidity[i] is not None:
            window_humidity.append(humidity[i])  # type: ignore[arg-type]

    if not window_temps or not window_humidity:
        return None

    # Representative condition: the hour closest to the activity's own start time -- "what was
    # it like when I went out", not an aggregate across possibly-changing conditions.
    closest_idx = min(range(len(hours)), key=lambda i: abs((hours[i] - start_utc).total_seconds()))
    if closest_idx >= len(codes) or codes[closest_idx] is None:
        return None

    return WeatherSummary(
        temperature_min_c=min(window_temps),
        temperature_max_c=max(window_temps),
        humidity_min_pct=min(window_humidity),
        humidity_max_pct=max(window_humidity),
        weather_code=int(codes[closest_idx]),  # type: ignore[arg-type]
    )


def _read_cached(conn: Connection, athlete_id: str, activity_id: str) -> WeatherSummary | None:
    rows = conn.execute(
        select(activity_metric.c.metric_key, activity_metric.c.value_num).where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.activity_id == activity_id,
            activity_metric.c.source == SOURCE,
            activity_metric.c.metric_key.in_(_ALL_METRIC_KEYS),
        )
    ).fetchall()
    if not rows:
        return None
    values = {r.metric_key: r.value_num for r in rows}
    if any(values.get(key) is None for key in _ALL_METRIC_KEYS):
        return None
    return WeatherSummary(
        temperature_min_c=values[METRIC_TEMPERATURE_MIN_C],
        temperature_max_c=values[METRIC_TEMPERATURE_MAX_C],
        humidity_min_pct=values[METRIC_HUMIDITY_MIN_PCT],
        humidity_max_pct=values[METRIC_HUMIDITY_MAX_PCT],
        weather_code=int(values[METRIC_WEATHER_CODE]),
    )


def _store(
    conn: Connection, athlete_id: str, activity_id: str, summary: WeatherSummary
) -> None:
    # Delete-then-insert rather than a blind insert: safe to re-run even if an earlier attempt
    # for this activity wrote some but not all five rows before failing (a partial write would
    # otherwise collide with activity_metric's (athlete_id, activity_id, metric_key, source)
    # unique constraint on the next attempt).
    conn.execute(
        delete(activity_metric).where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.activity_id == activity_id,
            activity_metric.c.source == SOURCE,
            activity_metric.c.metric_key.in_(_ALL_METRIC_KEYS),
        )
    )
    values: dict[str, float] = {
        METRIC_TEMPERATURE_MIN_C: summary.temperature_min_c,
        METRIC_TEMPERATURE_MAX_C: summary.temperature_max_c,
        METRIC_HUMIDITY_MIN_PCT: summary.humidity_min_pct,
        METRIC_HUMIDITY_MAX_PCT: summary.humidity_max_pct,
        METRIC_WEATHER_CODE: float(summary.weather_code),
    }
    now = datetime.now(UTC)
    for key, value in values.items():
        display_name, unit = _METRIC_META[key]
        get_or_register_metric(
            conn,
            metric_key=key,
            source=SOURCE,
            display_name=display_name,
            unit_si=unit,
            category="activity",
            value_type="numeric",
        )
        conn.execute(
            activity_metric.insert().values(
                athlete_id=athlete_id,
                activity_id=activity_id,
                metric_key=key,
                value_num=value,
                value_text=None,
                unit=unit,
                source=SOURCE,
                created_at=now,
            )
        )


def get_or_fetch_activity_weather(
    conn: Connection,
    archive_root: Path,
    *,
    athlete_id: str,
    activity_id: str,
    start_time_utc: datetime,
    duration_s: float,
    lat: float,
    lon: float,
    client: httpx.Client | None = None,
) -> WeatherSummary | None:
    """Read-through cache: returns already-stored weather.* activity_metric rows if present (no
    network call -- an activity's weather never changes, so it's fetched at most once ever),
    otherwise calls Open-Meteo's historical archive for this activity's own date/location,
    archives the raw response, parses + stores the summary, and returns it.

    Returns None (never a fabricated range) if there's nothing cached and the fetch fails, or the
    response has no usable hourly data for this activity's time window. Does not commit -- the
    caller (the read API route) controls the transaction, matching archive_raw_bytes' own
    convention.
    """
    cached = _read_cached(conn, athlete_id, activity_id)
    if cached is not None:
        return cached

    end_time_utc = start_time_utc + timedelta(seconds=duration_s)
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "start_date": start_time_utc.date().isoformat(),
        "end_date": end_time_utc.date().isoformat(),
        "hourly": "temperature_2m,relative_humidity_2m,weathercode",
        "timezone": "UTC",
    }

    owns_client = client is None
    http_client = client or httpx.Client(timeout=15.0)
    try:
        response = http_client.get(OPEN_METEO_ARCHIVE_URL, params=params)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    finally:
        if owns_client:
            http_client.close()

    archive_raw_bytes(
        conn,
        archive_root,
        athlete_id=athlete_id,
        source=SOURCE,
        kind="historical-weather",
        content=response.content,
        locator=str(response.url),
        external_id=activity_id,
        http_status=response.status_code,
    )

    summary = parse_open_meteo_response(response.json(), start_time_utc, end_time_utc)
    if summary is None:
        return None

    _store(conn, athlete_id, activity_id, summary)
    return summary
