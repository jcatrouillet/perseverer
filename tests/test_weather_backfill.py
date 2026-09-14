"""Tests for weather_backfill.py: the one-time re-fetch that lands dew_point_min_c/max_c,
solar_radiation_max_wm2/mean_wm2, cloud_cover_min_pct/max_pct, apparent_temperature_min_c/max_c,
and sunrise_utc/sunset_utc onto activities whose weather was cached before those fields were ever
requested from Open-Meteo. HTTP is mocked (no real Open-Meteo calls); see tests/test_weather.py
for exhaustive coverage of the underlying fetch/archive/store pipeline itself.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Connection, Engine, select

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, activity_metric, athlete, metadata, route_geom
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.weather import METRIC_DEW_POINT_MIN_C, get_or_fetch_activity_weather
from perseverer.weather_backfill import backfill_weather_fields

# Captured before any test monkeypatches httpx.Client, so the fake client factories below don't
# recursively call their own patched replacement.
_RealClient = httpx.Client


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


def _seed_activity(
    conn: Connection,
    *,
    activity_id: str,
    start: dt.datetime = dt.datetime(2026, 8, 2, 8, 0, tzinfo=dt.UTC),
    with_route: bool = True,
) -> None:
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start,
            utc_offset_s=0,
            local_date=start.date().isoformat(),
            sport="running",
            name="Morning Run",
            duration_s=1800.0,
            distance_m=5000.0,
            primary_source="fit_folder",
            created_at=start,
            updated_at=start,
        )
    )
    if with_route:
        conn.execute(
            route_geom.insert().values(
                activity_id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_lat=37.36,
                start_lng=-121.97,
            )
        )
    conn.commit()


def _old_response() -> dict[str, object]:
    # No dew_point_2m/shortwave_radiation/cloud_cover -- exactly what an activity cached before
    # this feature existed has archived on disk.
    return {
        "hourly": {
            "time": ["2026-08-02T08:00"],
            "temperature_2m": [20.0],
            "relative_humidity_2m": [50.0],
            "weathercode": [0],
        }
    }


def _new_response() -> dict[str, object]:
    return {
        "hourly": {
            "time": ["2026-08-02T08:00"],
            "temperature_2m": [20.0],
            "relative_humidity_2m": [50.0],
            "weathercode": [0],
            "dew_point_2m": [12.0],
            "shortwave_radiation": [300.0],
            "cloud_cover": [40.0],
        }
    }


def _seed_with_old_cache(engine: Engine, tmp_path: Path, activity_id: str = "a1") -> None:
    with engine.connect() as conn:
        _seed_activity(conn, activity_id=activity_id)

    old_transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_old_response()))
    with engine.connect() as conn:
        get_or_fetch_activity_weather(
            conn, tmp_path / "raw",
            athlete_id=DEFAULT_ATHLETE_ID, activity_id=activity_id,
            start_time_utc=dt.datetime(2026, 8, 2, 8, 0, tzinfo=dt.UTC), duration_s=1800.0,
            lat=37.36, lon=-121.97, client=_RealClient(transport=old_transport),
        )
        conn.commit()


class TestBackfillWeatherFields:
    def test_refetches_an_activity_cached_under_the_old_field_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine(tmp_path)
        _seed_with_old_cache(engine, tmp_path)

        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_new_response())

        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: _RealClient(transport=httpx.MockTransport(handler))
        )

        with engine.connect() as conn:
            refreshed = backfill_weather_fields(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert refreshed == ["a1"]
        assert request_count == 1
        with engine.connect() as conn:
            row = conn.execute(
                select(activity_metric.c.value_num).where(
                    activity_metric.c.activity_id == "a1",
                    activity_metric.c.metric_key == METRIC_DEW_POINT_MIN_C,
                )
            ).one()
            assert row.value_num == 12.0

    def test_second_run_makes_no_network_calls_once_backfilled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine(tmp_path)
        _seed_with_old_cache(engine, tmp_path)

        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_new_response())

        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: _RealClient(transport=httpx.MockTransport(handler))
        )

        with engine.connect() as conn:
            first = backfill_weather_fields(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)
        with engine.connect() as conn:
            second = backfill_weather_fields(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)

        assert first == ["a1"]
        assert second == []
        assert request_count == 1  # the second run never called Open-Meteo again

    def test_skips_an_activity_already_fetched_with_the_new_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_activity(conn, activity_id="a1")

        # Simulates an activity that was fetched live (GET .../weather) after this feature
        # shipped -- its archive already has dew_point_2m, so the backfill has nothing to do.
        new_transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_new_response()))
        with engine.connect() as conn:
            get_or_fetch_activity_weather(
                conn, tmp_path / "raw",
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                start_time_utc=dt.datetime(2026, 8, 2, 8, 0, tzinfo=dt.UTC), duration_s=1800.0,
                lat=37.36, lon=-121.97, client=_RealClient(transport=new_transport),
            )
            conn.commit()

        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_new_response())

        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: _RealClient(transport=httpx.MockTransport(handler))
        )

        with engine.connect() as conn:
            refreshed = backfill_weather_fields(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert refreshed == []
        assert request_count == 0

    def test_skips_an_activity_with_no_gps_start_point(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_activity(conn, activity_id="a1", with_route=False)

        with engine.connect() as conn:
            refreshed = backfill_weather_fields(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert refreshed == []

    def test_dry_run_reports_eligible_activities_without_writing_or_calling_open_meteo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine(tmp_path)
        _seed_with_old_cache(engine, tmp_path)

        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_new_response())

        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: _RealClient(transport=httpx.MockTransport(handler))
        )

        with engine.connect() as conn:
            refreshed = backfill_weather_fields(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID, dry_run=True
            )

        assert refreshed == ["a1"]
        assert request_count == 0  # dry run never actually calls Open-Meteo
        with engine.connect() as conn:
            row = conn.execute(
                select(activity_metric.c.value_num).where(
                    activity_metric.c.activity_id == "a1",
                    activity_metric.c.metric_key == METRIC_DEW_POINT_MIN_C,
                )
            ).one_or_none()
            assert row is None  # still the old, unbackfilled data

    def test_multiple_activities_only_the_unbackfilled_one_is_touched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine(tmp_path)
        _seed_with_old_cache(engine, tmp_path, activity_id="a1")
        with engine.connect() as conn:
            _seed_activity(
                conn, activity_id="a2", start=dt.datetime(2026, 8, 3, 8, 0, tzinfo=dt.UTC)
            )
        new_transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_new_response()))
        with engine.connect() as conn:
            get_or_fetch_activity_weather(
                conn, tmp_path / "raw",
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a2",
                start_time_utc=dt.datetime(2026, 8, 3, 8, 0, tzinfo=dt.UTC), duration_s=1800.0,
                lat=37.36, lon=-121.97, client=_RealClient(transport=new_transport),
            )
            conn.commit()

        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_new_response())

        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: _RealClient(transport=httpx.MockTransport(handler))
        )

        with engine.connect() as conn:
            refreshed = backfill_weather_fields(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert refreshed == ["a1"]
        assert request_count == 1
