"""Tests for weather_forecast.py: the pure Open-Meteo forecast-response parser and the
fetch orchestration (mocked HTTP, no real network calls, no archiving/caching -- see this
module's own docstring for why a forecast is deliberately never persisted)."""

from __future__ import annotations

from datetime import date

import httpx

from perseverer.weather_forecast import (
    MAX_FORECAST_DAYS,
    ForecastDay,
    fetch_forecast,
    parse_forecast_response,
)


class TestParseForecastResponse:
    def test_parses_a_full_daily_block(self) -> None:
        raw = {
            "daily": {
                "time": ["2026-09-14", "2026-09-15"],
                "weathercode": [3, 61],
                "temperature_2m_max": [28.4, 20.1],
                "temperature_2m_min": [18.2, 14.0],
            }
        }
        days = parse_forecast_response(raw)
        assert days == [
            ForecastDay(date(2026, 9, 14), 3, 18.2, 28.4),
            ForecastDay(date(2026, 9, 15), 61, 14.0, 20.1),
        ]

    def test_returns_empty_list_when_no_daily_block(self) -> None:
        assert parse_forecast_response({}) == []

    def test_skips_a_day_missing_any_field_rather_than_fabricating(self) -> None:
        raw = {
            "daily": {
                "time": ["2026-09-14", "2026-09-15"],
                "weathercode": [3, None],
                "temperature_2m_max": [28.4, 20.1],
                "temperature_2m_min": [18.2, 14.0],
            }
        }
        days = parse_forecast_response(raw)
        assert len(days) == 1
        assert days[0].local_date == date(2026, 9, 14)


class TestFetchForecast:
    def test_fetches_and_parses_on_success(self) -> None:
        raw = {
            "daily": {
                "time": ["2026-09-14"],
                "weathercode": [0],
                "temperature_2m_max": [25.0],
                "temperature_2m_min": [15.0],
            }
        }
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(request.url.params)
            return httpx.Response(200, json=raw)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        days = fetch_forecast(48.8566, 2.3522, 5, client=client)

        assert days == [ForecastDay(date(2026, 9, 14), 0, 15.0, 25.0)]
        assert captured["forecast_days"] == "5"
        assert captured["latitude"] == "48.8566"
        assert captured["longitude"] == "2.3522"
        assert captured["timezone"] == "UTC"

    def test_passes_the_given_timezone_through_to_open_meteo(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(request.url.params)
            return httpx.Response(200, json={"daily": {}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        fetch_forecast(48.8566, 2.3522, 5, tz="America/Los_Angeles", client=client)

        assert captured["timezone"] == "America/Los_Angeles"

    def test_clamps_days_to_open_meteos_own_cap(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(request.url.params)
            return httpx.Response(200, json={"daily": {}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        fetch_forecast(0.0, 0.0, 999, client=client)

        assert captured["forecast_days"] == str(MAX_FORECAST_DAYS)

    def test_returns_none_on_http_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert fetch_forecast(0.0, 0.0, 5, client=client) is None

    def test_returns_empty_list_when_response_has_no_usable_data(self) -> None:
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        assert fetch_forecast(0.0, 0.0, 5, client=client) == []
