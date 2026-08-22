"""Tests for weather_titles.py: the emoji-title backfill. HTTP is mocked (no real Open-Meteo
calls); see tests/test_weather.py for exhaustive coverage of the fetch/archive/store pipeline
itself, reused here via get_or_fetch_activity_weather.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Connection, Engine, select

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, athlete, metadata, route_geom
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.weather import get_or_fetch_activity_weather
from perseverer.weather_titles import _starts_with_emoji, backfill_weather_titles

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
    name: str | None = "Morning Run",
    sport: str = "running",
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
            sport=sport,
            name=name,
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


def _weather_response(code: int) -> dict[str, object]:
    return {
        "hourly": {
            "time": ["2026-08-02T08:00"],
            "temperature_2m": [20.0],
            "relative_humidity_2m": [50.0],
            "weathercode": [code],
            "apparent_temperature": [19.0],
            "wind_speed_10m": [3.0],
            "wind_direction_10m": [180.0],
        }
    }


class TestStartsWithEmoji:
    def test_true_for_a_leading_emoji(self) -> None:
        assert _starts_with_emoji("☀️ Morning Run")
        assert _starts_with_emoji("⛈️ Storm Chase")

    def test_false_for_plain_text(self) -> None:
        assert not _starts_with_emoji("Morning Run")
        assert not _starts_with_emoji("")

    def test_ignores_leading_whitespace(self) -> None:
        assert _starts_with_emoji("  ☀️ Morning Run")


class TestBackfillWeatherTitles:
    def test_prepends_the_condition_emoji_to_the_title(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_activity(conn, activity_id="a1", name="Morning Run")

        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_weather_response(0)))
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: _RealClient(transport=transport))

        with engine.connect() as conn:
            changes = backfill_weather_titles(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)

        assert changes == [("a1", "Morning Run", "☀️ Morning Run")]
        with engine.connect() as conn:
            row = conn.execute(select(activity.c.name).where(activity.c.id == "a1")).one()
            assert row.name == "☀️ Morning Run"

    def test_skips_an_activity_whose_title_already_has_an_emoji(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_activity(conn, activity_id="a1", name="🏔️ Half Dome")

        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_weather_response(0))

        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: _RealClient(transport=httpx.MockTransport(handler))
        )

        with engine.connect() as conn:
            changes = backfill_weather_titles(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)

        assert changes == []
        assert request_count == 0  # never even fetched weather for it

    def test_skips_an_activity_with_no_gps_start_point(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_activity(conn, activity_id="a1", with_route=False)

        with engine.connect() as conn:
            changes = backfill_weather_titles(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)

        assert changes == []

    def test_falls_back_to_the_sport_when_the_activity_has_no_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_activity(conn, activity_id="a1", name=None, sport="hiking")

        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_weather_response(3)))
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: _RealClient(transport=transport))

        with engine.connect() as conn:
            changes = backfill_weather_titles(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)

        assert changes == [("a1", "hiking", "☁️ hiking")]

    def test_dry_run_reports_changes_without_writing_anything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_activity(conn, activity_id="a1", name="Morning Run")

        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_weather_response(0)))
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: _RealClient(transport=transport))

        with engine.connect() as conn:
            changes = backfill_weather_titles(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID, dry_run=True
            )

        assert changes == [("a1", "Morning Run", "☀️ Morning Run")]
        with engine.connect() as conn:
            row = conn.execute(select(activity.c.name).where(activity.c.id == "a1")).one()
            assert row.name == "Morning Run"  # untouched

    def test_a_second_run_is_a_no_op(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_activity(conn, activity_id="a1", name="Morning Run")

        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_weather_response(0))

        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: _RealClient(transport=httpx.MockTransport(handler))
        )

        with engine.connect() as conn:
            first = backfill_weather_titles(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)
        with engine.connect() as conn:
            second = backfill_weather_titles(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)

        assert len(first) == 1
        assert second == []
        assert request_count == 1  # the second run never called Open-Meteo again

    def test_reuses_weather_already_cached_by_something_else(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Automatic wiring into every ingest run (see this module's own docstring) means an
        activity's weather may already be cached -- e.g. fetched live via GET .../weather --
        before the emoji backfill ever gets to it. It must read that cache, not force a second,
        wasted Open-Meteo call for data it already has."""
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_activity(conn, activity_id="a1", name="Morning Run")

        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_weather_response(0))

        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: _RealClient(transport=httpx.MockTransport(handler))
        )

        with engine.connect() as conn:
            summary = get_or_fetch_activity_weather(
                conn,
                tmp_path / "raw",
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                start_time_utc=dt.datetime(2026, 8, 2, 8, 0, tzinfo=dt.UTC),
                duration_s=1800.0,
                lat=37.36,
                lon=-121.97,
            )
            conn.commit()
        assert summary is not None
        assert request_count == 1  # the pre-population itself made the one real call

        with engine.connect() as conn:
            changes = backfill_weather_titles(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)

        assert changes == [("a1", "Morning Run", "☀️ Morning Run")]
        assert request_count == 1  # backfill reused the cache -- no second Open-Meteo call
