"""The remembered forecast (weather_forecast_cache.py): kept per athlete and kind for one place,
merged across fetches, never served for another place, for days already past, or when too old."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection

from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, metadata
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.weather_forecast_cache import (
    KIND_HOME,
    KIND_LAST_ACTIVITY,
    MAX_STALE_HOURS,
    load_forecast,
    store_forecast,
)

NOW = dt.datetime(2026, 11, 24, 12, 0)
LAT, LON = 37.3623, -121.9744


def _day(d: str, code: int = 3) -> dict[str, object]:
    return {
        "local_date": d,
        "weather_code": code,
        "temperature_min_c": 5.0,
        "temperature_max_c": 15.0,
    }


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Connection]:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    with engine.connect() as c:
        c.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=NOW,
            )
        )
        c.commit()
        yield c


def _store(conn: Connection, days: list[str]) -> None:
    store_forecast(
        conn,
        DEFAULT_ATHLETE_ID,
        KIND_HOME,
        LAT,
        LON,
        days=[_day(d) for d in days],
        upcoming=[],
        today="2026-11-24",
        now=NOW,
    )


def _load(
    conn: Connection,
    *,
    kind: str = KIND_HOME,
    lat: float = LAT,
    now: dt.datetime = NOW,
    today: str = "2026-11-24",
) -> Any:
    return load_forecast(conn, DEFAULT_ATHLETE_ID, kind, lat, LON, today=today, now=now)


def test_a_stored_forecast_comes_back_with_its_fetch_time(conn: Connection) -> None:
    _store(conn, ["2026-11-24", "2026-11-25"])
    loaded = _load(conn, now=NOW + dt.timedelta(hours=5))
    assert loaded is not None
    days, upcoming, fetched_at = loaded
    assert [d["local_date"] for d in days] == ["2026-11-24", "2026-11-25"]
    assert upcoming == []
    assert fetched_at == NOW


def test_a_shorter_later_fetch_does_not_shrink_what_is_kept(conn: Connection) -> None:
    _store(conn, ["2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"])
    store_forecast(
        conn,
        DEFAULT_ATHLETE_ID,
        KIND_HOME,
        LAT,
        LON,
        days=[_day("2026-11-25", code=61)],
        upcoming=[],
        today="2026-11-24",
        now=NOW + dt.timedelta(hours=1),
    )
    loaded = _load(conn)
    assert loaded is not None
    days = {d["local_date"]: d["weather_code"] for d in loaded[0]}
    assert days == {"2026-11-24": 3, "2026-11-25": 61, "2026-11-26": 3, "2026-11-27": 3}


def test_days_already_past_are_not_served(conn: Connection) -> None:
    _store(conn, ["2026-11-24", "2026-11-25"])
    loaded = _load(conn, today="2026-11-25")
    assert loaded is not None
    assert [d["local_date"] for d in loaded[0]] == ["2026-11-25"]
    assert _load(conn, today="2026-11-26") is None


def test_another_place_another_kind_or_too_old_gives_nothing(conn: Connection) -> None:
    _store(conn, ["2026-11-24", "2026-11-25"])
    assert _load(conn, lat=LAT + 1) is None
    assert _load(conn, kind=KIND_LAST_ACTIVITY) is None
    assert _load(conn, now=NOW + dt.timedelta(hours=MAX_STALE_HOURS, minutes=1)) is None
    assert _load(conn, now=NOW + dt.timedelta(hours=MAX_STALE_HOURS)) is not None
