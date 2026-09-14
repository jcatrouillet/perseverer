"""GET /weather/forecast -- the athlete's own home-location forecast, up to Open-Meteo's own
16-day cap. See api/schemas/weather_forecast.py for the response shape and weather_forecast.py
for the fetch/parse (deliberately un-cached and un-archived -- a forecast has no permanent-record
concept, unlike weather.py's own past-activity weather, see that module's own docstring).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.weather_forecast import ForecastDayOut, WeatherForecastOut
from perseverer.db.schema import athlete
from perseverer.weather_forecast import MAX_FORECAST_DAYS, fetch_forecast

router = APIRouter()


@router.get("/weather/forecast")
def get_weather_forecast(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    days: Annotated[int, Query(ge=1, le=MAX_FORECAST_DAYS)] = MAX_FORECAST_DAYS,
) -> WeatherForecastOut:
    """`available=False` -- never a fabricated forecast -- whenever the athlete hasn't set a home
    location (GET/PUT /settings/profile) or the Open-Meteo fetch fails. `days` is clamped to
    Open-Meteo's own 0-16 range at the query-param level already; `fetch_forecast` clamps again
    defensively."""
    row = conn.execute(
        select(athlete.c.home_lat, athlete.c.home_lon).where(athlete.c.id == athlete_id)
    ).fetchone()
    if row is None or row.home_lat is None or row.home_lon is None:
        return WeatherForecastOut(available=False)

    forecast_days = fetch_forecast(row.home_lat, row.home_lon, days)
    if not forecast_days:
        return WeatherForecastOut(available=False)

    return WeatherForecastOut(
        available=True,
        days=[
            ForecastDayOut(
                local_date=d.local_date.isoformat(),
                weather_code=d.weather_code,
                temperature_min_c=d.temperature_min_c,
                temperature_max_c=d.temperature_max_c,
            )
            for d in forecast_days
        ],
    )
