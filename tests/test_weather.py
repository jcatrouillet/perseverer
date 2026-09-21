"""Tests for weather.py: the pure Open-Meteo response parser, and the read-through-cache
fetch/archive/store orchestration (mocked HTTP, no real network calls). See AGENTS.md's
"raw first, always" rule -- every fetched response must be archived before being parsed, and
re-fetching the same activity's weather must never happen once it's cached.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
from pathlib import Path

import httpx
from sqlalchemy import Connection, Engine, select

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, athlete, metadata, raw_object
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.weather import (
    METRIC_FEELS_LIKE_C,
    METRIC_HUMIDITY_MAX_PCT,
    METRIC_HUMIDITY_MIN_PCT,
    METRIC_TEMPERATURE_MAX_C,
    METRIC_TEMPERATURE_MIN_C,
    METRIC_WEATHER_CODE,
    METRIC_WIND_DIRECTION_DEG,
    METRIC_WIND_SPEED_MPS,
    HourlyWeatherPoint,
    WeatherSummary,
    get_or_fetch_activity_weather,
    parse_open_meteo_hourly_series,
    parse_open_meteo_response,
    read_archived_open_meteo_response,
)


def _engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    return engine


def _seed_activity(conn: Connection, activity_id: str, start: dt.datetime) -> None:
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start,
            utc_offset_s=0,
            local_date=start.date().isoformat(),
            sport="running",
            duration_s=3600.0,
            distance_m=10000.0,
            primary_source="fit_folder",
            created_at=start,
            updated_at=start,
        )
    )
    conn.commit()


def _sample_response(
    hours: list[str],
    temps: list[float],
    humidity: list[float],
    codes: list[int],
    feels_like: list[float] | None = None,
    wind_speed: list[float] | None = None,
    wind_direction: list[float] | None = None,
    dew_point: list[float | None] | None = None,
    solar: list[float | None] | None = None,
    cloud: list[float | None] | None = None,
    precipitation: list[float | None] | None = None,
    daily_dates: list[str] | None = None,
    sunrise: list[str] | None = None,
    sunset: list[str] | None = None,
) -> dict[str, object]:
    hourly: dict[str, object] = {
        "time": hours,
        "temperature_2m": temps,
        "relative_humidity_2m": humidity,
        "weathercode": codes,
    }
    if feels_like is not None:
        hourly["apparent_temperature"] = feels_like
    if wind_speed is not None:
        hourly["wind_speed_10m"] = wind_speed
    if wind_direction is not None:
        hourly["wind_direction_10m"] = wind_direction
    if dew_point is not None:
        hourly["dew_point_2m"] = dew_point
    if solar is not None:
        hourly["shortwave_radiation"] = solar
    if cloud is not None:
        hourly["cloud_cover"] = cloud
    if precipitation is not None:
        hourly["precipitation"] = precipitation
    response: dict[str, object] = {"latitude": 37.36, "longitude": -121.97, "hourly": hourly}
    if daily_dates is not None:
        daily: dict[str, object] = {"time": daily_dates}
        if sunrise is not None:
            daily["sunrise"] = sunrise
        if sunset is not None:
            daily["sunset"] = sunset
        response["daily"] = daily
    return response


class TestParseOpenMeteoResponse:
    def test_reduces_hourly_arrays_to_min_max_over_the_activity_window(self) -> None:
        raw = _sample_response(
            hours=[
                "2026-08-02T06:00", "2026-08-02T07:00", "2026-08-02T08:00",
                "2026-08-02T09:00", "2026-08-02T10:00",
            ],
            temps=[15.0, 18.0, 22.0, 25.0, 27.0],
            humidity=[70.0, 60.0, 50.0, 40.0, 35.0],
            codes=[0, 0, 1, 2, 3],
        )
        start = dt.datetime(2026, 8, 2, 7, 30, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 9, 15, tzinfo=dt.UTC)

        summary = parse_open_meteo_response(raw, start, end)

        assert summary is not None
        # Hours 07:00-10:00 all overlap [07:30, 09:15]'s [hour, hour+1h) buckets.
        assert summary.temperature_min_c == 18.0
        assert summary.temperature_max_c == 25.0
        assert summary.humidity_min_pct == 40.0
        assert summary.humidity_max_pct == 60.0
        # 07:30 is equidistant from 07:00 and 08:00 (30min each) -- `min`'s first-wins tie
        # behavior picks 07:00 (index 1), whose code is 0.
        assert summary.weather_code == 0

    def test_a_short_activity_still_gets_the_one_bucket_it_falls_inside(self) -> None:
        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[20.0], humidity=[55.0], codes=[2],
        )
        start = dt.datetime(2026, 8, 2, 8, 10, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 8, 25, tzinfo=dt.UTC)

        summary = parse_open_meteo_response(raw, start, end)

        assert summary == WeatherSummary(
            temperature_min_c=20.0, temperature_max_c=20.0,
            humidity_min_pct=55.0, humidity_max_pct=55.0, weather_code=2,
        )

    def test_none_when_hourly_block_is_missing(self) -> None:
        now = dt.datetime.now(dt.UTC)
        assert parse_open_meteo_response({}, now, now) is None

    def test_none_when_no_hour_overlaps_the_activity_window(self) -> None:
        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[20.0], humidity=[55.0], codes=[2],
        )
        start = dt.datetime(2026, 8, 3, 8, 0, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 3, 9, 0, tzinfo=dt.UTC)

        assert parse_open_meteo_response(raw, start, end) is None

    def test_extracts_feels_like_and_wind_at_the_representative_hour(self) -> None:
        raw = _sample_response(
            hours=["2026-08-02T08:00"],
            temps=[20.0], humidity=[55.0], codes=[2],
            feels_like=[18.5], wind_speed=[5.2], wind_direction=[270.0],
        )
        start = dt.datetime(2026, 8, 2, 8, 10, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 8, 25, tzinfo=dt.UTC)

        summary = parse_open_meteo_response(raw, start, end)

        assert summary is not None
        assert summary.feels_like_c == 18.5
        assert summary.wind_speed_mps == 5.2
        assert summary.wind_direction_deg == 270.0

    def test_feels_like_and_wind_are_none_when_the_response_lacks_those_arrays(self) -> None:
        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[20.0], humidity=[55.0], codes=[2],
        )
        start = dt.datetime(2026, 8, 2, 8, 10, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 8, 25, tzinfo=dt.UTC)

        summary = parse_open_meteo_response(raw, start, end)

        assert summary is not None
        assert summary.feels_like_c is None
        assert summary.wind_speed_mps is None
        assert summary.wind_direction_deg is None

    def test_computes_dew_point_solar_cloud_and_apparent_temperature_over_the_window(
        self,
    ) -> None:
        raw = _sample_response(
            hours=[
                "2026-08-02T07:00", "2026-08-02T08:00", "2026-08-02T09:00",
            ],
            temps=[18.0, 22.0, 25.0],
            humidity=[60.0, 50.0, 40.0],
            codes=[0, 1, 2],
            feels_like=[17.0, 21.0, 27.0],
            dew_point=[12.0, 14.0, 13.0],
            solar=[0.0, 400.0, 820.0],
            cloud=[80.0, 40.0, 10.0],
        )
        start = dt.datetime(2026, 8, 2, 7, 0, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 9, 30, tzinfo=dt.UTC)

        summary = parse_open_meteo_response(raw, start, end)

        assert summary is not None
        assert summary.dew_point_min_c == 12.0
        assert summary.dew_point_max_c == 14.0
        assert summary.solar_radiation_max_wm2 == 820.0
        assert summary.solar_radiation_mean_wm2 == (0.0 + 400.0 + 820.0) / 3
        assert summary.cloud_cover_min_pct == 10.0
        assert summary.cloud_cover_max_pct == 80.0
        # apparent_temperature min/max is a full-window range, independent of feels_like_c's own
        # single representative-hour value (which stays whatever the closest hour to `start` is).
        assert summary.apparent_temperature_min_c == 17.0
        assert summary.apparent_temperature_max_c == 27.0
        assert summary.feels_like_c == 17.0

    def test_precipitation_is_a_window_sum_not_a_range(self) -> None:
        raw = _sample_response(
            hours=["2026-08-02T07:00", "2026-08-02T08:00", "2026-08-02T09:00"],
            temps=[18.0, 22.0, 25.0],
            humidity=[60.0, 50.0, 40.0],
            codes=[0, 1, 2],
            precipitation=[0.2, 1.5, 0.0],
        )
        start = dt.datetime(2026, 8, 2, 7, 0, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 9, 30, tzinfo=dt.UTC)

        summary = parse_open_meteo_response(raw, start, end)

        assert summary is not None
        assert summary.precipitation_mm == 0.2 + 1.5 + 0.0

    def test_precipitation_is_a_real_zero_when_every_hour_reads_no_rain(self) -> None:
        # 0.0 is a real, meaningful reading (no rain fell) -- distinct from None (no data at
        # all), which the next test below covers.
        raw = _sample_response(
            hours=["2026-08-02T07:00"], temps=[18.0], humidity=[60.0], codes=[0],
            precipitation=[0.0],
        )
        start = dt.datetime(2026, 8, 2, 7, 0, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 7, 30, tzinfo=dt.UTC)

        summary = parse_open_meteo_response(raw, start, end)

        assert summary is not None
        assert summary.precipitation_mm == 0.0

    def test_precipitation_is_none_when_every_overlapping_hour_reads_null(self) -> None:
        raw = _sample_response(
            hours=["2026-08-02T07:00", "2026-08-02T08:00"],
            temps=[18.0, 22.0], humidity=[60.0, 50.0], codes=[0, 1],
            precipitation=[None, None],
        )
        start = dt.datetime(2026, 8, 2, 7, 0, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 8, 30, tzinfo=dt.UTC)

        summary = parse_open_meteo_response(raw, start, end)

        assert summary is not None
        assert summary.precipitation_mm is None

    def test_new_fields_are_none_when_the_response_lacks_those_arrays_entirely(self) -> None:
        # Simulates an old archived response, fetched before dew_point_2m/shortwave_radiation/
        # cloud_cover were ever requested -- exactly what an already-cached activity has on disk.
        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[20.0], humidity=[55.0], codes=[2],
        )
        start = dt.datetime(2026, 8, 2, 8, 10, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 8, 25, tzinfo=dt.UTC)

        summary = parse_open_meteo_response(raw, start, end)

        assert summary is not None
        assert summary.dew_point_min_c is None
        assert summary.dew_point_max_c is None
        assert summary.solar_radiation_max_wm2 is None
        assert summary.solar_radiation_mean_wm2 is None
        assert summary.cloud_cover_min_pct is None
        assert summary.cloud_cover_max_pct is None
        assert summary.apparent_temperature_min_c is None
        assert summary.apparent_temperature_max_c is None
        assert summary.sunrise_utc is None
        assert summary.sunset_utc is None
        assert summary.precipitation_mm is None

    def test_extracts_sunrise_and_sunset_for_the_activitys_own_start_date(self) -> None:
        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[20.0], humidity=[55.0], codes=[2],
            daily_dates=["2026-08-01", "2026-08-02", "2026-08-03"],
            sunrise=["2026-08-01T06:10", "2026-08-02T06:11", "2026-08-03T06:12"],
            sunset=["2026-08-01T20:05", "2026-08-02T20:04", "2026-08-03T20:03"],
        )
        start = dt.datetime(2026, 8, 2, 8, 10, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 8, 25, tzinfo=dt.UTC)

        summary = parse_open_meteo_response(raw, start, end)

        assert summary is not None
        assert summary.sunrise_utc == dt.datetime(2026, 8, 2, 6, 11)
        assert summary.sunset_utc == dt.datetime(2026, 8, 2, 20, 4)

    def test_sunrise_and_sunset_are_none_when_the_daily_block_is_missing(self) -> None:
        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[20.0], humidity=[55.0], codes=[2],
        )
        start = dt.datetime(2026, 8, 2, 8, 10, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 8, 25, tzinfo=dt.UTC)

        summary = parse_open_meteo_response(raw, start, end)

        assert summary is not None
        assert summary.sunrise_utc is None
        assert summary.sunset_utc is None


class TestParseOpenMeteoHourlySeries:
    def test_returns_one_point_per_overlapping_hour_in_order(self) -> None:
        raw = _sample_response(
            hours=["2026-08-02T07:00", "2026-08-02T08:00", "2026-08-02T09:00"],
            temps=[18.0, 22.0, 25.0],
            humidity=[60.0, 50.0, 40.0],
            codes=[0, 1, 2],
            feels_like=[17.0, 21.0, 27.0],
            wind_speed=[3.0, 4.0, 5.0],
            wind_direction=[90.0, 100.0, 110.0],
            dew_point=[12.0, 14.0, 13.0],
            solar=[0.0, 400.0, 820.0],
            cloud=[80.0, 40.0, 10.0],
            precipitation=[0.0, 1.2, 0.0],
        )
        start = dt.datetime(2026, 8, 2, 7, 30, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 9, 15, tzinfo=dt.UTC)

        points = parse_open_meteo_hourly_series(raw, start, end)

        assert [p.time_utc for p in points] == [
            dt.datetime(2026, 8, 2, 7, 0),
            dt.datetime(2026, 8, 2, 8, 0),
            dt.datetime(2026, 8, 2, 9, 0),
        ]
        assert points[1] == HourlyWeatherPoint(
            time_utc=dt.datetime(2026, 8, 2, 8, 0),
            temperature_c=22.0,
            apparent_temperature_c=21.0,
            dew_point_c=14.0,
            relative_humidity_pct=50.0,
            shortwave_radiation_wm2=400.0,
            cloud_cover_pct=40.0,
            wind_speed_mps=4.0,
            wind_direction_deg=100.0,
            precipitation_mm=1.2,
        )

    def test_new_fields_are_none_on_every_point_for_an_old_archived_response(self) -> None:
        # No dew_point_2m/shortwave_radiation/cloud_cover/precipitation arrays at all -- the
        # response genuinely doesn't have this data, so re-parsing it can't manufacture it. Not a
        # crash, not a missing point -- the point still exists with those fields None.
        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[20.0], humidity=[55.0], codes=[2],
            feels_like=[19.0], wind_speed=[2.0], wind_direction=[45.0],
        )
        start = dt.datetime(2026, 8, 2, 8, 10, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 8, 25, tzinfo=dt.UTC)

        points = parse_open_meteo_hourly_series(raw, start, end)

        assert len(points) == 1
        assert points[0].dew_point_c is None
        assert points[0].shortwave_radiation_wm2 is None
        assert points[0].precipitation_mm is None
        assert points[0].cloud_cover_pct is None
        # Fields the old response DOES carry are still populated.
        assert points[0].temperature_c == 20.0
        assert points[0].apparent_temperature_c == 19.0

    def test_a_null_reading_at_one_specific_hour_is_none_without_affecting_other_hours(
        self,
    ) -> None:
        raw = _sample_response(
            hours=["2026-08-02T08:00", "2026-08-02T09:00"],
            temps=[20.0, 21.0],
            humidity=[55.0, 50.0],
            codes=[2, 2],
            dew_point=[12.0, None],
            solar=[None, 300.0],
            cloud=[50.0, None],
            precipitation=[0.4, None],
        )
        start = dt.datetime(2026, 8, 2, 8, 0, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 2, 9, 30, tzinfo=dt.UTC)

        points = parse_open_meteo_hourly_series(raw, start, end)

        assert len(points) == 2
        assert points[0].dew_point_c == 12.0
        assert points[0].shortwave_radiation_wm2 is None
        assert points[0].precipitation_mm == 0.4
        assert points[1].dew_point_c is None
        assert points[1].shortwave_radiation_wm2 == 300.0
        assert points[1].cloud_cover_pct is None
        assert points[1].precipitation_mm is None

    def test_returns_empty_list_when_hourly_block_is_missing(self) -> None:
        now = dt.datetime.now(dt.UTC)
        assert parse_open_meteo_hourly_series({}, now, now) == []

    def test_returns_empty_list_when_no_hour_overlaps_the_window(self) -> None:
        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[20.0], humidity=[55.0], codes=[2],
        )
        start = dt.datetime(2026, 8, 3, 8, 0, tzinfo=dt.UTC)
        end = dt.datetime(2026, 8, 3, 9, 0, tzinfo=dt.UTC)

        assert parse_open_meteo_hourly_series(raw, start, end) == []


class TestGetOrFetchActivityWeather:
    def test_fetches_archives_and_stores_on_a_cache_miss(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        raw = _sample_response(
            hours=["2026-08-02T08:00", "2026-08-02T09:00"],
            temps=[25.0, 36.0],
            humidity=[30.0, 50.0],
            codes=[0, 1],
        )
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=raw)

        mock_client = httpx.Client(transport=httpx.MockTransport(handler))
        archive_root = tmp_path / "raw"

        with engine.connect() as conn:
            summary = get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=3600.0,
                lat=37.3622, lon=-121.9745, client=mock_client,
            )
            conn.commit()

        assert request_count == 1
        assert summary == WeatherSummary(
            temperature_min_c=25.0, temperature_max_c=36.0,
            humidity_min_pct=30.0, humidity_max_pct=50.0, weather_code=0,
        )

        with engine.connect() as conn:
            raw_rows = conn.execute(
                select(raw_object.c.storage_path, raw_object.c.kind, raw_object.c.source).where(
                    raw_object.c.source == "open-meteo"
                )
            ).fetchall()
            assert len(raw_rows) == 1
            assert raw_rows[0].kind == "historical-weather"
            blob = gzip.decompress((archive_root / raw_rows[0].storage_path).read_bytes())
            assert json.loads(blob) == raw

    def test_second_call_reads_the_cache_and_makes_no_network_request(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0],
        )
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=raw)

        archive_root = tmp_path / "raw"
        for _ in range(2):
            mock_client = httpx.Client(transport=httpx.MockTransport(handler))
            with engine.connect() as conn:
                summary = get_or_fetch_activity_weather(
                    conn, archive_root,
                    athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                    start_time_utc=start, duration_s=1800.0,
                    lat=37.3622, lon=-121.9745, client=mock_client,
                )
                conn.commit()

        assert request_count == 1  # only the first call ever hit the network
        assert summary is not None

    def test_returns_none_and_writes_nothing_on_a_network_failure(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        mock_client = httpx.Client(transport=httpx.MockTransport(handler))
        archive_root = tmp_path / "raw"

        with engine.connect() as conn:
            summary = get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745, client=mock_client,
            )
            conn.commit()

        assert summary is None
        with engine.connect() as conn:
            written = conn.execute(
                select(raw_object).where(raw_object.c.source == "open-meteo")
            ).fetchall()
            assert written == []

    def test_stores_all_five_weather_metric_keys(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0],
        )
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=raw))
        mock_client = httpx.Client(transport=transport)
        archive_root = tmp_path / "raw"

        with engine.connect() as conn:
            get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745, client=mock_client,
            )
            conn.commit()

        from perseverer.db.schema import activity_metric

        with engine.connect() as conn:
            rows = conn.execute(
                select(activity_metric.c.metric_key).where(
                    activity_metric.c.activity_id == "a1", activity_metric.c.source == "open-meteo"
                )
            ).fetchall()
        keys = {r.metric_key for r in rows}
        assert keys == {
            METRIC_TEMPERATURE_MIN_C, METRIC_TEMPERATURE_MAX_C,
            METRIC_HUMIDITY_MIN_PCT, METRIC_HUMIDITY_MAX_PCT, METRIC_WEATHER_CODE,
        }

    def test_stores_the_three_optional_keys_when_present(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0],
            feels_like=[24.0], wind_speed=[3.1], wind_direction=[180.0],
        )
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=raw))
        mock_client = httpx.Client(transport=transport)
        archive_root = tmp_path / "raw"

        with engine.connect() as conn:
            get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745, client=mock_client,
            )
            conn.commit()

        from perseverer.db.schema import activity_metric

        with engine.connect() as conn:
            rows = conn.execute(
                select(activity_metric.c.metric_key, activity_metric.c.value_num).where(
                    activity_metric.c.activity_id == "a1", activity_metric.c.source == "open-meteo"
                )
            ).fetchall()
        values = {r.metric_key: r.value_num for r in rows}
        assert values[METRIC_FEELS_LIKE_C] == 24.0
        assert values[METRIC_WIND_SPEED_MPS] == 3.1
        assert values[METRIC_WIND_DIRECTION_DEG] == 180.0

    def test_cache_hit_still_works_when_only_the_five_original_keys_are_stored(
        self, tmp_path: Path
    ) -> None:
        # Simulates an activity whose weather was cached before feels-like/wind (or any of the
        # even-newer dew-point/solar/cloud/apparent-temperature/sunrise/sunset/precipitation
        # fields) existed -- a second (non-forced) call must still cache-hit rather than
        # re-fetching forever.
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        raw = _sample_response(hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0])
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=raw)

        archive_root = tmp_path / "raw"
        for _ in range(2):
            mock_client = httpx.Client(transport=httpx.MockTransport(handler))
            with engine.connect() as conn:
                summary = get_or_fetch_activity_weather(
                    conn, archive_root,
                    athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                    start_time_utc=start, duration_s=1800.0,
                    lat=37.3622, lon=-121.9745, client=mock_client,
                )
                conn.commit()

        assert request_count == 1  # the second call cache-hit; no second Open-Meteo call at all.
        assert summary is not None
        assert summary.feels_like_c is None
        assert summary.wind_speed_mps is None
        assert summary.dew_point_min_c is None
        assert summary.dew_point_max_c is None
        assert summary.solar_radiation_max_wm2 is None
        assert summary.solar_radiation_mean_wm2 is None
        assert summary.cloud_cover_min_pct is None
        assert summary.cloud_cover_max_pct is None
        assert summary.apparent_temperature_min_c is None
        assert summary.apparent_temperature_max_c is None
        assert summary.sunrise_utc is None
        assert summary.sunset_utc is None
        assert summary.precipitation_mm is None

    def test_force_refresh_bypasses_the_cache_and_picks_up_new_fields(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        # First call: old-style response, no feels-like/wind (simulating an already-cached
        # activity from before those fields existed).
        old_raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0]
        )
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return httpx.Response(200, json=old_raw)
            new_raw = _sample_response(
                hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0],
                feels_like=[23.0], wind_speed=[4.4], wind_direction=[90.0],
            )
            return httpx.Response(200, json=new_raw)

        archive_root = tmp_path / "raw"
        with engine.connect() as conn:
            get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
            conn.commit()

        # Without force_refresh, this would cache-hit on the five original keys and never
        # discover that feels-like/wind are still missing.
        with engine.connect() as conn:
            unforced = get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
        assert request_count == 1
        assert unforced is not None and unforced.feels_like_c is None

        with engine.connect() as conn:
            forced = get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                force_refresh=True,
            )
            conn.commit()

        assert request_count == 2
        assert forced is not None
        assert forced.feels_like_c == 23.0
        assert forced.wind_speed_mps == 4.4
        assert forced.wind_direction_deg == 90.0

    def test_force_refresh_picks_up_dew_point_solar_cloud_and_sunrise_sunset(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        old_raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0]
        )
        new_raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0],
            feels_like=[23.0], dew_point=[15.0], solar=[600.0], cloud=[20.0],
            daily_dates=["2026-08-02"], sunrise=["2026-08-02T06:11"], sunset=["2026-08-02T20:04"],
        )
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=old_raw if request_count == 1 else new_raw)

        archive_root = tmp_path / "raw"
        with engine.connect() as conn:
            get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
            conn.commit()

        with engine.connect() as conn:
            forced = get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                force_refresh=True,
            )
            conn.commit()

        assert forced is not None
        assert forced.dew_point_min_c == 15.0
        assert forced.dew_point_max_c == 15.0
        assert forced.solar_radiation_max_wm2 == 600.0
        assert forced.cloud_cover_min_pct == 20.0
        assert forced.sunrise_utc == dt.datetime(2026, 8, 2, 6, 11)
        assert forced.sunset_utc == dt.datetime(2026, 8, 2, 20, 4)

        # Round-trips correctly through storage (value_text, not value_num) on a THIRD, plain
        # cache-read call -- proves _store/_read_cached's own text-field path, not just the
        # in-memory parse result the forced call above already returned.
        with engine.connect() as conn:
            cached = get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
        assert request_count == 2  # the third call cache-hit -- no third network request.
        assert cached is not None
        assert cached.sunrise_utc == dt.datetime(2026, 8, 2, 6, 11)
        assert cached.sunset_utc == dt.datetime(2026, 8, 2, 20, 4)
        assert cached.dew_point_min_c == 15.0

    def test_force_refresh_picks_up_precipitation(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        old_raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0]
        )
        new_raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0],
            precipitation=[2.4],
        )
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=old_raw if request_count == 1 else new_raw)

        archive_root = tmp_path / "raw"
        with engine.connect() as conn:
            get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
            conn.commit()

        with engine.connect() as conn:
            forced = get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                force_refresh=True,
            )
            conn.commit()

        assert forced is not None
        assert forced.precipitation_mm == 2.4

        # Round-trips through storage (value_num) on a plain cache-read call, same "not just the
        # in-memory result" proof the sibling dew-point/solar/cloud test above already applies.
        with engine.connect() as conn:
            cached = get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
        assert request_count == 2  # the third call cache-hit -- no third network request.
        assert cached is not None
        assert cached.precipitation_mm == 2.4


class TestReadArchivedOpenMeteoResponse:
    def test_returns_none_when_nothing_was_ever_archived(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            assert (
                read_archived_open_meteo_response(
                    conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1"
                )
                is None
            )

    def test_reads_back_the_exact_archived_response(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0],
            dew_point=[14.0],
        )
        archive_root = tmp_path / "raw"
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=raw))
        with engine.connect() as conn:
            get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=transport),
            )
            conn.commit()

        with engine.connect() as conn:
            read_back = read_archived_open_meteo_response(
                conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1"
            )
        assert read_back == raw

    def test_picks_the_newest_archived_response_after_a_force_refresh(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        old_raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0]
        )
        new_raw = _sample_response(
            hours=["2026-08-02T08:00"], temps=[25.0], humidity=[30.0], codes=[0],
            dew_point=[14.0],
        )
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=old_raw if request_count == 1 else new_raw)

        archive_root = tmp_path / "raw"
        with engine.connect() as conn:
            get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
            conn.commit()
        with engine.connect() as conn:
            get_or_fetch_activity_weather(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=start, duration_s=1800.0,
                lat=37.3622, lon=-121.9745,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                force_refresh=True,
            )
            conn.commit()

        with engine.connect() as conn:
            read_back = read_archived_open_meteo_response(
                conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1"
            )
        assert read_back == new_raw
