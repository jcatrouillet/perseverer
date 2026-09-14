"""Tests for GET /weather/forecast. The actual Open-Meteo fetch/parse logic is exhaustively
covered by tests/test_weather_forecast.py against a mocked HTTP transport -- these tests only
check the router's own responsibilities: no-home-location "unavailable", shaping a successful
fetch into the response schema, and the `days` query param. `fetch_forecast` is monkeypatched
here rather than mocking HTTP a second time.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import athlete
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.weather_forecast import ForecastDay


def _set_home_location(engine: Engine, lat: float, lon: float) -> None:
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == DEFAULT_ATHLETE_ID)
            .values(home_lat=lat, home_lon=lon)
        )
        conn.commit()


def test_unavailable_when_no_home_location_set(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/weather/forecast", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"available": False, "days": []}


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
    assert r.json() == {"available": False, "days": []}


def test_days_query_param_is_passed_through(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_home_location(engine, 48.8566, 2.3522)
    captured: dict[str, object] = {}

    def fake_fetch(lat: float, lon: float, days: int) -> list[ForecastDay]:
        captured["lat"] = lat
        captured["lon"] = lon
        captured["days"] = days
        return []

    monkeypatch.setattr("perseverer.api.routers.weather_forecast.fetch_forecast", fake_fetch)

    r = client.get("/api/v1/weather/forecast?days=5", headers=auth_headers)
    assert r.status_code == 200
    assert captured == {"lat": 48.8566, "lon": 2.3522, "days": 5}


def test_days_query_param_is_bounded(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/api/v1/weather/forecast?days=0", headers=auth_headers).status_code == 422
    assert (
        client.get("/api/v1/weather/forecast?days=17", headers=auth_headers).status_code == 422
    )


def test_forecast_endpoint_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/weather/forecast").status_code in (401, 403)
