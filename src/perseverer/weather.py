"""Open-Meteo historical weather enrichment, per activity -- fetched lazily on first request for
a GPS-bearing activity, archived raw (AGENTS.md's "raw first, always" rule applies to Open-Meteo
the same as any other vendor: every byte fetched is archived verbatim before anything is parsed),
then cached forever in `activity_metric` so no activity's weather is ever fetched twice.

The real Open-Meteo archive API response shape was confirmed by calling it directly
(https://archive-api.open-meteo.com/v1/archive) rather than assumed from memory: one `hourly`
block keyed by UTC-aligned ISO timestamps (`timezone=UTC` requested explicitly, so the response
doesn't need per-activity-timezone handling), with parallel `temperature_2m`,
`relative_humidity_2m`, `weathercode`, `apparent_temperature`, `wind_speed_10m`,
`wind_direction_10m`, `dew_point_2m`, `shortwave_radiation`, `cloud_cover`, and `precipitation`
arrays, plus a `daily` block (`sunrise`/`sunset`, one entry per calendar date in the requested
range, also UTC since `timezone=UTC` covers the whole request). `wind_speed_unit=ms` is
requested explicitly -- Open-Meteo defaults wind speed to km/h, which would otherwise be the one
non-SI value in this project's whole storage layer (AGENTS.md's "SI units in storage" rule).

No API key needed (Open-Meteo's public tier, no auth). WMO weather codes are stored as-is --
mapping a code to an icon/label is a presentation concern, done in the frontend
(weatherCode.ts) for display and in weather_code.py (backend) for the one non-display use --
prepending a condition emoji to an activity's own title, see weather_titles.py.

Feels-like/wind are a single representative value (the hour closest to the activity's own
start), matching weather_code's own "what was it like when I went out" framing -- not a min/max
range like temperature/humidity, which genuinely can swing meaningfully over a multi-hour
activity in a way wind conditions are reported less precisely for anyway at hourly resolution.
Each is `None` (never a fabricated value) whenever Open-Meteo's response is missing that
particular array or that particular hour's reading -- this can happen for very old dates the
free archive doesn't have full hourly wind/apparent-temperature coverage for, even when
temperature/humidity/weathercode are present.

Dew point, solar radiation, cloud cover, and a full min/max on apparent temperature (added
alongside the original five/eight fields, not replacing them) exist for a materially different
consumer than the fields above: an AI coaching agent judging heat stress needs the humidity
number that actually matters (dew point, not relative humidity alone), how much direct sun was
involved (shortwave radiation, W/m^2 -- sustained readings past ~800 are severe), and whether
apparent temperature sat notably below air temperature (dry air/wind doing real work), none of
which a single min/max on air temperature and a start-of-run feels-like value can show. These
five new scalar pairs (`dew_point_min_c`/`max_c`, `solar_radiation_max_wm2`/`mean_wm2`,
`cloud_cover_min_pct`/`max_c`, `apparent_temperature_min_c`/`max_c`) follow the exact same
window-aggregate convention as `temperature_min_c`/`max_c` -- computed over every hourly bucket
overlapping the activity's own window, `None` (never fabricated) when Open-Meteo's response
lacks the array entirely, same posture as feels-like/wind above. `sunrise_utc`/`sunset_utc`
(stored as `value_text`, ISO -- activity_metric.value_num is a Float column, no datetime concept
of its own) are the daily entry matching the activity's own start date; whether sunset actually
fell inside the run window is deliberately NOT stored as a synthetic activity_metric row -- it's
a pure function of already-available data (sunset_utc vs. the activity's own start/end), computed
once at the API layer (`ActivityWeatherOut.sunset_during_run`) rather than persisted redundantly.

`precipitation_mm` (added later still) is a window **sum**, not a min/max range like the fields
above -- "how much rain fell during the run" is a total, not a range, the same way a runner would
describe it. `0.0` is a real, meaningful value (no rain fell) and stays distinct from `None`
(Open-Meteo's response lacks the `precipitation` array at all, or every overlapping hour's
reading is null) -- summing an empty list would silently collapse those two very different cases
into the same `0.0`, so the window list is checked for emptiness first, same guard the
solar-radiation mean already uses for the identical reason.

The hour-by-hour trajectory (`parse_open_meteo_hourly_series`, surfaced as `GET
/activities/{id}/weather`'s own `hourly[]` array) is deliberately NOT stored in `activity_metric`
at all -- that table is scalar-only (`value_num`/`value_text`, one row per metric key), and an
hourly series doesn't fit it; inventing per-hour synthetic metric keys would pollute
`metric_definition` with hundreds of junk rows for no benefit over just re-reading the archive.
Instead it's re-derived from the same raw Open-Meteo response already archived verbatim on
first fetch -- free, no vendor call, exactly what "raw first, always" exists to enable
(`read_archived_open_meteo_response`, called by the API route after the summary is resolved).
This is also what makes an *already-cached* activity (fetched before dew_point_2m/
shortwave_radiation/cloud_cover existed) behave correctly with no special-casing: its archived
response genuinely doesn't have those three arrays, so every hourly point simply has those three
fields `None` -- re-parsing an old payload can't manufacture data that was never fetched. Getting
the *new* fields onto an already-cached activity for real needs an actual re-fetch --
`get_or_fetch_activity_weather(force_refresh=True)` (see `weather_backfill.py`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import httpx
from sqlalchemy import Connection, delete, select

from perseverer.archive import archive_raw_bytes, read_raw_bytes
from perseverer.db.schema import activity_metric, raw_object
from perseverer.metrics.registry import get_or_register_metric

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE = "open-meteo"

METRIC_TEMPERATURE_MIN_C = "weather.open_meteo.temperature_min_c"
METRIC_TEMPERATURE_MAX_C = "weather.open_meteo.temperature_max_c"
METRIC_HUMIDITY_MIN_PCT = "weather.open_meteo.humidity_min_pct"
METRIC_HUMIDITY_MAX_PCT = "weather.open_meteo.humidity_max_pct"
METRIC_WEATHER_CODE = "weather.open_meteo.weather_code"
METRIC_FEELS_LIKE_C = "weather.open_meteo.feels_like_c"
METRIC_WIND_SPEED_MPS = "weather.open_meteo.wind_speed_mps"
METRIC_WIND_DIRECTION_DEG = "weather.open_meteo.wind_direction_deg"
METRIC_DEW_POINT_MIN_C = "weather.open_meteo.dew_point_min_c"
METRIC_DEW_POINT_MAX_C = "weather.open_meteo.dew_point_max_c"
METRIC_SOLAR_RADIATION_MAX_WM2 = "weather.open_meteo.solar_radiation_max_wm2"
METRIC_SOLAR_RADIATION_MEAN_WM2 = "weather.open_meteo.solar_radiation_mean_wm2"
METRIC_CLOUD_COVER_MIN_PCT = "weather.open_meteo.cloud_cover_min_pct"
METRIC_CLOUD_COVER_MAX_PCT = "weather.open_meteo.cloud_cover_max_pct"
METRIC_APPARENT_TEMPERATURE_MIN_C = "weather.open_meteo.apparent_temperature_min_c"
METRIC_APPARENT_TEMPERATURE_MAX_C = "weather.open_meteo.apparent_temperature_max_c"
METRIC_SUNRISE_UTC = "weather.open_meteo.sunrise_utc"
METRIC_SUNSET_UTC = "weather.open_meteo.sunset_utc"
METRIC_PRECIPITATION_MM = "weather.open_meteo.precipitation_mm"

# The five original keys are the ones a cache-hit requires -- see _read_cached's own docstring
# for why every other key (13 of them now) is deliberately excluded from that check. Adding a
# new key to THIS tuple, not _OPTIONAL_METRIC_KEYS, is exactly the mistake that would make every
# already-cached activity fail the cache-hit check and re-fetch from Open-Meteo on every single
# view, forever -- the new key would never be backfilled onto old rows, so the check would never
# pass again for them.
_ALL_METRIC_KEYS = (
    METRIC_TEMPERATURE_MIN_C,
    METRIC_TEMPERATURE_MAX_C,
    METRIC_HUMIDITY_MIN_PCT,
    METRIC_HUMIDITY_MAX_PCT,
    METRIC_WEATHER_CODE,
)
_OPTIONAL_METRIC_KEYS = (
    METRIC_FEELS_LIKE_C,
    METRIC_WIND_SPEED_MPS,
    METRIC_WIND_DIRECTION_DEG,
    METRIC_DEW_POINT_MIN_C,
    METRIC_DEW_POINT_MAX_C,
    METRIC_SOLAR_RADIATION_MAX_WM2,
    METRIC_SOLAR_RADIATION_MEAN_WM2,
    METRIC_CLOUD_COVER_MIN_PCT,
    METRIC_CLOUD_COVER_MAX_PCT,
    METRIC_APPARENT_TEMPERATURE_MIN_C,
    METRIC_APPARENT_TEMPERATURE_MAX_C,
    METRIC_SUNRISE_UTC,
    METRIC_SUNSET_UTC,
    METRIC_PRECIPITATION_MM,
)

_METRIC_META: dict[str, tuple[str, str | None]] = {
    METRIC_TEMPERATURE_MIN_C: ("Min temperature (activity window)", "degC"),
    METRIC_TEMPERATURE_MAX_C: ("Max temperature (activity window)", "degC"),
    METRIC_HUMIDITY_MIN_PCT: ("Min relative humidity (activity window)", "%"),
    METRIC_HUMIDITY_MAX_PCT: ("Max relative humidity (activity window)", "%"),
    METRIC_WEATHER_CODE: ("Weather condition (WMO code, at activity start)", None),
    METRIC_FEELS_LIKE_C: ("Feels-like temperature (at activity start)", "degC"),
    METRIC_WIND_SPEED_MPS: ("Wind speed (at activity start)", "m/s"),
    METRIC_WIND_DIRECTION_DEG: ("Wind direction, degrees from north (at activity start)", "deg"),
    METRIC_DEW_POINT_MIN_C: ("Min dew point (activity window)", "degC"),
    METRIC_DEW_POINT_MAX_C: ("Max dew point (activity window)", "degC"),
    METRIC_SOLAR_RADIATION_MAX_WM2: ("Max shortwave radiation (activity window)", "W/m2"),
    METRIC_SOLAR_RADIATION_MEAN_WM2: ("Mean shortwave radiation (activity window)", "W/m2"),
    METRIC_CLOUD_COVER_MIN_PCT: ("Min cloud cover (activity window)", "%"),
    METRIC_CLOUD_COVER_MAX_PCT: ("Max cloud cover (activity window)", "%"),
    METRIC_APPARENT_TEMPERATURE_MIN_C: ("Min apparent temperature (activity window)", "degC"),
    METRIC_APPARENT_TEMPERATURE_MAX_C: ("Max apparent temperature (activity window)", "degC"),
    METRIC_SUNRISE_UTC: ("Sunrise, UTC (activity's own start date)", None),
    METRIC_SUNSET_UTC: ("Sunset, UTC (activity's own start date)", None),
    METRIC_PRECIPITATION_MM: ("Total precipitation (activity window)", "mm"),
}


class WeatherSummary(NamedTuple):
    temperature_min_c: float
    temperature_max_c: float
    humidity_min_pct: float
    humidity_max_pct: float
    weather_code: int
    feels_like_c: float | None = None
    wind_speed_mps: float | None = None
    wind_direction_deg: float | None = None
    dew_point_min_c: float | None = None
    dew_point_max_c: float | None = None
    solar_radiation_max_wm2: float | None = None
    solar_radiation_mean_wm2: float | None = None
    cloud_cover_min_pct: float | None = None
    cloud_cover_max_pct: float | None = None
    apparent_temperature_min_c: float | None = None
    apparent_temperature_max_c: float | None = None
    # Naive-implicit-UTC (ADR 0002), like every other internal datetime in this codebase -- the
    # API route attaches tzinfo via to_utc() when building the response, same as start_time_utc.
    sunrise_utc: datetime | None = None
    sunset_utc: datetime | None = None
    # A window SUM, not a min/max range -- see this module's own docstring for why. `None`
    # (never a fabricated 0.0) when Open-Meteo's response lacks the `precipitation` array
    # entirely, or every overlapping hour's reading is null.
    precipitation_mm: float | None = None


class HourlyWeatherPoint(NamedTuple):
    """One hourly bucket of the activity's own window -- see this module's own docstring for why
    this is a separate, re-derived-from-the-archive structure rather than anything stored in
    activity_metric. `time_utc` is naive-implicit-UTC, matching `WeatherSummary.sunrise_utc`/
    `sunset_utc` above."""

    time_utc: datetime
    temperature_c: float | None
    apparent_temperature_c: float | None
    dew_point_c: float | None
    relative_humidity_pct: float | None
    shortwave_radiation_wm2: float | None
    cloud_cover_pct: float | None
    wind_speed_mps: float | None
    wind_direction_deg: float | None
    precipitation_mm: float | None = None


def _value_at(arr: list[Any], index: int) -> float | None:
    if index >= len(arr) or arr[index] is None:
        return None
    return float(arr[index])


def parse_open_meteo_response(
    raw: dict[str, Any], start_utc: datetime, end_utc: datetime
) -> WeatherSummary | None:
    """Pure parse: reduces the response's hourly arrays to a min/max summary over the activity's
    own time window, plus a representative weather code/feels-like/wind for the hour closest to
    the activity's start. Every hourly bucket [hour, hour+1h) that overlaps [start_utc, end_utc]
    contributes to the min/max fields -- a short activity still gets at least the one bucket it
    falls inside, which is the best an hourly-resolution API can offer (never interpolated to
    look more precise than it is). Returns None if the response has no usable temperature/
    humidity/weathercode data for this window -- never a fabricated range. feels_like_c/
    wind_speed_mps/wind_direction_deg are independently `None` (rather than failing the whole
    parse) whenever Open-Meteo's response doesn't have that one array or that one hour's value.
    """
    hourly = raw.get("hourly")
    if not hourly:
        return None
    times: list[str] = hourly.get("time") or []
    temps: list[float | None] = hourly.get("temperature_2m") or []
    humidity: list[float | None] = hourly.get("relative_humidity_2m") or []
    codes: list[int | None] = hourly.get("weathercode") or []
    feels_like: list[float | None] = hourly.get("apparent_temperature") or []
    wind_speed: list[float | None] = hourly.get("wind_speed_10m") or []
    wind_direction: list[float | None] = hourly.get("wind_direction_10m") or []
    dew_point: list[float | None] = hourly.get("dew_point_2m") or []
    solar: list[float | None] = hourly.get("shortwave_radiation") or []
    cloud: list[float | None] = hourly.get("cloud_cover") or []
    precipitation: list[float | None] = hourly.get("precipitation") or []
    if not times:
        return None

    hours = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in times]

    window_temps: list[float] = []
    window_humidity: list[float] = []
    window_dew_point: list[float] = []
    window_solar: list[float] = []
    window_cloud: list[float] = []
    window_apparent: list[float] = []
    window_precipitation: list[float] = []
    for i, hour in enumerate(hours):
        if hour + timedelta(hours=1) < start_utc or hour > end_utc:
            continue
        if i < len(temps) and temps[i] is not None:
            window_temps.append(temps[i])  # type: ignore[arg-type]
        if i < len(humidity) and humidity[i] is not None:
            window_humidity.append(humidity[i])  # type: ignore[arg-type]
        if i < len(dew_point) and dew_point[i] is not None:
            window_dew_point.append(dew_point[i])  # type: ignore[arg-type]
        if i < len(solar) and solar[i] is not None:
            window_solar.append(solar[i])  # type: ignore[arg-type]
        if i < len(cloud) and cloud[i] is not None:
            window_cloud.append(cloud[i])  # type: ignore[arg-type]
        if i < len(feels_like) and feels_like[i] is not None:
            window_apparent.append(feels_like[i])  # type: ignore[arg-type]
        if i < len(precipitation) and precipitation[i] is not None:
            window_precipitation.append(precipitation[i])  # type: ignore[arg-type]

    if not window_temps or not window_humidity:
        return None

    # Representative condition: the hour closest to the activity's own start time -- "what was
    # it like when I went out", not an aggregate across possibly-changing conditions.
    closest_idx = min(range(len(hours)), key=lambda i: abs((hours[i] - start_utc).total_seconds()))
    if closest_idx >= len(codes) or codes[closest_idx] is None:
        return None

    sunrise_utc, sunset_utc = _parse_sunrise_sunset(raw.get("daily"), start_utc)

    return WeatherSummary(
        temperature_min_c=min(window_temps),
        temperature_max_c=max(window_temps),
        humidity_min_pct=min(window_humidity),
        humidity_max_pct=max(window_humidity),
        weather_code=int(codes[closest_idx]),  # type: ignore[arg-type]
        feels_like_c=_value_at(feels_like, closest_idx),
        wind_speed_mps=_value_at(wind_speed, closest_idx),
        wind_direction_deg=_value_at(wind_direction, closest_idx),
        dew_point_min_c=min(window_dew_point) if window_dew_point else None,
        dew_point_max_c=max(window_dew_point) if window_dew_point else None,
        solar_radiation_max_wm2=max(window_solar) if window_solar else None,
        solar_radiation_mean_wm2=(sum(window_solar) / len(window_solar) if window_solar else None),
        cloud_cover_min_pct=min(window_cloud) if window_cloud else None,
        cloud_cover_max_pct=max(window_cloud) if window_cloud else None,
        apparent_temperature_min_c=min(window_apparent) if window_apparent else None,
        apparent_temperature_max_c=max(window_apparent) if window_apparent else None,
        sunrise_utc=sunrise_utc,
        sunset_utc=sunset_utc,
        precipitation_mm=sum(window_precipitation) if window_precipitation else None,
    )


def _parse_sunrise_sunset(
    daily: dict[str, Any] | None, start_utc: datetime
) -> tuple[datetime | None, datetime | None]:
    """Picks the `daily` block's sunrise/sunset entry for the activity's own start date --
    Open-Meteo returns one entry per calendar date in the requested range (UTC, since the whole
    request carries `timezone=UTC`), and at this project's hourly resolution a run is treated as
    belonging to the one UTC date it started on. Returns (None, None), never a fabricated time,
    when the `daily` block is absent entirely (an old archived response fetched before this field
    was requested) or doesn't cover the activity's own date."""
    if not daily:
        return None, None
    dates: list[str] = daily.get("time") or []
    sunrises: list[str | None] = daily.get("sunrise") or []
    sunsets: list[str | None] = daily.get("sunset") or []
    target = start_utc.date().isoformat()
    if target not in dates:
        return None, None
    idx = dates.index(target)
    sunrise_text = sunrises[idx] if idx < len(sunrises) else None
    sunset_text = sunsets[idx] if idx < len(sunsets) else None
    sunrise = datetime.fromisoformat(sunrise_text) if sunrise_text is not None else None
    sunset = datetime.fromisoformat(sunset_text) if sunset_text is not None else None
    return sunrise, sunset


def parse_open_meteo_hourly_series(
    raw: dict[str, Any], start_utc: datetime, end_utc: datetime
) -> list[HourlyWeatherPoint]:
    """The hour-by-hour trajectory across the activity's own window -- the field that lets a
    consumer (an AI coaching agent, this project's own frontend) render a full run-window
    conditions table without a second call to Open-Meteo. See this module's own docstring for why
    this is re-derived from the archived raw response (via `read_archived_open_meteo_response`)
    rather than stored in `activity_metric`. Same window rule as `parse_open_meteo_response`:
    every hourly bucket [hour, hour+1h) overlapping [start_utc, end_utc] contributes one point,
    in chronological order. Each field is independently `None` (never fabricated) whenever the
    response lacks that array entirely (an old archive, pre-dating dew_point_2m/
    shortwave_radiation/cloud_cover/precipitation) or that one hour's reading. Returns [] when
    there's no hourly block at all, or no hour overlaps the window -- never a fabricated point.
    """
    hourly = raw.get("hourly")
    if not hourly:
        return []
    times: list[str] = hourly.get("time") or []
    temps: list[float | None] = hourly.get("temperature_2m") or []
    apparent: list[float | None] = hourly.get("apparent_temperature") or []
    dew_point: list[float | None] = hourly.get("dew_point_2m") or []
    humidity: list[float | None] = hourly.get("relative_humidity_2m") or []
    solar: list[float | None] = hourly.get("shortwave_radiation") or []
    cloud: list[float | None] = hourly.get("cloud_cover") or []
    wind_speed: list[float | None] = hourly.get("wind_speed_10m") or []
    wind_direction: list[float | None] = hourly.get("wind_direction_10m") or []
    precipitation: list[float | None] = hourly.get("precipitation") or []

    points: list[HourlyWeatherPoint] = []
    for i, t in enumerate(times):
        hour = datetime.fromisoformat(t)  # naive; already UTC since timezone=UTC was requested
        hour_aware = hour.replace(tzinfo=UTC)
        if hour_aware + timedelta(hours=1) < start_utc or hour_aware > end_utc:
            continue
        points.append(
            HourlyWeatherPoint(
                time_utc=hour,
                temperature_c=_value_at(temps, i),
                apparent_temperature_c=_value_at(apparent, i),
                dew_point_c=_value_at(dew_point, i),
                relative_humidity_pct=_value_at(humidity, i),
                shortwave_radiation_wm2=_value_at(solar, i),
                cloud_cover_pct=_value_at(cloud, i),
                wind_speed_mps=_value_at(wind_speed, i),
                wind_direction_deg=_value_at(wind_direction, i),
                precipitation_mm=_value_at(precipitation, i),
            )
        )
    return points


def read_archived_open_meteo_response(
    conn: Connection, archive_root: Path, *, athlete_id: str, activity_id: str
) -> dict[str, Any] | None:
    """Reads back the most recently archived Open-Meteo response for this activity -- a free,
    no-network re-derivation of the hour-by-hour trajectory from bytes already on disk, per
    AGENTS.md's raw-first rule ("if we discover a field we ignored today, we must be able to
    re-derive [it] without re-contacting any vendor"). Picks the newest archived response by
    `fetched_at` when more than one exists for this activity: a `force_refresh=True` backfill
    (see `weather_backfill.py`) archives a second, newer, content-addressed blob alongside the
    original rather than replacing it (never destructive, raw-first), and the newer one is the
    one actually carrying the fields a caller wants. Returns None (never a fabricated response)
    when nothing was ever archived for this activity."""
    row = conn.execute(
        select(raw_object.c.storage_path)
        .where(
            raw_object.c.athlete_id == athlete_id,
            raw_object.c.source == SOURCE,
            raw_object.c.kind == "historical-weather",
            raw_object.c.external_id == activity_id,
        )
        .order_by(raw_object.c.fetched_at.desc(), raw_object.c.id.desc())
        .limit(1)
    ).fetchone()
    if row is None:
        return None
    parsed: dict[str, Any] = json.loads(read_raw_bytes(archive_root, row.storage_path))
    return parsed


def _read_cached(conn: Connection, athlete_id: str, activity_id: str) -> WeatherSummary | None:
    """A cache-hit requires only the five original keys -- every newer key (feels-like/wind, and
    the dew-point/solar/cloud/apparent-temperature/sunrise/sunset fields added alongside them) is
    read back if present but never required, so an activity fetched before those existed still
    cache-hits (with those fields simply None) rather than silently re-fetching on every single
    view forever, since real historical dates can genuinely lack that data on Open-Meteo's side
    and would otherwise never satisfy an "all keys present" check. Use `force_refresh=True` on
    `get_or_fetch_activity_weather` (see weather_backfill.py) to deliberately re-fetch an
    already-cached activity and pick up the newer fields for real."""
    rows = conn.execute(
        select(
            activity_metric.c.metric_key,
            activity_metric.c.value_num,
            activity_metric.c.value_text,
        ).where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.activity_id == activity_id,
            activity_metric.c.source == SOURCE,
            activity_metric.c.metric_key.in_((*_ALL_METRIC_KEYS, *_OPTIONAL_METRIC_KEYS)),
        )
    ).fetchall()
    if not rows:
        return None
    values = {r.metric_key: r.value_num for r in rows}
    texts = {r.metric_key: r.value_text for r in rows}
    if any(values.get(key) is None for key in _ALL_METRIC_KEYS):
        return None
    sunrise_text = texts.get(METRIC_SUNRISE_UTC)
    sunset_text = texts.get(METRIC_SUNSET_UTC)
    return WeatherSummary(
        temperature_min_c=values[METRIC_TEMPERATURE_MIN_C],
        temperature_max_c=values[METRIC_TEMPERATURE_MAX_C],
        humidity_min_pct=values[METRIC_HUMIDITY_MIN_PCT],
        humidity_max_pct=values[METRIC_HUMIDITY_MAX_PCT],
        weather_code=int(values[METRIC_WEATHER_CODE]),
        feels_like_c=values.get(METRIC_FEELS_LIKE_C),
        wind_speed_mps=values.get(METRIC_WIND_SPEED_MPS),
        wind_direction_deg=values.get(METRIC_WIND_DIRECTION_DEG),
        dew_point_min_c=values.get(METRIC_DEW_POINT_MIN_C),
        dew_point_max_c=values.get(METRIC_DEW_POINT_MAX_C),
        solar_radiation_max_wm2=values.get(METRIC_SOLAR_RADIATION_MAX_WM2),
        solar_radiation_mean_wm2=values.get(METRIC_SOLAR_RADIATION_MEAN_WM2),
        cloud_cover_min_pct=values.get(METRIC_CLOUD_COVER_MIN_PCT),
        cloud_cover_max_pct=values.get(METRIC_CLOUD_COVER_MAX_PCT),
        apparent_temperature_min_c=values.get(METRIC_APPARENT_TEMPERATURE_MIN_C),
        apparent_temperature_max_c=values.get(METRIC_APPARENT_TEMPERATURE_MAX_C),
        sunrise_utc=datetime.fromisoformat(sunrise_text) if sunrise_text else None,
        sunset_utc=datetime.fromisoformat(sunset_text) if sunset_text else None,
        precipitation_mm=values.get(METRIC_PRECIPITATION_MM),
    )


def _store(conn: Connection, athlete_id: str, activity_id: str, summary: WeatherSummary) -> None:
    # Delete-then-insert rather than a blind insert: safe to re-run even if an earlier attempt
    # for this activity wrote some but not all rows before failing (a partial write would
    # otherwise collide with activity_metric's (athlete_id, activity_id, metric_key, source)
    # unique constraint on the next attempt).
    conn.execute(
        delete(activity_metric).where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.activity_id == activity_id,
            activity_metric.c.source == SOURCE,
            activity_metric.c.metric_key.in_((*_ALL_METRIC_KEYS, *_OPTIONAL_METRIC_KEYS)),
        )
    )
    values: dict[str, float] = {
        METRIC_TEMPERATURE_MIN_C: summary.temperature_min_c,
        METRIC_TEMPERATURE_MAX_C: summary.temperature_max_c,
        METRIC_HUMIDITY_MIN_PCT: summary.humidity_min_pct,
        METRIC_HUMIDITY_MAX_PCT: summary.humidity_max_pct,
        METRIC_WEATHER_CODE: float(summary.weather_code),
    }
    # Optional numeric fields only get a row when Open-Meteo actually had one -- never a
    # fabricated 0, matching AGENTS.md's raw-first rule exactly as strictly as the required
    # fields above.
    _add_if_present(values, METRIC_FEELS_LIKE_C, summary.feels_like_c)
    _add_if_present(values, METRIC_WIND_SPEED_MPS, summary.wind_speed_mps)
    _add_if_present(values, METRIC_WIND_DIRECTION_DEG, summary.wind_direction_deg)
    _add_if_present(values, METRIC_DEW_POINT_MIN_C, summary.dew_point_min_c)
    _add_if_present(values, METRIC_DEW_POINT_MAX_C, summary.dew_point_max_c)
    _add_if_present(values, METRIC_SOLAR_RADIATION_MAX_WM2, summary.solar_radiation_max_wm2)
    _add_if_present(values, METRIC_SOLAR_RADIATION_MEAN_WM2, summary.solar_radiation_mean_wm2)
    _add_if_present(values, METRIC_CLOUD_COVER_MIN_PCT, summary.cloud_cover_min_pct)
    _add_if_present(values, METRIC_CLOUD_COVER_MAX_PCT, summary.cloud_cover_max_pct)
    _add_if_present(values, METRIC_APPARENT_TEMPERATURE_MIN_C, summary.apparent_temperature_min_c)
    _add_if_present(values, METRIC_APPARENT_TEMPERATURE_MAX_C, summary.apparent_temperature_max_c)
    _add_if_present(values, METRIC_PRECIPITATION_MM, summary.precipitation_mm)

    # Sunrise/sunset are timestamps, not floats -- activity_metric.value_num is a Float column,
    # so these go in value_text as ISO strings instead (value_type="text", same precedent
    # geocoding.py's own location-name metric already established for a non-numeric reading).
    texts: dict[str, str] = {}
    if summary.sunrise_utc is not None:
        texts[METRIC_SUNRISE_UTC] = summary.sunrise_utc.isoformat()
    if summary.sunset_utc is not None:
        texts[METRIC_SUNSET_UTC] = summary.sunset_utc.isoformat()

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
    for key, text in texts.items():
        display_name, unit = _METRIC_META[key]
        get_or_register_metric(
            conn,
            metric_key=key,
            source=SOURCE,
            display_name=display_name,
            unit_si=unit,
            category="activity",
            value_type="text",
        )
        conn.execute(
            activity_metric.insert().values(
                athlete_id=athlete_id,
                activity_id=activity_id,
                metric_key=key,
                value_num=None,
                value_text=text,
                unit=unit,
                source=SOURCE,
                created_at=now,
            )
        )


def _add_if_present(values: dict[str, float], key: str, value: float | None) -> None:
    if value is not None:
        values[key] = value


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
    force_refresh: bool = False,
) -> WeatherSummary | None:
    """Read-through cache: returns already-stored weather.* activity_metric rows if present (no
    network call -- an activity's weather never changes, so it's fetched at most once ever),
    otherwise calls Open-Meteo's historical archive for this activity's own date/location,
    archives the raw response, parses + stores the summary, and returns it.

    `force_refresh=True` skips the cache-read and always calls Open-Meteo -- for
    weather_backfill.py's backfill, which needs the newer fields (originally feels-like/wind;
    now also dew point/solar radiation/cloud cover/apparent-temperature-range/sunrise/sunset) on
    activities whose weather was already cached (and therefore cache-hits, per _read_cached's own
    docstring) before those fields existed. Not exposed to the read API route, which always wants
    the cheap path.

    Returns None (never a fabricated range) if there's nothing cached and the fetch fails, or the
    response has no usable hourly data for this activity's time window. Does not commit -- the
    caller (the read API route) controls the transaction, matching archive_raw_bytes' own
    convention.
    """
    if not force_refresh:
        cached = _read_cached(conn, athlete_id, activity_id)
        if cached is not None:
            return cached

    end_time_utc = start_time_utc + timedelta(seconds=duration_s)
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "start_date": start_time_utc.date().isoformat(),
        "end_date": end_time_utc.date().isoformat(),
        "hourly": (
            "temperature_2m,relative_humidity_2m,weathercode,"
            "apparent_temperature,wind_speed_10m,wind_direction_10m,"
            "dew_point_2m,shortwave_radiation,cloud_cover,precipitation"
        ),
        "daily": "sunrise,sunset",
        "wind_speed_unit": "ms",
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
