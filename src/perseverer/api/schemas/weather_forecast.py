"""Request/response models for GET /weather/forecast -- the athlete's own home-location forecast,
up to Open-Meteo's own 16-day cap, plus a richer near-term detail. See weather_forecast.py for
the fetch/parse and db/schema.py::athlete's home_lat/home_lon columns for where the location
comes from.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ForecastDayOut(BaseModel):
    local_date: str  # ISO date
    weather_code: int
    temperature_min_c: float
    temperature_max_c: float


class ForecastHourlyPointOut(BaseModel):
    """One hourly bucket of a single upcoming day. `time_local` is naive, in the athlete's own
    local time -- NOT UTC, unlike ActivityWeatherHourlyPointOut's own `time_utc` -- see
    weather_forecast.py's own module docstring for why."""

    time_local: datetime
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    dew_point_c: float | None = None
    relative_humidity_pct: float | None = None
    shortwave_radiation_wm2: float | None = None
    cloud_cover_pct: float | None = None
    wind_speed_mps: float | None = None
    wind_direction_deg: float | None = None
    precipitation_mm: float | None = None


class ForecastDayDetailOut(BaseModel):
    """The same richer field set ActivityWeatherOut collects for a past run's own window,
    gathered instead for one of the athlete's own upcoming days -- see weather_forecast.py's own
    module docstring for why feels_like_c/wind_speed_mps/wind_direction_deg have no scalar
    equivalent here (hourly carries per-hour wind/apparent-temperature instead) and why every
    datetime field is local, not UTC."""

    local_date: str  # ISO date, in the athlete's own local timezone
    weather_code: int | None = None
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
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
    # A day-total SUM, not a range -- same convention ActivityWeatherOut.precipitation_mm uses.
    # 0.0 is a real reading (no rain forecast); None means no usable precipitation data at all.
    precipitation_mm: float | None = None
    sunrise_local: datetime | None = None
    sunset_local: datetime | None = None
    # The hour-by-hour trajectory across this day -- [] (never fabricated) when nothing usable
    # was returned for it.
    hourly: list[ForecastHourlyPointOut] = []


class WeatherForecastOut(BaseModel):
    # False whenever there's nothing to show -- the athlete hasn't set a home location yet, or
    # the Open-Meteo fetch failed/returned no usable data. Never a fabricated forecast
    # (AGENTS.md's raw-first rule), matching ActivityWeatherOut/ActivityLocationOut's own
    # available-boolean convention.
    available: bool
    days: list[ForecastDayOut] = []
    # The near-term (UPCOMING_DETAIL_DAYS) rich-conditions detail, from a separate Open-Meteo
    # request than `days` above -- see weather_forecast.py's own module docstring for why. []
    # (never fabricated, and independent of `available` above) whenever that second request
    # itself fails or returns nothing usable, even when `days` succeeded.
    upcoming: list[ForecastDayDetailOut] = []


class ForecastSourceActivityOut(BaseModel):
    """The recorded activity whose GPS start point the forecast was fetched for."""

    id: str
    local_date: str | None
    name: str | None
    sport: str
    start_lat: float
    start_lng: float
    # IANA zone (e.g. "America/Los_Angeles") the forecast's local dates/times are expressed in.
    # Requested from Open-Meteo as the activity's own `tz_name` when it has one, else "auto"
    # (resolve from the coordinates) -- and either way reported as the zone name Open-Meteo
    # actually used. None only when the activity has no tz_name *and* the fetch failed, so no zone
    # was ever resolved: never the literal "auto", which is a request mode, not a zone.
    timezone: str | None


class ActivityLocationForecastOut(BaseModel):
    """GET /weather/forecast/last-activity -- the forecast at the athlete's most recent recorded
    activity that has a GPS start point, rather than their configured home location. Same
    `available` convention as WeatherForecastOut: false (never fabricated) when there is no such
    activity or the Open-Meteo fetch failed."""

    available: bool
    source_activity: ForecastSourceActivityOut | None = None
    days: list[ForecastDayOut] = []
    upcoming: list[ForecastDayDetailOut] = []
