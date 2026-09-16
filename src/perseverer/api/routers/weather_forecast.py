"""GET /weather/forecast -- the athlete's own home-location forecast, up to Open-Meteo's own
16-day cap, in the athlete's own local timezone (`athlete.timezone`, so a forecast day's
`local_date` lines up with the Week view's own local-date grouping -- see
weather_forecast.py::fetch_forecast's own docstring for why UTC would misalign by a day for
roughly a third of the globe), plus a richer near-term (`upcoming`) detail -- see
weather_forecast.py::fetch_upcoming_conditions's own docstring for why that's a second, separate
Open-Meteo request rather than an extension of the coarse one. See api/schemas/weather_forecast.py
for the response shape and weather_forecast.py for the fetch/parse (deliberately un-cached and
un-archived -- a forecast has no permanent-record concept, unlike weather.py's own past-activity
weather, see that module's own docstring).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.weather_forecast import (
    ForecastDayDetailOut,
    ForecastDayOut,
    ForecastHourlyPointOut,
    WeatherForecastOut,
)
from perseverer.db.schema import athlete
from perseverer.weather_forecast import (
    MAX_FORECAST_DAYS,
    ForecastDayDetail,
    fetch_forecast,
    fetch_upcoming_conditions,
)

router = APIRouter()


def _day_detail_out(d: ForecastDayDetail) -> ForecastDayDetailOut:
    return ForecastDayDetailOut(
        local_date=d.local_date.isoformat(),
        weather_code=d.weather_code,
        temperature_min_c=d.temperature_min_c,
        temperature_max_c=d.temperature_max_c,
        humidity_min_pct=d.humidity_min_pct,
        humidity_max_pct=d.humidity_max_pct,
        dew_point_min_c=d.dew_point_min_c,
        dew_point_max_c=d.dew_point_max_c,
        solar_radiation_max_wm2=d.solar_radiation_max_wm2,
        solar_radiation_mean_wm2=d.solar_radiation_mean_wm2,
        cloud_cover_min_pct=d.cloud_cover_min_pct,
        cloud_cover_max_pct=d.cloud_cover_max_pct,
        apparent_temperature_min_c=d.apparent_temperature_min_c,
        apparent_temperature_max_c=d.apparent_temperature_max_c,
        precipitation_mm=d.precipitation_mm,
        sunrise_local=d.sunrise_local,
        sunset_local=d.sunset_local,
        hourly=[
            ForecastHourlyPointOut(
                time_local=p.time_local,
                temperature_c=p.temperature_c,
                apparent_temperature_c=p.apparent_temperature_c,
                dew_point_c=p.dew_point_c,
                relative_humidity_pct=p.relative_humidity_pct,
                shortwave_radiation_wm2=p.shortwave_radiation_wm2,
                cloud_cover_pct=p.cloud_cover_pct,
                wind_speed_mps=p.wind_speed_mps,
                wind_direction_deg=p.wind_direction_deg,
                precipitation_mm=p.precipitation_mm,
            )
            for p in d.hourly
        ],
    )


@router.get("/weather/forecast")
def get_weather_forecast(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    days: Annotated[int, Query(ge=1, le=MAX_FORECAST_DAYS)] = MAX_FORECAST_DAYS,
) -> WeatherForecastOut:
    """`available=False` -- never a fabricated forecast -- whenever the athlete hasn't set a home
    location (GET/PUT /settings/profile) or the Open-Meteo fetch fails. `days` is clamped to
    Open-Meteo's own 0-16 range at the query-param level already; `fetch_forecast` clamps again
    defensively. `upcoming` is populated from a second, independent Open-Meteo request
    (`fetch_upcoming_conditions`) -- [] whenever that one fails or returns nothing usable, even
    when `days`/`available` succeeded, since the two requests can fail independently of each
    other."""
    row = conn.execute(
        select(athlete.c.home_lat, athlete.c.home_lon, athlete.c.timezone).where(
            athlete.c.id == athlete_id
        )
    ).fetchone()
    if row is None or row.home_lat is None or row.home_lon is None:
        return WeatherForecastOut(available=False)

    forecast_days = fetch_forecast(row.home_lat, row.home_lon, days, tz=row.timezone)
    upcoming = fetch_upcoming_conditions(row.home_lat, row.home_lon, tz=row.timezone)
    upcoming_out = [_day_detail_out(d) for d in upcoming or []]
    if not forecast_days:
        return WeatherForecastOut(available=False, upcoming=upcoming_out)

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
        upcoming=upcoming_out,
    )
