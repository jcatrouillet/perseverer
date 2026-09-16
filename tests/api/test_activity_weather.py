"""Tests for GET /activities/{id}/weather. The actual Open-Meteo fetch/archive/store logic is
exhaustively covered by tests/test_weather.py against a mocked HTTP transport -- these tests
only check the router's own responsibilities: 404, the no-GPS/no-duration "unavailable" case,
shaping a successful weather.py result into the response schema (including the newer scalar
fields and the derived sunset_during_run boolean), and wiring the archived-raw-response read into
the hourly[] trajectory. `get_or_fetch_activity_weather`/`read_archived_open_meteo_response` are
monkeypatched here rather than mocking HTTP a second time.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.weather import WeatherSummary
from tests.api.conftest import seed_activity
from tests.api.test_activities import _add_route


def test_404_for_unknown_activity(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get("/api/v1/activities/doesnotexist/weather", headers=auth_headers)
    assert r.status_code == 404


def test_unavailable_when_activity_has_no_gps_start_point(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    # No _add_route() call -- no route_geom row at all for this activity.

    r = client.get("/api/v1/activities/a1/weather", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["temperature_min_c"] is None


def test_returns_the_fetched_summary_when_available(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_route(engine, activity_id="a1", start_lat=37.36, start_lng=-121.97)

    summary = WeatherSummary(
        temperature_min_c=18.0, temperature_max_c=26.0,
        humidity_min_pct=35.0, humidity_max_pct=60.0, weather_code=2,
        feels_like_c=17.0, wind_speed_mps=4.5, wind_direction_deg=225.0,
    )
    monkeypatch.setattr(
        "perseverer.api.routers.activities.get_or_fetch_activity_weather",
        lambda *args, **kwargs: summary,
    )
    # No raw_object row was ever archived for "a1" in this test's DB (the real fetch function was
    # replaced above), so the real read_archived_open_meteo_response finds nothing -- hourly=[].

    r = client.get("/api/v1/activities/a1/weather", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "available": True,
        "temperature_min_c": 18.0,
        "temperature_max_c": 26.0,
        "humidity_min_pct": 35.0,
        "humidity_max_pct": 60.0,
        "weather_code": 2,
        "feels_like_c": 17.0,
        "wind_speed_mps": 4.5,
        "wind_direction_deg": 225.0,
        "dew_point_min_c": None,
        "dew_point_max_c": None,
        "solar_radiation_max_wm2": None,
        "solar_radiation_mean_wm2": None,
        "cloud_cover_min_pct": None,
        "cloud_cover_max_pct": None,
        "apparent_temperature_min_c": None,
        "apparent_temperature_max_c": None,
        "precipitation_mm": None,
        "sunrise_utc": None,
        "sunset_utc": None,
        "sunset_during_run": None,
        "hourly": [],
    }


def test_unavailable_when_the_fetch_returns_none(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_route(engine, activity_id="a1", start_lat=37.36, start_lng=-121.97)

    monkeypatch.setattr(
        "perseverer.api.routers.activities.get_or_fetch_activity_weather",
        lambda *args, **kwargs: None,
    )

    r = client.get("/api/v1/activities/a1/weather", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_includes_the_new_scalar_fields_when_present(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_route(engine, activity_id="a1", start_lat=37.36, start_lng=-121.97)

    summary = WeatherSummary(
        temperature_min_c=18.0, temperature_max_c=26.0,
        humidity_min_pct=35.0, humidity_max_pct=60.0, weather_code=2,
        dew_point_min_c=10.0, dew_point_max_c=14.0,
        solar_radiation_max_wm2=820.0, solar_radiation_mean_wm2=400.0,
        cloud_cover_min_pct=10.0, cloud_cover_max_pct=80.0,
        apparent_temperature_min_c=17.0, apparent_temperature_max_c=27.0,
        precipitation_mm=3.4,
        sunrise_utc=dt.datetime(2025, 6, 1, 6, 11),
        sunset_utc=dt.datetime(2025, 6, 1, 20, 4),
    )
    monkeypatch.setattr(
        "perseverer.api.routers.activities.get_or_fetch_activity_weather",
        lambda *args, **kwargs: summary,
    )

    r = client.get("/api/v1/activities/a1/weather", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["dew_point_min_c"] == 10.0
    assert body["dew_point_max_c"] == 14.0
    assert body["solar_radiation_max_wm2"] == 820.0
    assert body["solar_radiation_mean_wm2"] == 400.0
    assert body["cloud_cover_min_pct"] == 10.0
    assert body["cloud_cover_max_pct"] == 80.0
    assert body["apparent_temperature_min_c"] == 17.0
    assert body["apparent_temperature_max_c"] == 27.0
    assert body["precipitation_mm"] == 3.4
    assert body["sunrise_utc"] == "2025-06-01T06:11:00Z"
    assert body["sunset_utc"] == "2025-06-01T20:04:00Z"
    # Activity (seed_activity default): starts 2025-06-01T10:00:00Z, 1800s duration --
    # 20:04 falls outside [10:00, 10:30].
    assert body["sunset_during_run"] is False


def test_sunset_during_run_is_true_when_sunset_falls_inside_the_activity_window(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", duration_s=1800.0)
    _add_route(engine, activity_id="a1", start_lat=37.36, start_lng=-121.97)

    # Activity window is [2025-06-01T10:00:00, 2025-06-01T10:30:00] -- sunset at 10:15 falls
    # inside it.
    summary = WeatherSummary(
        temperature_min_c=18.0, temperature_max_c=26.0,
        humidity_min_pct=35.0, humidity_max_pct=60.0, weather_code=2,
        sunset_utc=dt.datetime(2025, 6, 1, 10, 15),
    )
    monkeypatch.setattr(
        "perseverer.api.routers.activities.get_or_fetch_activity_weather",
        lambda *args, **kwargs: summary,
    )

    r = client.get("/api/v1/activities/a1/weather", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["sunset_during_run"] is True


def test_sunset_during_run_is_none_when_sunset_utc_is_none(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_route(engine, activity_id="a1", start_lat=37.36, start_lng=-121.97)

    summary = WeatherSummary(
        temperature_min_c=18.0, temperature_max_c=26.0,
        humidity_min_pct=35.0, humidity_max_pct=60.0, weather_code=2,
    )
    monkeypatch.setattr(
        "perseverer.api.routers.activities.get_or_fetch_activity_weather",
        lambda *args, **kwargs: summary,
    )

    r = client.get("/api/v1/activities/a1/weather", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["sunset_during_run"] is None


def test_includes_the_hourly_trajectory_derived_from_the_archived_raw_response(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")  # starts 2025-06-01T10:00:00Z, 1800s duration
    _add_route(engine, activity_id="a1", start_lat=37.36, start_lng=-121.97)

    summary = WeatherSummary(
        temperature_min_c=18.0, temperature_max_c=26.0,
        humidity_min_pct=35.0, humidity_max_pct=60.0, weather_code=2,
    )
    monkeypatch.setattr(
        "perseverer.api.routers.activities.get_or_fetch_activity_weather",
        lambda *args, **kwargs: summary,
    )
    raw = {
        "hourly": {
            "time": ["2025-06-01T10:00"],
            "temperature_2m": [20.0],
            "relative_humidity_2m": [55.0],
            "apparent_temperature": [19.0],
            "dew_point_2m": [12.0],
            "shortwave_radiation": [300.0],
            "cloud_cover": [40.0],
            "wind_speed_10m": [3.0],
            "wind_direction_10m": [180.0],
            "precipitation": [0.6],
        }
    }
    monkeypatch.setattr(
        "perseverer.api.routers.activities.read_archived_open_meteo_response",
        lambda *args, **kwargs: raw,
    )

    r = client.get("/api/v1/activities/a1/weather", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["hourly"] == [
        {
            "time_utc": "2025-06-01T10:00:00Z",
            "temperature_c": 20.0,
            "apparent_temperature_c": 19.0,
            "dew_point_c": 12.0,
            "relative_humidity_pct": 55.0,
            "shortwave_radiation_wm2": 300.0,
            "cloud_cover_pct": 40.0,
            "wind_speed_mps": 3.0,
            "wind_direction_deg": 180.0,
            "precipitation_mm": 0.6,
        }
    ]
