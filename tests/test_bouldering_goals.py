"""bouldering_goals.py -- period bounds, grade matching, and the progress math itself (the API
router's own behaviour is covered by tests/api/test_bouldering_goals.py)."""

from __future__ import annotations

import datetime as dt
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Engine

from perseverer.bouldering_goals import compute_progress, grade_matches, period_bounds
from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, athlete, metadata, split
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.goals import InvalidPeriod

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


def _session(
    engine: Engine, activity_id: str, local_date: str, routes: list[tuple[int, str]]
) -> None:
    """A bouldering session with one (grade, result) split per route."""
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=NOW,
                utc_offset_s=0,
                local_date=local_date,
                sport="rock_climbing",
                sub_sport="bouldering",
                duration_s=3600.0,
                primary_source="fit_folder",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        for i, (grade, result) in enumerate(routes):
            conn.execute(
                split.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id=activity_id,
                    split_index=i,
                    split_type="climb_active",
                    climb_grade=grade,
                    climb_result=result,
                    duration_s=60.0,
                )
            )
        conn.commit()


def _progress(engine: Engine, *, as_of: date, **kwargs: object):  # type: ignore[no-untyped-def]
    params: dict[str, object] = {
        "period_type": "year",
        "period_start": "2026",
        "grade": None,
        "and_harder": False,
        "target_count": 10,
    }
    params.update(kwargs)
    with engine.connect() as conn:
        return compute_progress(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=as_of, **params)  # type: ignore[arg-type]


class TestPeriodBounds:
    def test_year_month_and_week(self) -> None:
        assert period_bounds("year", "2026") == (date(2026, 1, 1), date(2026, 12, 31))
        assert period_bounds("month", "2026-10") == (date(2026, 10, 1), date(2026, 10, 31))
        # A week is 7 days from whatever date it starts on -- any weekday.
        assert period_bounds("week", "2026-09-27") == (date(2026, 9, 27), date(2026, 10, 3))

    @pytest.mark.parametrize(
        ("period_type", "period_start"),
        [("week", "2026-13-40"), ("week", "2026"), ("month", "2026-13"), ("day", "2026-01-01")],
    )
    def test_rejects_a_bad_period(self, period_type: str, period_start: str) -> None:
        with pytest.raises(InvalidPeriod):
            period_bounds(period_type, period_start)


class TestGradeMatches:
    def test_exact_and_or_harder_and_any(self) -> None:
        assert grade_matches(4, 4, False) is True
        assert grade_matches(5, 4, False) is False
        assert grade_matches(5, 4, True) is True
        assert grade_matches(3, 4, True) is False
        assert grade_matches(0, None, False) is True


class TestComputeProgress:
    def test_counts_only_completed_routes_of_the_goal_grade(self, engine: Engine) -> None:
        _session(engine, "s1", "2026-03-01", [(4, "completed"), (4, "attempt"), (3, "completed")])
        _session(engine, "s2", "2026-03-08", [(4, "completed"), (4, "completed")])
        p = _progress(engine, as_of=date(2026, 6, 30), grade=4)
        assert p.current_count == 3
        assert p.daily[-1].cumulative_count == 3

    def test_or_harder_and_any_grade(self, engine: Engine) -> None:
        _session(engine, "s1", "2026-03-01", [(4, "completed"), (5, "completed"), (3, "completed")])
        assert (
            _progress(engine, as_of=date(2026, 6, 30), grade=4, and_harder=True).current_count == 2
        )
        assert _progress(engine, as_of=date(2026, 6, 30)).current_count == 3

    def test_an_unconfirmed_route_is_never_counted(self, engine: Engine) -> None:
        _session(engine, "s1", "2026-03-01", [(4, "unknown_3"), (4, "completed")])
        assert _progress(engine, as_of=date(2026, 6, 30), grade=4).current_count == 1

    def test_only_sessions_inside_the_period_and_up_to_today_count(self, engine: Engine) -> None:
        _session(engine, "before", "2025-12-31", [(4, "completed")])
        _session(engine, "inside", "2026-02-01", [(4, "completed")])
        _session(engine, "future", "2026-09-01", [(4, "completed")])
        p = _progress(engine, as_of=date(2026, 6, 30), grade=4)
        assert p.current_count == 1

    def test_cumulative_line_is_one_entry_per_day_gap_filled(self, engine: Engine) -> None:
        _session(engine, "s1", "2026-10-02", [(5, "completed")])
        _session(engine, "s2", "2026-10-05", [(5, "completed"), (5, "completed")])
        p = _progress(
            engine,
            as_of=date(2026, 10, 6),
            period_type="month",
            period_start="2026-10",
            grade=5,
            target_count=1,
        )
        assert p.daily[0].local_date == "2026-10-01"
        assert len(p.daily) == 6
        assert [d.cumulative_count for d in p.daily] == [0, 1, 1, 1, 3, 3]
        assert p.pct_complete == 3.0  # a goal can be exceeded

    def test_ahead_behind_uses_a_straight_line_pace(self, engine: Engine) -> None:
        # 10 routes over a 365-day year: on day 73 (Mar 14) dead-even pace is 2.0 routes.
        _session(engine, "s1", "2026-03-01", [(4, "completed")] * 3)
        p = _progress(engine, as_of=date(2026, 3, 14), grade=4)
        assert p.target_as_of_today == pytest.approx(10 / 365 * 73)
        assert p.ahead_behind == pytest.approx(3 - 10 / 365 * 73)

    def test_a_future_period_has_no_data_and_no_pace_yet(self, engine: Engine) -> None:
        p = _progress(engine, as_of=date(2026, 1, 1), period_start="2027")
        assert p.daily == []
        assert p.current_count == 0
        assert p.target_as_of_today == 0
        assert p.ahead_behind == 0

    def test_a_deleted_or_non_bouldering_activity_is_ignored(self, engine: Engine) -> None:
        _session(engine, "s1", "2026-03-01", [(4, "completed")])
        with engine.connect() as conn:
            conn.execute(activity.update().where(activity.c.id == "s1").values(deleted_at=NOW))
            conn.commit()
        assert _progress(engine, as_of=date(2026, 6, 30), grade=4).current_count == 0
        _session(engine, "s2", "2026-03-02", [(4, "completed")])
        with engine.connect() as conn:
            conn.execute(activity.update().where(activity.c.id == "s2").values(sub_sport=None))
            conn.commit()
        assert _progress(engine, as_of=date(2026, 6, 30), grade=4).current_count == 0
