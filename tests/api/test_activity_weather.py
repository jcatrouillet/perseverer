"""Tests for GET /activities/{id}/weather. The actual Open-Meteo fetch/archive/store logic is
exhaustively covered by tests/test_weather.py against a mocked HTTP transport -- these tests
only check the router's own responsibilities: 404, the no-GPS/no-duration "unavailable" case,
and shaping a successful weather.py result into the response schema. `get_or_fetch_activity_weather`
is monkeypatched here rather than mocking HTTP a second time.
"""

from __future__ import annotations

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
