"""Open-Meteo forecast for an athlete's own home location -- the future-facing counterpart to
`weather.py`'s past-activity weather, and a deliberately different shape from it. Confirmed live
against the real endpoint (https://api.open-meteo.com/v1/forecast, distinct from the historical
archive API `weather.py` calls) rather than assumed: `forecast_days` is hard-capped to the range
0-16 -- requesting more returns `{"error":true,"reason":"Forecast days is invalid. Allowed range
0 to 16. Given <n>."}` -- and the daily response shape (`daily=weathercode,temperature_2m_max,
temperature_2m_min`) returns parallel `time`/`weathercode`/`temperature_2m_max`/
`temperature_2m_min` arrays, the same field-naming convention `weather.py`'s own historical
request already uses.

`timezone` is the athlete's own IANA zone (`athlete.timezone`, GET/PUT /settings/profile),
never hardcoded UTC -- confirmed live that Open-Meteo's `daily` entries are dates in the
*requested* timezone, so a UTC request for an athlete west of Greenwich returns "today" as
already tomorrow locally for several hours each day (e.g. requesting `timezone=UTC` at 20:00
Pacific returns a `time[0]` one calendar day ahead of the same request with
`timezone=America/Los_Angeles`). The Week view matches a forecast day to a column by exact
`local_date` string equality against its own browser-local "today" -- a UTC-anchored forecast
would misalign by one day for roughly a third of the globe, exactly the kind of "changing weeks
at the wrong time" bug an athlete would actually notice.

Deliberately NOT archived raw and NOT cached, unlike every other vendor fetch in this codebase
(AGENTS.md's "raw first, always" rule exists so a permanent record can be re-derived from an
archive without recontacting a vendor -- a forecast has no such permanent-record concept, since
it's superseded by reality as the date approaches, so archiving it would only accumulate useless
bytes with zero re-derivation benefit). This mirrors the "request-time exception to the rollup
mandate" precedent `vo2max_analysis.py`/`pace_hr_zones.py` already establish for a bounded,
occasional live lookup, not a new one.

No API key needed (Open-Meteo's public tier). Weather-code-to-icon mapping stays a frontend
presentation concern (`weatherCode.ts`) for the Week view, same posture as `weather.py` --
`email_reports.py`'s own per-day emoji is the one non-frontend-display exception, reusing
`weather_code.py` (backend) the same way `weather_titles.py` already does for an activity title.

`fetch_upcoming_conditions`/`ForecastDayDetail` (added later) collect the same richer field set
`weather.py` gathers for a past run -- dew point, shortwave radiation, cloud cover, a full
apparent-temperature range, precipitation, sunrise/sunset, and an hour-by-hour trajectory -- but
for the athlete's own *upcoming* `UPCOMING_DETAIL_DAYS` days rather than one activity's window,
so a coaching agent (or this app's own future UI) can judge tomorrow's conditions in the same
bpm/pace terms it already judges a past run in, without a second Open-Meteo call. Deliberately a
**separate** Open-Meteo request from `fetch_forecast`'s own coarse, up-to-16-day one above, not
an extension of it: `forecast_days` controls both the `daily` and `hourly` ranges together in one
request, and the Week view's own simple icon+temperature columns need up to 16 days while the
rich hourly detail below is only ever fetched for the near-term handful of days it stays
meaningfully accurate for -- combining them would mean fetching 16 days of hourly data (a lot of
otherwise-unused points) just to serve 3 days' worth of any consumer that wants it, or capping
the coarse forecast at 3 days and breaking the Week view. `fetch_forecast`/`ForecastDay` above
are completely untouched by this addition.

One further difference from `weather.py`'s own activity-window shape: `timezone=UTC` is not an
option here (see the athlete's-own-timezone discussion above), so every date/time value below --
`local_date`, `sunrise_local`/`sunset_local`, and each `ForecastHourlyPoint.time_local` -- is a
naive value in the athlete's own local time (confirmed live, same as the coarse forecast's own
`daily` dates), never UTC; the `_local` suffix on the datetime fields (vs. `weather.py`'s own
`_utc` ones) says so explicitly rather than leaving the distinction implicit. There is also no
single "activity start" to anchor a representative wind/feels-like reading against the way
`weather.py` does for feels_like_c/wind_speed_mps/wind_direction_deg -- a whole day has no one
particular hour that's "when I went out" -- so `ForecastDayDetail` deliberately has no scalar
equivalents for those three fields; `hourly` carries per-hour wind/apparent-temperature instead,
letting a consumer pick whichever hour matches their own planned time, honest about what a
day-level forecast actually is rather than fabricating one representative hour.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, NamedTuple

import httpx

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo's own hard cap, confirmed live -- requesting more raises an error response rather
# than silently truncating.
MAX_FORECAST_DAYS = 16

# How many of the athlete's own upcoming days get the full rich-conditions detail
# (fetch_upcoming_conditions/ForecastDayDetail) alongside the coarse icon+temperature forecast
# above -- a forecast that far out stays meaningfully accurate; see this module's own docstring.
UPCOMING_DETAIL_DAYS = 3


class ForecastDay(NamedTuple):
    local_date: date
    weather_code: int
    temperature_min_c: float
    temperature_max_c: float


def parse_forecast_response(raw: dict[str, Any]) -> list[ForecastDay]:
    """Pure parse of the `daily` block into one entry per date. Skips (never fabricates) a date
    whose weathercode/min/max isn't all present -- Open-Meteo's forecast reliably fills every
    requested day in practice, but a day missing any of the three is dropped rather than shown
    with a guessed value."""
    daily = raw.get("daily")
    if not daily:
        return []
    dates: list[str] = daily.get("time") or []
    codes: list[int | None] = daily.get("weathercode") or []
    mins: list[float | None] = daily.get("temperature_2m_min") or []
    maxs: list[float | None] = daily.get("temperature_2m_max") or []

    days: list[ForecastDay] = []
    for i, d in enumerate(dates):
        code = codes[i] if i < len(codes) else None
        t_min = mins[i] if i < len(mins) else None
        t_max = maxs[i] if i < len(maxs) else None
        if code is None or t_min is None or t_max is None:
            continue
        days.append(
            ForecastDay(
                local_date=date.fromisoformat(d),
                weather_code=int(code),
                temperature_min_c=float(t_min),
                temperature_max_c=float(t_max),
            )
        )
    return days


def parse_resolved_timezone(raw: dict[str, Any]) -> str | None:
    """The IANA zone name Open-Meteo actually used for the response (its top-level `timezone`
    field) -- the real name behind a `timezone=auto` request, which is how a forecast for a
    coordinate with no known zone can still say which zone its local dates are in."""
    tz = raw.get("timezone")
    return tz if isinstance(tz, str) and tz else None


def fetch_forecast(
    lat: float, lon: float, days: int, *, tz: str = "UTC", client: httpx.Client | None = None
) -> list[ForecastDay] | None:
    result = fetch_forecast_with_timezone(lat, lon, days, tz=tz, client=client)
    return None if result is None else result[0]


def fetch_forecast_with_timezone(
    lat: float, lon: float, days: int, *, tz: str = "UTC", client: httpx.Client | None = None
) -> tuple[list[ForecastDay], str | None] | None:
    """Calls Open-Meteo's forecast endpoint for `min(days, MAX_FORECAST_DAYS)` days starting
    today (Open-Meteo's own `forecast_days` semantics -- today plus `forecast_days - 1` more) in
    `tz`, the athlete's own IANA timezone -- see this module's own docstring for why this must be
    a real per-athlete zone, not a hardcoded UTC, for `local_date` to line up with the Week
    view's own local-date grouping. `tz` is trusted as already-validated (see
    api/schemas/settings.py::AthleteProfileIn's own zoneinfo check) -- Open-Meteo itself would
    just 400 on a garbage value, which surfaces as a plain fetch failure (None) below. Returns
    None (never a fabricated forecast) on any HTTP failure; returns [] if the response parses but
    has no usable daily data. The second tuple element is the zone name Open-Meteo resolved (see
    `parse_resolved_timezone`), None if the response omits it."""
    requested_days = max(0, min(days, MAX_FORECAST_DAYS))
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "daily": "weathercode,temperature_2m_max,temperature_2m_min",
        "forecast_days": str(requested_days),
        "timezone": tz,
    }

    owns_client = client is None
    http_client = client or httpx.Client(timeout=15.0)
    try:
        response = http_client.get(OPEN_METEO_FORECAST_URL, params=params)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    finally:
        if owns_client:
            http_client.close()

    raw = response.json()
    return parse_forecast_response(raw), parse_resolved_timezone(raw)


class ForecastHourlyPoint(NamedTuple):
    """One hourly bucket of a single upcoming day. `time_local` is naive, in the athlete's own
    local time (per the `tz` requested) -- NOT UTC, unlike weather.py's own
    HourlyWeatherPoint.time_utc -- see this module's own docstring for why."""

    time_local: datetime
    temperature_c: float | None
    apparent_temperature_c: float | None
    dew_point_c: float | None
    relative_humidity_pct: float | None
    shortwave_radiation_wm2: float | None
    cloud_cover_pct: float | None
    wind_speed_mps: float | None
    wind_direction_deg: float | None
    precipitation_mm: float | None = None


class ForecastDayDetail(NamedTuple):
    """The same richer field set weather.py collects for a past activity's own window, gathered
    instead for one upcoming calendar day -- see this module's own docstring for why
    feels_like_c/wind_speed_mps/wind_direction_deg have no scalar equivalent here (no single
    "activity start" hour to anchor a representative reading against; `hourly` carries per-hour
    wind/apparent-temperature instead) and why every datetime field is local, not UTC."""

    local_date: date
    weather_code: int | None
    temperature_min_c: float | None
    temperature_max_c: float | None
    humidity_min_pct: float | None = None
    humidity_max_pct: float | None = None
    dew_point_min_c: float | None = None
    dew_point_max_c: float | None = None
    solar_radiation_max_wm2: float | None = None
    solar_radiation_mean_wm2: float | None = None
    cloud_cover_min_pct: float | None = None
    cloud_cover_max_pct: float | None = None
    apparent_temperature_min_c: float | None = None
    apparent_temperature_max_c: float | None = None
    # A day-total SUM, not a range -- same "how much rain fell" convention weather.py's own
    # precipitation_mm uses. 0.0 is a real reading (no rain forecast); None means Open-Meteo's
    # response has no usable precipitation data for this day at all.
    precipitation_mm: float | None = None
    sunrise_local: datetime | None = None
    sunset_local: datetime | None = None
    hourly: tuple[ForecastHourlyPoint, ...] = ()


def _forecast_value_at(arr: list[Any], index: int) -> float | None:
    if index >= len(arr) or arr[index] is None:
        return None
    return float(arr[index])


def parse_upcoming_conditions_response(raw: dict[str, Any]) -> list[ForecastDayDetail]:
    """Pure parse: groups the response's hourly arrays by the calendar date each (local, naive)
    timestamp falls on, then reduces each day's own group to the same min/max/sum aggregates
    weather.py's own parse_open_meteo_response uses for an activity's window -- see this module's
    own docstring for the full field-by-field rationale. One `ForecastDayDetail` per entry in the
    `daily` block, in the order Open-Meteo returns them; a day whose own hourly group is empty
    (shouldn't happen with a well-formed response, but never assumed) still gets an entry, with
    every hourly-derived field `None` -- the daily-only fields (weather_code/temperature_min_c/
    max_c/sunrise_local/sunset_local) still came from a real `daily` entry. Returns [] when
    either the `daily` or `hourly` block is missing entirely."""
    daily = raw.get("daily")
    hourly = raw.get("hourly")
    if not daily or not hourly:
        return []

    dates: list[str] = daily.get("time") or []
    codes: list[int | None] = daily.get("weathercode") or []
    mins: list[float | None] = daily.get("temperature_2m_min") or []
    maxs: list[float | None] = daily.get("temperature_2m_max") or []
    sunrises: list[str | None] = daily.get("sunrise") or []
    sunsets: list[str | None] = daily.get("sunset") or []

    hourly_times: list[str] = hourly.get("time") or []
    temps: list[float | None] = hourly.get("temperature_2m") or []
    humidity: list[float | None] = hourly.get("relative_humidity_2m") or []
    apparent: list[float | None] = hourly.get("apparent_temperature") or []
    dew_point: list[float | None] = hourly.get("dew_point_2m") or []
    solar: list[float | None] = hourly.get("shortwave_radiation") or []
    cloud: list[float | None] = hourly.get("cloud_cover") or []
    precipitation: list[float | None] = hourly.get("precipitation") or []
    wind_speed: list[float | None] = hourly.get("wind_speed_10m") or []
    wind_direction: list[float | None] = hourly.get("wind_direction_10m") or []

    # Every hourly index, grouped by the calendar date its own local timestamp falls on -- a
    # cheap string-prefix slice ("YYYY-MM-DDTHH:MM" -> "YYYY-MM-DD") since Open-Meteo's own
    # `daily.time` entries use the identical "YYYY-MM-DD" spelling to key against.
    indices_by_date: dict[str, list[int]] = {}
    for i, t in enumerate(hourly_times):
        indices_by_date.setdefault(t[:10], []).append(i)

    def _window(values: list[float | None], indices: list[int]) -> list[float]:
        return [values[i] for i in indices if i < len(values) and values[i] is not None]  # type: ignore[misc]

    days: list[ForecastDayDetail] = []
    for day_idx, d in enumerate(dates):
        code = codes[day_idx] if day_idx < len(codes) else None
        t_min = mins[day_idx] if day_idx < len(mins) else None
        t_max = maxs[day_idx] if day_idx < len(maxs) else None
        sunrise_text = sunrises[day_idx] if day_idx < len(sunrises) else None
        sunset_text = sunsets[day_idx] if day_idx < len(sunsets) else None

        indices = indices_by_date.get(d, [])
        window_humidity = _window(humidity, indices)
        window_dew_point = _window(dew_point, indices)
        window_solar = _window(solar, indices)
        window_cloud = _window(cloud, indices)
        window_apparent = _window(apparent, indices)
        window_precip = _window(precipitation, indices)

        hourly_points = tuple(
            ForecastHourlyPoint(
                time_local=datetime.fromisoformat(hourly_times[i]),
                temperature_c=_forecast_value_at(temps, i),
                apparent_temperature_c=_forecast_value_at(apparent, i),
                dew_point_c=_forecast_value_at(dew_point, i),
                relative_humidity_pct=_forecast_value_at(humidity, i),
                shortwave_radiation_wm2=_forecast_value_at(solar, i),
                cloud_cover_pct=_forecast_value_at(cloud, i),
                wind_speed_mps=_forecast_value_at(wind_speed, i),
                wind_direction_deg=_forecast_value_at(wind_direction, i),
                precipitation_mm=_forecast_value_at(precipitation, i),
            )
            for i in indices
        )

        days.append(
            ForecastDayDetail(
                local_date=date.fromisoformat(d),
                weather_code=int(code) if code is not None else None,
                temperature_min_c=float(t_min) if t_min is not None else None,
                temperature_max_c=float(t_max) if t_max is not None else None,
                humidity_min_pct=min(window_humidity) if window_humidity else None,
                humidity_max_pct=max(window_humidity) if window_humidity else None,
                dew_point_min_c=min(window_dew_point) if window_dew_point else None,
                dew_point_max_c=max(window_dew_point) if window_dew_point else None,
                solar_radiation_max_wm2=max(window_solar) if window_solar else None,
                solar_radiation_mean_wm2=(
                    sum(window_solar) / len(window_solar) if window_solar else None
                ),
                cloud_cover_min_pct=min(window_cloud) if window_cloud else None,
                cloud_cover_max_pct=max(window_cloud) if window_cloud else None,
                apparent_temperature_min_c=min(window_apparent) if window_apparent else None,
                apparent_temperature_max_c=max(window_apparent) if window_apparent else None,
                precipitation_mm=sum(window_precip) if window_precip else None,
                sunrise_local=datetime.fromisoformat(sunrise_text) if sunrise_text else None,
                sunset_local=datetime.fromisoformat(sunset_text) if sunset_text else None,
                hourly=hourly_points,
            )
        )
    return days


def fetch_upcoming_conditions(
    lat: float,
    lon: float,
    *,
    tz: str = "UTC",
    days: int = UPCOMING_DETAIL_DAYS,
    client: httpx.Client | None = None,
) -> list[ForecastDayDetail] | None:
    """Calls Open-Meteo's forecast endpoint for `min(days, MAX_FORECAST_DAYS)` days starting
    today, requesting the same richer hourly field set weather.py's own historical-archive call
    does (plus the daily sunrise/sunset weather.py reads from its own `daily` block) -- see this
    module's own docstring for why this is a separate request from `fetch_forecast`'s own coarse
    one, not an extension of it. `wind_speed_unit=ms` for the same SI-units reason weather.py's
    own request sets it. Returns None (never a fabricated forecast) on any HTTP failure; []
    if the response parses but has no usable daily/hourly data."""
    requested_days = max(0, min(days, MAX_FORECAST_DAYS))
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "daily": "weathercode,temperature_2m_max,temperature_2m_min,sunrise,sunset",
        "hourly": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "dew_point_2m,shortwave_radiation,cloud_cover,precipitation,"
            "wind_speed_10m,wind_direction_10m"
        ),
        "forecast_days": str(requested_days),
        "wind_speed_unit": "ms",
        "timezone": tz,
    }

    owns_client = client is None
    http_client = client or httpx.Client(timeout=15.0)
    try:
        response = http_client.get(OPEN_METEO_FORECAST_URL, params=params)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    finally:
        if owns_client:
            http_client.close()

    return parse_upcoming_conditions_response(response.json())
