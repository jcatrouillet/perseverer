"""Open-Meteo forecast for an athlete's own home location -- the future-facing counterpart to
`weather.py`'s past-activity weather, and a deliberately different shape from it. Confirmed live
against the real endpoint (https://api.open-meteo.com/v1/forecast, distinct from the historical
archive API `weather.py` calls) rather than assumed: `forecast_days` is hard-capped to the range
0-16 -- requesting more returns `{"error":true,"reason":"Forecast days is invalid. Allowed range
0 to 16. Given <n>."}` -- and the daily response shape (`daily=weathercode,temperature_2m_max,
temperature_2m_min&timezone=UTC`) returns parallel `time`/`weathercode`/`temperature_2m_max`/
`temperature_2m_min` arrays, the same field-naming convention `weather.py`'s own historical
request already uses.

Deliberately NOT archived raw and NOT cached, unlike every other vendor fetch in this codebase
(CLAUDE.md's "raw first, always" rule exists so a permanent record can be re-derived from an
archive without recontacting a vendor -- a forecast has no such permanent-record concept, since
it's superseded by reality as the date approaches, so archiving it would only accumulate useless
bytes with zero re-derivation benefit). This mirrors the "request-time exception to the rollup
mandate" precedent `vo2max_analysis.py`/`threshold_analysis.py` already establish for a bounded,
occasional live lookup, not a new one.

No API key needed (Open-Meteo's public tier). Weather-code-to-icon mapping stays a frontend
presentation concern (`weatherCode.ts`), same posture as `weather.py`.
"""

from __future__ import annotations

from datetime import date
from typing import Any, NamedTuple

import httpx

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo's own hard cap, confirmed live -- requesting more raises an error response rather
# than silently truncating.
MAX_FORECAST_DAYS = 16


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


def fetch_forecast(
    lat: float, lon: float, days: int, *, client: httpx.Client | None = None
) -> list[ForecastDay] | None:
    """Calls Open-Meteo's forecast endpoint for `min(days, MAX_FORECAST_DAYS)` days starting
    today (Open-Meteo's own `forecast_days` semantics -- today plus `forecast_days - 1` more).
    Returns None (never a fabricated forecast) on any HTTP failure; returns [] if the response
    parses but has no usable daily data."""
    requested_days = max(0, min(days, MAX_FORECAST_DAYS))
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "daily": "weathercode,temperature_2m_max,temperature_2m_min",
        "forecast_days": str(requested_days),
        "timezone": "UTC",
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

    return parse_forecast_response(response.json())
