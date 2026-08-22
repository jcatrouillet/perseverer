"""Tests for weather.py: the pure Open-Meteo response parser, and the read-through-cache
fetch/archive/store orchestration (mocked HTTP, no real network calls). See CLAUDE.md's
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
    WeatherSummary,
    get_or_fetch_activity_weather,
    parse_open_meteo_response,
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
    return {"latitude": 37.36, "longitude": -121.97, "hourly": hourly}


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
        # Simulates an activity whose weather was cached before feels-like/wind existed --
        # a second (non-forced) call must still cache-hit rather than re-fetching forever.
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

        assert request_count == 1
        assert summary is not None
        assert summary.feels_like_c is None
        assert summary.wind_speed_mps is None

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
