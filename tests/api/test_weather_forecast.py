"""Tests for GET /weather/forecast. The actual Open-Meteo fetch/parse logic is exhaustively
covered by tests/test_weather_forecast.py against a mocked HTTP transport -- these tests only
check the router's own responsibilities: no-home-location "unavailable", shaping a successful
fetch into the response schema, the `days` query param, and that `upcoming` comes from a second,
independent fetch (`fetch_upcoming_conditions`) that can succeed/fail on its own regardless of
`fetch_forecast`. Both fetch functions are monkeypatched here rather than mocking HTTP twice --
`fetch_upcoming_conditions` is monkeypatched to return [] by default (an autouse fixture) so no
test accidentally makes a real network call just by not mentioning it.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import activity as activity_table
from perseverer.db.schema import athlete, route_geom
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.weather_forecast import ForecastDay, ForecastDayDetail, ForecastHourlyPoint
from tests.api.conftest import seed_activity


@pytest.fixture(autouse=True)
def _no_upcoming_conditions_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_upcoming_conditions",
        lambda *args, **kwargs: [],
    )


def _set_home_location(
    engine: Engine, lat: float, lon: float, *, timezone: str | None = None
) -> None:
    values: dict[str, object] = {"home_lat": lat, "home_lon": lon}
    if timezone is not None:
        values["timezone"] = timezone
    with engine.connect() as conn:
        conn.execute(athlete.update().where(athlete.c.id == DEFAULT_ATHLETE_ID).values(**values))
        conn.commit()


def test_unavailable_when_no_home_location_set(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/weather/forecast", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"available": False, "days": [], "upcoming": []}


def test_returns_the_fetched_forecast_when_available(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_home_location(engine, 48.8566, 2.3522)
    days = [
        ForecastDay(date(2026, 9, 14), 3, 18.0, 26.0),
        ForecastDay(date(2026, 9, 15), 61, 14.0, 20.0),
    ]
    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_forecast",
        lambda *args, **kwargs: days,
    )

    r = client.get("/api/v1/weather/forecast", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {
        "available": True,
        "days": [
            {
                "local_date": "2026-09-14",
                "weather_code": 3,
                "temperature_min_c": 18.0,
                "temperature_max_c": 26.0,
            },
            {
                "local_date": "2026-09-15",
                "weather_code": 61,
                "temperature_min_c": 14.0,
                "temperature_max_c": 20.0,
            },
        ],
        "upcoming": [],
    }


def test_unavailable_when_fetch_fails(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_home_location(engine, 48.8566, 2.3522)
    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_forecast",
        lambda *args, **kwargs: None,
    )

    r = client.get("/api/v1/weather/forecast", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"available": False, "days": [], "upcoming": []}


def test_days_query_param_is_passed_through(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_home_location(engine, 48.8566, 2.3522)
    captured: dict[str, object] = {}

    def fake_fetch(lat: float, lon: float, days: int, *, tz: str) -> list[ForecastDay]:
        captured["lat"] = lat
        captured["lon"] = lon
        captured["days"] = days
        captured["tz"] = tz
        return []

    monkeypatch.setattr("perseverer.api.routers.weather_forecast.fetch_forecast", fake_fetch)

    r = client.get("/api/v1/weather/forecast?days=5", headers=auth_headers)
    assert r.status_code == 200
    assert captured == {"lat": 48.8566, "lon": 2.3522, "days": 5, "tz": "UTC"}


def test_athletes_own_timezone_is_passed_to_the_forecast_fetch(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_home_location(engine, 48.8566, 2.3522, timezone="America/Los_Angeles")
    captured: dict[str, object] = {}

    def fake_fetch(lat: float, lon: float, days: int, *, tz: str) -> list[ForecastDay]:
        captured["tz"] = tz
        return []

    monkeypatch.setattr("perseverer.api.routers.weather_forecast.fetch_forecast", fake_fetch)

    r = client.get("/api/v1/weather/forecast", headers=auth_headers)
    assert r.status_code == 200
    assert captured["tz"] == "America/Los_Angeles"


def test_days_query_param_is_bounded(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/api/v1/weather/forecast?days=0", headers=auth_headers).status_code == 422
    assert client.get("/api/v1/weather/forecast?days=17", headers=auth_headers).status_code == 422


def test_forecast_endpoint_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/weather/forecast").status_code in (401, 403)


def test_upcoming_is_shaped_from_the_separate_fetch(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_home_location(engine, 48.8566, 2.3522)
    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_forecast",
        lambda *args, **kwargs: [ForecastDay(date(2026, 9, 14), 3, 18.0, 26.0)],
    )
    detail = ForecastDayDetail(
        local_date=date(2026, 9, 14),
        weather_code=3,
        temperature_min_c=18.0,
        temperature_max_c=26.0,
        humidity_min_pct=40.0,
        humidity_max_pct=70.0,
        dew_point_min_c=10.0,
        dew_point_max_c=14.0,
        solar_radiation_max_wm2=800.0,
        solar_radiation_mean_wm2=300.0,
        cloud_cover_min_pct=10.0,
        cloud_cover_max_pct=60.0,
        apparent_temperature_min_c=17.0,
        apparent_temperature_max_c=27.0,
        precipitation_mm=0.4,
        sunrise_local=datetime(2026, 9, 14, 6, 30),
        sunset_local=datetime(2026, 9, 14, 19, 45),
        hourly=(
            ForecastHourlyPoint(
                time_local=datetime(2026, 9, 14, 7, 0),
                temperature_c=19.0,
                apparent_temperature_c=18.0,
                dew_point_c=11.0,
                relative_humidity_pct=65.0,
                shortwave_radiation_wm2=100.0,
                cloud_cover_pct=50.0,
                wind_speed_mps=3.0,
                wind_direction_deg=90.0,
                precipitation_mm=0.1,
            ),
        ),
    )
    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_upcoming_conditions",
        lambda *args, **kwargs: [detail],
    )

    r = client.get("/api/v1/weather/forecast", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["upcoming"]) == 1
    day = body["upcoming"][0]
    assert day["local_date"] == "2026-09-14"
    assert day["humidity_min_pct"] == 40.0
    assert day["precipitation_mm"] == 0.4
    assert day["sunrise_local"] == "2026-09-14T06:30:00"
    assert len(day["hourly"]) == 1
    assert day["hourly"][0]["temperature_c"] == 19.0
    assert day["hourly"][0]["precipitation_mm"] == 0.1


def test_upcoming_is_empty_when_that_fetch_fails_even_though_the_coarse_forecast_succeeds(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_home_location(engine, 48.8566, 2.3522)
    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_forecast",
        lambda *args, **kwargs: [ForecastDay(date(2026, 9, 14), 3, 18.0, 26.0)],
    )
    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_upcoming_conditions",
        lambda *args, **kwargs: None,
    )

    r = client.get("/api/v1/weather/forecast", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["upcoming"] == []


def test_upcoming_is_populated_even_when_the_coarse_forecast_is_unavailable(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two fetches are genuinely independent -- available=False (the coarse forecast's own
    failure) doesn't blank out a real upcoming-conditions result."""
    _set_home_location(engine, 48.8566, 2.3522)
    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_forecast",
        lambda *args, **kwargs: None,
    )
    detail = ForecastDayDetail(
        local_date=date(2026, 9, 14),
        weather_code=3,
        temperature_min_c=18.0,
        temperature_max_c=26.0,
    )
    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_upcoming_conditions",
        lambda *args, **kwargs: [detail],
    )

    r = client.get("/api/v1/weather/forecast", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert len(body["upcoming"]) == 1
    assert body["upcoming"][0]["local_date"] == "2026-09-14"


# --- GET /weather/forecast/last-activity ---------------------------------------------------


def _seed_located_activity(
    engine: Engine,
    activity_id: str,
    *,
    start: datetime,
    lat: float | None,
    lng: float | None,
    tz_name: str | None = None,
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id=activity_id)
        conn.execute(
            activity_table.update()
            .where(activity_table.c.id == activity_id)
            .values(start_time_utc=start, tz_name=tz_name)
        )
        conn.execute(
            route_geom.insert().values(
                activity_id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_lat=lat,
                start_lng=lng,
            )
        )
        conn.commit()


def test_last_activity_forecast_unavailable_without_a_located_activity(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="no-gps")  # an activity with no route_geom row at all
    r = client.get("/api/v1/weather/forecast/last-activity", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {
        "available": False,
        "source_activity": None,
        "days": [],
        "upcoming": [],
    }


def test_last_activity_forecast_uses_the_most_recent_located_activity(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_home_location(engine, 1.0, 1.0)  # must NOT be used
    _seed_located_activity(engine, "older", start=datetime(2026, 9, 1, 8), lat=37.36, lng=-121.97)
    _seed_located_activity(
        engine,
        "newest-with-gps",
        start=datetime(2026, 9, 10, 8),
        lat=48.85,
        lng=2.35,
        tz_name="Europe/Paris",
    )
    # More recent than both, but no GPS start point -- must be skipped, not chosen.
    _seed_located_activity(
        engine, "newest-no-gps", start=datetime(2026, 9, 12, 8), lat=None, lng=None
    )

    calls: list[tuple[float, float, int, str]] = []

    def fake_fetch(
        lat: float, lon: float, days: int, *, tz: str = "UTC"
    ) -> tuple[list[ForecastDay], str | None]:
        calls.append((lat, lon, days, tz))
        return [ForecastDay(date(2026, 9, 14), 3, 12.0, 19.0)], "Europe/Paris"

    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_forecast_with_timezone", fake_fetch
    )

    r = client.get("/api/v1/weather/forecast/last-activity", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert calls == [(48.85, 2.35, 3, "Europe/Paris")]
    assert body["available"] is True
    assert body["source_activity"]["id"] == "newest-with-gps"
    assert body["source_activity"]["timezone"] == "Europe/Paris"
    assert body["days"] == [
        {
            "local_date": "2026-09-14",
            "weather_code": 3,
            "temperature_min_c": 12.0,
            "temperature_max_c": 19.0,
        }
    ]


def test_last_activity_forecast_falls_back_to_auto_timezone(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_located_activity(engine, "a1", start=datetime(2026, 9, 1, 8), lat=10.0, lng=20.0)
    seen: list[str] = []
    upcoming_tz: list[str] = []

    def fake_fetch(
        lat: float, lon: float, days: int, *, tz: str = "UTC"
    ) -> tuple[list[ForecastDay], str | None]:
        seen.append(tz)
        return [ForecastDay(date(2026, 9, 14), 0, 1.0, 2.0)], "America/Los_Angeles"

    def fake_upcoming(lat: float, lon: float, *, tz: str = "UTC") -> list[ForecastDayDetail]:
        upcoming_tz.append(tz)
        return []

    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_forecast_with_timezone", fake_fetch
    )
    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_upcoming_conditions", fake_upcoming
    )
    r = client.get("/api/v1/weather/forecast/last-activity?days=5", headers=auth_headers)
    assert r.status_code == 200
    # No stored tz_name -> "auto" is what is *requested*, but the response reports the real zone
    # Open-Meteo resolved -- never the literal "auto" -- and the second request reuses it.
    assert seen == ["auto"]
    assert r.json()["source_activity"]["timezone"] == "America/Los_Angeles"
    assert upcoming_tz == ["America/Los_Angeles"]


def test_last_activity_forecast_unavailable_when_fetch_fails_but_names_the_source(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_located_activity(engine, "a1", start=datetime(2026, 9, 1, 8), lat=10.0, lng=20.0)
    monkeypatch.setattr(
        "perseverer.api.routers.weather_forecast.fetch_forecast_with_timezone",
        lambda *a, **k: None,
    )
    r = client.get("/api/v1/weather/forecast/last-activity", headers=auth_headers)
    assert r.json()["available"] is False
    assert r.json()["source_activity"]["id"] == "a1"
    # Nothing was resolved and the activity stores no tz_name: unknown, not the literal "auto".
    assert r.json()["source_activity"]["timezone"] is None


def test_last_activity_forecast_requires_auth_and_bounds_days(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.get("/api/v1/weather/forecast/last-activity").status_code == 401
    r = client.get("/api/v1/weather/forecast/last-activity?days=17", headers=auth_headers)
    assert r.status_code == 422
