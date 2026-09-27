"""duration_goals.py -- the time-progress math (the router is covered by
tests/api/test_duration_goals.py)."""

from __future__ import annotations

import datetime as dt
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Engine

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, athlete, metadata
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.duration_goals import compute_progress

NOW = dt.datetime(2026, 1, 1, 10, 0, 0)


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


def _act(
    engine: Engine,
    activity_id: str,
    local_date: str,
    sport: str,
    *,
    duration_s: float,
    moving_s: float | None,
) -> None:
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=NOW,
                utc_offset_s=0,
                local_date=local_date,
                sport=sport,
                duration_s=duration_s,
                moving_duration_s=moving_s,
                primary_source="fit_folder",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        conn.commit()


def _progress(engine: Engine, *, as_of: date, **kwargs: object):  # type: ignore[no-untyped-def]
    params: dict[str, object] = {
        "period_type": "week",
        "period_start": "2026-09-28",
        "sport": None,
        "target_duration_s": 7 * 3600.0,
    }
    params.update(kwargs)
    with engine.connect() as conn:
        return compute_progress(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=as_of, **params)  # type: ignore[arg-type]


class TestComputeProgress:
    def test_sums_time_across_sports_or_for_one_sport(self, engine: Engine) -> None:
        _act(engine, "a", "2026-09-28", "yoga", duration_s=3600, moving_s=3600)
        _act(engine, "b", "2026-09-29", "running", duration_s=1800, moving_s=1700)
        _act(engine, "c", "2026-09-29", "yoga", duration_s=1200, moving_s=1200)
        everything = _progress(engine, as_of=date(2026, 10, 4))
        assert everything.current_duration_s == 3600 + 1700 + 1200
        yoga = _progress(engine, as_of=date(2026, 10, 4), sport="yoga")
        assert yoga.current_duration_s == 4800

    def test_uses_moving_time_falling_back_to_elapsed_when_it_is_missing(
        self, engine: Engine
    ) -> None:
        _act(engine, "a", "2026-09-28", "yoga", duration_s=3600, moving_s=3000)
        _act(engine, "b", "2026-09-29", "yoga", duration_s=1800, moving_s=None)
        assert _progress(engine, as_of=date(2026, 10, 4)).current_duration_s == 3000 + 1800

    def test_only_the_period_up_to_today_counts(self, engine: Engine) -> None:
        _act(engine, "before", "2026-09-27", "yoga", duration_s=999, moving_s=999)
        _act(engine, "inside", "2026-09-30", "yoga", duration_s=600, moving_s=600)
        _act(engine, "future", "2026-10-03", "yoga", duration_s=777, moving_s=777)
        p = _progress(engine, as_of=date(2026, 10, 1))
        assert p.current_duration_s == 600
        assert len(p.daily) == 4  # Sep 28 .. Oct 1
        assert p.period_end == "2026-10-04"

    def test_straight_line_pace_and_ahead_behind(self, engine: Engine) -> None:
        # 7h over a 7-day week = 1h/day; through day 3 dead-even pace is 3h.
        _act(engine, "a", "2026-09-28", "yoga", duration_s=5 * 3600, moving_s=5 * 3600)
        p = _progress(engine, as_of=date(2026, 9, 30))
        assert p.target_per_day_s == 3600
        assert p.target_as_of_today_s == 3 * 3600
        assert p.ahead_behind_s == 2 * 3600
        assert p.pct_complete == pytest.approx(5 / 7)

    def test_deleted_activities_are_ignored(self, engine: Engine) -> None:
        _act(engine, "a", "2026-09-28", "yoga", duration_s=3600, moving_s=3600)
        with engine.connect() as conn:
            conn.execute(activity.update().values(deleted_at=NOW))
            conn.commit()
        assert _progress(engine, as_of=date(2026, 10, 4)).current_duration_s == 0

    def test_a_future_period_has_no_data_and_no_pace_yet(self, engine: Engine) -> None:
        p = _progress(engine, as_of=date(2026, 9, 1))
        assert p.daily == []
        assert p.target_as_of_today_s == 0
        assert p.ahead_behind_s == 0
