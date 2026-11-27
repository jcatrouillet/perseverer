"""GET /weather/forecast -- the athlete's own home-location forecast, up to Open-Meteo's own
16-day cap, in the athlete's own local timezone (`athlete.timezone`, so a forecast day's
`local_date` lines up with the Week view's own local-date grouping -- see
weather_forecast.py::fetch_forecast's own docstring for why UTC would misalign by a day for
roughly a third of the globe), plus a richer near-term (`upcoming`) detail -- see
weather_forecast.py::fetch_upcoming_conditions's own docstring for why that's a second, separate
Open-Meteo request rather than an extension of the coarse one. See api/schemas/weather_forecast.py
for the response shape and weather_forecast.py for the fetch/parse (deliberately un-archived and
always fetched live -- a forecast has no permanent-record concept, unlike weather.py's own
past-activity weather, see that module's own docstring; the one exception is the last answer
weather_forecast_cache.py keeps to serve, flagged stale, when the provider is down).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.weather_forecast import (
    ActivityLocationForecastOut,
    ForecastDayDetailOut,
    ForecastDayOut,
    ForecastHourlyPointOut,
    ForecastSourceActivityOut,
    WeatherForecastOut,
)
from perseverer.db.schema import activity, athlete, route_geom
from perseverer.weather_forecast import (
    MAX_FORECAST_DAYS,
    ForecastDayDetail,
    fetch_forecast,
    fetch_forecast_with_timezone,
    fetch_upcoming_conditions,
)
from perseverer.weather_forecast_cache import (
    KIND_HOME,
    KIND_LAST_ACTIVITY,
    load_forecast,
    now_utc,
    store_forecast,
    today_in,
)

logger = logging.getLogger(__name__)

router = APIRouter()


_today_in = today_in
_now_utc = now_utc


def _remember(
    conn: Connection,
    athlete_id: str,
    kind: str,
    lat: float,
    lon: float,
    tz_name: str | None,
    days: list[ForecastDayOut],
    upcoming: list[ForecastDayDetailOut],
) -> None:
    """Keeps a forecast the provider just answered, for the outage fallback. Best effort: a
    failure to remember it must never fail the request that already has its answer."""
    try:
        store_forecast(
            conn,
            athlete_id,
            kind,
            lat,
            lon,
            days=[d.model_dump(mode="json") for d in days],
            upcoming=[u.model_dump(mode="json") for u in upcoming],
            today=_today_in(tz_name),
            now=_now_utc(),
        )
    except Exception:
        conn.rollback()
        logger.warning("Could not remember the forecast for the outage fallback", exc_info=True)


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
    location (GET/PUT /settings/profile) or the Open-Meteo fetch fails; `reason` says which
    (`no_home_location`, `provider_error`, `no_forecast_data`). When the provider fails but an
    earlier forecast for the same place is kept (weather_forecast_cache.py), that one is served with
    `stale: true` and its `fetched_at` instead. `days` is clamped to
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
        return WeatherForecastOut(available=False, reason="no_home_location")

    # End the read transaction: nothing is held open across the vendor calls below.
    conn.commit()
    forecast_days = fetch_forecast(row.home_lat, row.home_lon, days, tz=row.timezone)
    upcoming = fetch_upcoming_conditions(row.home_lat, row.home_lon, tz=row.timezone)
    upcoming_out = [_day_detail_out(d) for d in upcoming or []]
    if not forecast_days:
        # None is a failed fetch, [] an answer with nothing usable in it. Either way the last
        # forecast the provider did answer is better than nothing, flagged stale.
        cached = load_forecast(
            conn,
            athlete_id,
            KIND_HOME,
            row.home_lat,
            row.home_lon,
            today=_today_in(row.timezone),
            now=_now_utc(),
        )
        if cached is not None:
            return WeatherForecastOut(
                available=True,
                stale=True,
                fetched_at=cached[2],
                days=[ForecastDayOut(**d) for d in cached[0]],
                upcoming=upcoming_out or [ForecastDayDetailOut(**u) for u in cached[1]],
            )
        return WeatherForecastOut(
            available=False,
            reason="provider_error" if forecast_days is None else "no_forecast_data",
            upcoming=upcoming_out,
        )

    days_out = [
        ForecastDayOut(
            local_date=d.local_date.isoformat(),
            weather_code=d.weather_code,
            temperature_min_c=d.temperature_min_c,
            temperature_max_c=d.temperature_max_c,
        )
        for d in forecast_days
    ]
    _remember(
        conn,
        athlete_id,
        KIND_HOME,
        row.home_lat,
        row.home_lon,
        row.timezone,
        days_out,
        upcoming_out,
    )
    return WeatherForecastOut(available=True, days=days_out, upcoming=upcoming_out)


# The near-term window this endpoint serves -- the same UPCOMING_DETAIL_DAYS the home-location
# forecast's own rich `upcoming` block covers, since Open-Meteo hourly detail is only meaningfully
# accurate for a few days out.
_LAST_ACTIVITY_DEFAULT_DAYS = 3


@router.get("/weather/forecast/last-activity")
def get_last_activity_forecast(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    days: Annotated[int, Query(ge=1, le=MAX_FORECAST_DAYS)] = _LAST_ACTIVITY_DEFAULT_DAYS,
) -> ActivityLocationForecastOut:
    """The forecast (default the next 3 days) at the GPS start point of the athlete's most recent
    activity that has one -- for when the athlete is away from home (a trip, a race weekend) and
    their configured home location would answer the wrong question. `days` sets the coarse
    per-day list; `upcoming` (rich hourly detail) always covers the next
    UPCOMING_DETAIL_DAYS. Timezone is requested as the activity's own recorded `tz_name`, else
    Open-Meteo's `auto` (derived from the coordinates), and the zone Open-Meteo actually resolved
    is echoed in `source_activity.timezone`, so `local_date` is the *location's* local date, not
    the athlete's home one. `available=false` -- never a fabricated forecast -- when the athlete
    has no activity with a GPS start point or the coarse fetch fails; unlike
    GET /weather/forecast this deliberately does not fall back to the home location, since the
    caller asked for the last-activity one and a silent substitution would answer a different
    question."""
    row = conn.execute(
        select(
            activity.c.id,
            activity.c.local_date,
            activity.c.name,
            activity.c.sport,
            activity.c.tz_name,
            route_geom.c.start_lat,
            route_geom.c.start_lng,
        )
        .select_from(activity.join(route_geom, route_geom.c.activity_id == activity.c.id))
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            route_geom.c.start_lat.is_not(None),
            route_geom.c.start_lng.is_not(None),
        )
        .order_by(activity.c.start_time_utc.desc())
        .limit(1)
    ).fetchone()
    if row is None:
        return ActivityLocationForecastOut(available=False, reason="no_gps_activity")

    requested_tz = row.tz_name or "auto"
    conn.commit()  # end the read transaction before the vendor calls
    fetched = fetch_forecast_with_timezone(row.start_lat, row.start_lng, days, tz=requested_tz)
    forecast_days, resolved_tz = fetched if fetched is not None else (None, None)
    # Report the zone Open-Meteo actually used -- the real name behind an "auto" request -- and
    # never the literal "auto", which is a request mode, not a timezone. Activities here don't
    # store a tz_name, so "auto" is the common case.
    source = ForecastSourceActivityOut(
        id=row.id,
        local_date=row.local_date,
        name=row.name,
        sport=row.sport,
        start_lat=row.start_lat,
        start_lng=row.start_lng,
        timezone=resolved_tz or row.tz_name,
    )
    upcoming = fetch_upcoming_conditions(
        row.start_lat, row.start_lng, tz=resolved_tz or requested_tz
    )
    upcoming_out = [_day_detail_out(d) for d in upcoming or []]
    if not forecast_days:
        cached = load_forecast(
            conn,
            athlete_id,
            KIND_LAST_ACTIVITY,
            row.start_lat,
            row.start_lng,
            today=_today_in(resolved_tz or row.tz_name),
            now=_now_utc(),
        )
        if cached is not None:
            return ActivityLocationForecastOut(
                available=True,
                stale=True,
                fetched_at=cached[2],
                source_activity=source,
                days=[ForecastDayOut(**d) for d in cached[0]],
                upcoming=upcoming_out or [ForecastDayDetailOut(**u) for u in cached[1]],
            )
        return ActivityLocationForecastOut(
            available=False,
            reason="provider_error" if fetched is None else "no_forecast_data",
            source_activity=source,
            upcoming=upcoming_out,
        )
    days_out = [
        ForecastDayOut(
            local_date=d.local_date.isoformat(),
            weather_code=d.weather_code,
            temperature_min_c=d.temperature_min_c,
            temperature_max_c=d.temperature_max_c,
        )
        for d in forecast_days
    ]
    _remember(
        conn,
        athlete_id,
        KIND_LAST_ACTIVITY,
        row.start_lat,
        row.start_lng,
        resolved_tz or row.tz_name,
        days_out,
        upcoming_out,
    )
    return ActivityLocationForecastOut(
        available=True, source_activity=source, days=days_out, upcoming=upcoming_out
    )
