"""Tests for planned_races.py::predicted_duration_s_for_distance -- the target-vs-predicted
finish-time lookup for a scheduled race. The route/CRUD layer is covered separately in
tests/api/test_planned_races.py.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine

from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, metadata, performance_daily_rollup
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.planned_races import predicted_duration_s_for_distance


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    eng = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(eng)
    with eng.connect() as conn:
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
    return eng


@pytest.fixture
def conn(engine: Engine):  # type: ignore[no-untyped-def]
    with engine.connect() as c:
        yield c


def _seed_rollup(conn: Connection, local_date: str, **predictions: float) -> None:
    conn.execute(
        performance_daily_rollup.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            local_date=local_date,
            refreshed_at=dt.datetime(2026, 1, 1),
            **predictions,
        )
    )
    conn.commit()


def test_returns_none_for_a_custom_distance(conn: Connection) -> None:
    _seed_rollup(conn, "2026-09-01", predicted_5k_s=1100.0)
    assert (
        predicted_duration_s_for_distance(conn, athlete_id=DEFAULT_ATHLETE_ID, distance_m=15000.0)
        is None
    )


def test_returns_none_when_no_rollup_exists_yet(conn: Connection) -> None:
    assert (
        predicted_duration_s_for_distance(conn, athlete_id=DEFAULT_ATHLETE_ID, distance_m=5000.0)
        is None
    )


def test_matches_a_standard_distance_to_its_own_predicted_column(conn: Connection) -> None:
    _seed_rollup(
        conn,
        "2026-09-01",
        predicted_5k_s=1100.0,
        predicted_marathon_s=13500.0,
    )
    assert predicted_duration_s_for_distance(
        conn, athlete_id=DEFAULT_ATHLETE_ID, distance_m=5000.0
    ) == 1100.0
    assert predicted_duration_s_for_distance(
        conn, athlete_id=DEFAULT_ATHLETE_ID, distance_m=42195.0
    ) == 13500.0


def test_uses_the_most_recent_rollup_row(conn: Connection) -> None:
    _seed_rollup(conn, "2026-08-01", predicted_10k_s=2400.0)
    _seed_rollup(conn, "2026-09-01", predicted_10k_s=2300.0)
    assert (
        predicted_duration_s_for_distance(conn, athlete_id=DEFAULT_ATHLETE_ID, distance_m=10000.0)
        == 2300.0
    )


def test_skips_a_more_recent_row_missing_that_distance(conn: Connection) -> None:
    # The latest row has no half-marathon prediction yet (e.g. rolling VDOT dropped out of the
    # search bounds that day) -- fall back to the most recent row that actually has one, rather
    # than reporting "no prediction" while an older, still-relevant one exists.
    _seed_rollup(conn, "2026-08-01", predicted_half_marathon_s=5800.0)
    _seed_rollup(conn, "2026-09-01", predicted_5k_s=1100.0)
    assert (
        predicted_duration_s_for_distance(
            conn, athlete_id=DEFAULT_ATHLETE_ID, distance_m=21097.5
        )
        == 5800.0
    )
