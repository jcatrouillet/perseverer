"""Request/response models for GET /weather/forecast -- the athlete's own home-location forecast,
up to Open-Meteo's own 16-day cap. See weather_forecast.py for the fetch/parse and
db/schema.py::athlete's home_lat/home_lon columns for where the location comes from.
"""

from __future__ import annotations

from pydantic import BaseModel


class ForecastDayOut(BaseModel):
    local_date: str  # ISO date
    weather_code: int
    temperature_min_c: float
    temperature_max_c: float


class WeatherForecastOut(BaseModel):
    # False whenever there's nothing to show -- the athlete hasn't set a home location yet, or
    # the Open-Meteo fetch failed/returned no usable data. Never a fabricated forecast
    # (CLAUDE.md's raw-first rule), matching ActivityWeatherOut/ActivityLocationOut's own
    # available-boolean convention.
    available: bool
    days: list[ForecastDayOut] = []
