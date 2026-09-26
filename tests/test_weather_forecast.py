"""Tests for weather_forecast.py: the pure Open-Meteo forecast-response parser and the
fetch orchestration (mocked HTTP, no real network calls, no archiving/caching -- see this
module's own docstring for why a forecast is deliberately never persisted)."""

from __future__ import annotations

from datetime import date, datetime

import httpx

from perseverer.weather_forecast import (
    MAX_FORECAST_DAYS,
    UPCOMING_DETAIL_DAYS,
    ForecastDay,
    ForecastHourlyPoint,
    fetch_forecast,
    fetch_forecast_with_timezone,
    fetch_upcoming_conditions,
    parse_forecast_response,
    parse_resolved_timezone,
    parse_upcoming_conditions_response,
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


def _sample_upcoming_response() -> dict[str, object]:
    """Two days' worth of daily + hourly data -- day 1 (2026-09-14) has 3 hourly buckets, day 2
    (2026-09-15) has 2, so aggregation-per-day is genuinely exercised, not just a single-bucket
    passthrough."""
    return {
        "daily": {
            "time": ["2026-09-14", "2026-09-15"],
            "weathercode": [3, 61],
            "temperature_2m_max": [28.4, 20.1],
            "temperature_2m_min": [18.2, 14.0],
            "sunrise": ["2026-09-14T06:30", "2026-09-15T06:31"],
            "sunset": ["2026-09-14T19:45", "2026-09-15T19:44"],
        },
        "hourly": {
            "time": [
                "2026-09-14T00:00", "2026-09-14T01:00", "2026-09-14T02:00",
                "2026-09-15T00:00", "2026-09-15T01:00",
            ],
            "temperature_2m": [18.0, 19.0, 20.0, 14.0, 15.0],
            "relative_humidity_2m": [70.0, 65.0, 60.0, 80.0, 78.0],
            "apparent_temperature": [17.0, 18.5, 19.5, 13.0, 14.0],
            "dew_point_2m": [12.0, 12.5, 13.0, 10.0, 10.5],
            "shortwave_radiation": [0.0, 200.0, 500.0, 0.0, 100.0],
            "cloud_cover": [80.0, 50.0, 20.0, 90.0, 70.0],
            "precipitation": [0.0, 0.5, 0.0, 1.0, 0.0],
            "wind_speed_10m": [2.0, 3.0, 4.0, 1.5, 2.5],
            "wind_direction_10m": [90.0, 100.0, 110.0, 200.0, 210.0],
        },
    }


class TestParseUpcomingConditionsResponse:
    def test_reduces_each_days_own_hourly_group_to_min_max_sum_aggregates(self) -> None:
        days = parse_upcoming_conditions_response(_sample_upcoming_response())

        assert len(days) == 2
        day1, day2 = days
        assert day1.local_date == date(2026, 9, 14)
        assert day1.weather_code == 3
        assert day1.temperature_min_c == 18.2
        assert day1.temperature_max_c == 28.4
        assert day1.humidity_min_pct == 60.0
        assert day1.humidity_max_pct == 70.0
        assert day1.dew_point_min_c == 12.0
        assert day1.dew_point_max_c == 13.0
        assert day1.solar_radiation_max_wm2 == 500.0
        assert day1.solar_radiation_mean_wm2 == (0.0 + 200.0 + 500.0) / 3
        assert day1.cloud_cover_min_pct == 20.0
        assert day1.cloud_cover_max_pct == 80.0
        assert day1.apparent_temperature_min_c == 17.0
        assert day1.apparent_temperature_max_c == 19.5
        # A window SUM, not a range -- same convention weather.py's own precipitation_mm uses.
        assert day1.precipitation_mm == 0.0 + 0.5 + 0.0
        assert day1.sunrise_local == datetime(2026, 9, 14, 6, 30)
        assert day1.sunset_local == datetime(2026, 9, 14, 19, 45)

        assert day2.local_date == date(2026, 9, 15)
        assert day2.weather_code == 61
        assert day2.temperature_min_c == 14.0
        assert day2.temperature_max_c == 20.1
        assert day2.precipitation_mm == 1.0 + 0.0

    def test_hourly_points_are_grouped_per_day_in_order(self) -> None:
        days = parse_upcoming_conditions_response(_sample_upcoming_response())

        assert len(days[0].hourly) == 3
        assert [p.time_local for p in days[0].hourly] == [
            datetime(2026, 9, 14, 0, 0),
            datetime(2026, 9, 14, 1, 0),
            datetime(2026, 9, 14, 2, 0),
        ]
        assert days[0].hourly[1] == ForecastHourlyPoint(
            time_local=datetime(2026, 9, 14, 1, 0),
            temperature_c=19.0,
            apparent_temperature_c=18.5,
            dew_point_c=12.5,
            relative_humidity_pct=65.0,
            shortwave_radiation_wm2=200.0,
            cloud_cover_pct=50.0,
            wind_speed_mps=3.0,
            wind_direction_deg=100.0,
            precipitation_mm=0.5,
        )

        assert len(days[1].hourly) == 2
        assert [p.time_local for p in days[1].hourly] == [
            datetime(2026, 9, 15, 0, 0),
            datetime(2026, 9, 15, 1, 0),
        ]

    def test_precipitation_is_a_real_zero_distinct_from_no_data(self) -> None:
        raw = _sample_upcoming_response()
        raw["hourly"]["precipitation"] = [0.0, 0.0, 0.0, 1.0, 0.0]  # type: ignore[index]

        days = parse_upcoming_conditions_response(raw)

        assert days[0].precipitation_mm == 0.0

    def test_returns_empty_list_when_daily_block_is_missing(self) -> None:
        raw = _sample_upcoming_response()
        del raw["daily"]
        assert parse_upcoming_conditions_response(raw) == []

    def test_returns_empty_list_when_hourly_block_is_missing(self) -> None:
        raw = _sample_upcoming_response()
        del raw["hourly"]
        assert parse_upcoming_conditions_response(raw) == []

    def test_a_daily_entry_with_no_matching_hourly_points_still_gets_an_entry(self) -> None:
        # A daily date with genuinely no hourly rows grouped under it -- every aggregate field
        # stays None (never fabricated), the daily-only fields (weather_code/temperature range/
        # sunrise/sunset) still come through from the real daily entry.
        raw: dict[str, object] = {
            "daily": {
                "time": ["2026-09-14"],
                "weathercode": [3],
                "temperature_2m_max": [28.4],
                "temperature_2m_min": [18.2],
            },
            "hourly": {"time": [], "temperature_2m": []},
        }
        days = parse_upcoming_conditions_response(raw)

        assert len(days) == 1
        assert days[0].weather_code == 3
        assert days[0].temperature_min_c == 18.2
        assert days[0].humidity_min_pct is None
        assert days[0].precipitation_mm is None
        assert days[0].hourly == ()


class TestFetchUpcomingConditions:
    def test_fetches_and_parses_on_success(self) -> None:
        raw = _sample_upcoming_response()
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(request.url.params)
            return httpx.Response(200, json=raw)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        days = fetch_upcoming_conditions(48.8566, 2.3522, tz="Europe/Paris", client=client)

        assert days is not None
        assert len(days) == 2
        assert captured["forecast_days"] == str(UPCOMING_DETAIL_DAYS)
        assert captured["latitude"] == "48.8566"
        assert captured["longitude"] == "2.3522"
        assert captured["timezone"] == "Europe/Paris"
        assert captured["wind_speed_unit"] == "ms"
        assert "precipitation" in captured["hourly"]
        assert "dew_point_2m" in captured["hourly"]
        assert "shortwave_radiation" in captured["hourly"]
        assert "cloud_cover" in captured["hourly"]
        assert "sunrise" in captured["daily"]
        assert "sunset" in captured["daily"]

    def test_clamps_days_to_open_meteos_own_cap(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(request.url.params)
            return httpx.Response(200, json={"daily": {}, "hourly": {}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        fetch_upcoming_conditions(0.0, 0.0, days=999, client=client)

        assert captured["forecast_days"] == str(MAX_FORECAST_DAYS)

    def test_returns_none_on_http_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert fetch_upcoming_conditions(0.0, 0.0, client=client) is None

    def test_returns_empty_list_when_response_has_no_usable_data(self) -> None:
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        assert fetch_upcoming_conditions(0.0, 0.0, client=client) == []


class TestResolvedTimezone:
    def test_reads_the_zone_open_meteo_resolved(self) -> None:
        assert parse_resolved_timezone({"timezone": "America/Los_Angeles"}) == (
            "America/Los_Angeles"
        )

    def test_none_when_absent_or_not_a_name(self) -> None:
        assert parse_resolved_timezone({}) is None
        assert parse_resolved_timezone({"timezone": ""}) is None
        assert parse_resolved_timezone({"timezone": 7}) is None

    def test_fetch_returns_the_zone_behind_an_auto_request(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(request.url.params)
            return httpx.Response(
                200,
                json={
                    "timezone": "America/Los_Angeles",
                    "daily": {
                        "time": ["2026-09-14"],
                        "weathercode": [0],
                        "temperature_2m_max": [25.0],
                        "temperature_2m_min": [15.0],
                    },
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = fetch_forecast_with_timezone(37.36, -121.97, 3, tz="auto", client=client)

        assert captured["timezone"] == "auto"
        assert result == (
            [ForecastDay(date(2026, 9, 14), 0, 15.0, 25.0)],
            "America/Los_Angeles",
        )

    def test_fetch_failure_is_none(self) -> None:
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        assert fetch_forecast_with_timezone(0.0, 0.0, 3, client=client) is None
