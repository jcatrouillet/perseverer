"""Tests for goals.py: period_bounds' calendar-period parsing, and compute_progress's
cumulative-distance/target-line math against a real activity table."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import Engine

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, athlete, metadata
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.goals import InvalidPeriod, compute_progress, period_bounds


class TestPeriodBounds:
    def test_year(self) -> None:
        assert period_bounds("year", "2026") == (dt.date(2026, 1, 1), dt.date(2026, 12, 31))

    def test_month(self) -> None:
        assert period_bounds("month", "2026-02") == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))

    def test_leap_year_february(self) -> None:
        assert period_bounds("month", "2024-02") == (dt.date(2024, 2, 1), dt.date(2024, 2, 29))

    def test_week_is_seven_days_from_any_weekday(self) -> None:
        # A Sunday start and a Monday start are both valid -- the frontend's week-start
        # preference decides which day a week begins on.
        assert period_bounds("week", "2026-09-27") == (dt.date(2026, 9, 27), dt.date(2026, 10, 3))
        assert period_bounds("week", "2026-09-28") == (dt.date(2026, 9, 28), dt.date(2026, 10, 4))

    def test_malformed_week_start_raises(self) -> None:
        for bad in ("2026", "2026-13-40", "not-a-date"):
            with pytest.raises(InvalidPeriod):
                period_bounds("week", bad)

    def test_invalid_period_type_raises(self) -> None:
        with pytest.raises(InvalidPeriod):
            period_bounds("day", "2026-01-01")

    def test_non_numeric_year_raises(self) -> None:
        with pytest.raises(InvalidPeriod):
            period_bounds("year", "twenty-twenty-six")

    def test_month_out_of_range_raises(self) -> None:
        with pytest.raises(InvalidPeriod):
            period_bounds("month", "2026-13")

    def test_malformed_month_string_raises(self) -> None:
        with pytest.raises(InvalidPeriod):
            period_bounds("month", "2026/02")


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
    engine: Engine, *, activity_id: str, local_date: str, sport: str, distance_m: float
) -> None:
    now = dt.datetime.fromisoformat(f"{local_date}T10:00:00")
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date=local_date,
                sport=sport,
                duration_s=1800.0,
                distance_m=distance_m,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()


class TestComputeProgress:
    def test_week_goal_sums_the_seven_days_and_paces_per_day(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _seed_activity(
            engine, activity_id="in1", local_date="2026-09-28", sport="running", distance_m=10_000.0
        )
        _seed_activity(
            engine, activity_id="in2", local_date="2026-10-02", sport="running", distance_m=6_000.0
        )
        # Just outside the 7-day window on either side.
        _seed_activity(
            engine,
            activity_id="out1",
            local_date="2026-09-27",
            sport="running",
            distance_m=99_000.0,
        )
        _seed_activity(
            engine,
            activity_id="out2",
            local_date="2026-10-05",
            sport="running",
            distance_m=99_000.0,
        )
        with engine.connect() as conn:
            progress = compute_progress(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="week",
                period_start="2026-09-28",
                sport="running",
                target_distance_m=70_000.0,
                as_of=dt.date(2026, 10, 1),
            )
        assert progress.period_end == "2026-10-04"
        # Through Thursday Oct 1: only the Sep 28 run so far (the Oct 2 run is still in the future).
        assert progress.current_distance_m == 10_000.0
        assert len(progress.daily) == 4
        assert progress.target_per_day_m == 10_000.0
        assert progress.target_distance_as_of_today_m == 40_000.0
        assert progress.ahead_behind_m == -30_000.0

    def test_cumulative_daily_series_and_current_total(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _seed_activity(
            engine, activity_id="a1", local_date="2026-01-05", sport="running", distance_m=5000.0
        )
        _seed_activity(
            engine, activity_id="a2", local_date="2026-01-10", sport="running", distance_m=3000.0
        )
        # Two runs on the same day sum together.
        _seed_activity(
            engine, activity_id="a3", local_date="2026-01-10", sport="running", distance_m=2000.0
        )

        with engine.connect() as conn:
            progress = compute_progress(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="year",
                period_start="2026",
                sport="running",
                target_distance_m=100_000.0,
                as_of=dt.date(2026, 1, 15),
            )

        by_date = {p.local_date: p.cumulative_distance_m for p in progress.daily}
        assert by_date["2026-01-04"] == 0.0
        assert by_date["2026-01-05"] == 5000.0
        assert by_date["2026-01-09"] == 5000.0  # no activity that day -- carries forward
        assert by_date["2026-01-10"] == 10000.0  # both same-day runs summed in
        assert by_date["2026-01-15"] == 10000.0  # as_of caps the series here
        assert progress.current_distance_m == 10000.0
        assert len(progress.daily) == 15  # Jan 1 through Jan 15 inclusive

    def test_only_the_goals_sport_counts(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _seed_activity(
            engine, activity_id="a1", local_date="2026-01-05", sport="running", distance_m=5000.0
        )
        _seed_activity(
            engine, activity_id="a2", local_date="2026-01-05", sport="cycling", distance_m=20000.0
        )

        with engine.connect() as conn:
            progress = compute_progress(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="year",
                period_start="2026",
                sport="running",
                target_distance_m=100_000.0,
                as_of=dt.date(2026, 1, 5),
            )

        assert progress.current_distance_m == 5000.0

    def test_sport_none_combines_every_sport(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _seed_activity(
            engine, activity_id="a1", local_date="2026-01-05", sport="running", distance_m=5000.0
        )
        _seed_activity(
            engine, activity_id="a2", local_date="2026-01-05", sport="cycling", distance_m=20000.0
        )

        with engine.connect() as conn:
            progress = compute_progress(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="year",
                period_start="2026",
                sport=None,
                target_distance_m=100_000.0,
                as_of=dt.date(2026, 1, 5),
            )

        assert progress.current_distance_m == 25000.0

    def test_target_per_day_and_ahead_behind_on_pace(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        # A year goal of 3650 km over 365 days is exactly 10 km/day.
        with engine.connect() as conn:
            progress = compute_progress(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="year",
                period_start="2026",
                sport="running",
                target_distance_m=3_650_000.0,
                as_of=dt.date(2026, 1, 10),  # 10 days elapsed
            )

        assert progress.target_per_day_m == pytest.approx(10_000.0)
        assert progress.target_distance_as_of_today_m == pytest.approx(100_000.0)
        assert progress.current_distance_m == 0.0
        assert progress.ahead_behind_m == pytest.approx(-100_000.0)  # nothing logged yet

    def test_ahead_of_pace_when_current_exceeds_target_line(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _seed_activity(
            engine, activity_id="a1", local_date="2026-01-01", sport="running", distance_m=50_000.0
        )

        with engine.connect() as conn:
            progress = compute_progress(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="year",
                period_start="2026",
                sport="running",
                target_distance_m=3_650_000.0,  # 10 km/day
                as_of=dt.date(2026, 1, 5),  # target-as-of-today = 50 km
            )

        assert progress.current_distance_m == 50_000.0
        assert progress.target_distance_as_of_today_m == pytest.approx(50_000.0)
        assert progress.ahead_behind_m == pytest.approx(0.0)

    def test_future_period_has_zero_target_as_of_today_not_negative(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            progress = compute_progress(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="year",
                period_start="2027",
                sport="running",
                target_distance_m=1_000_000.0,
                as_of=dt.date(2026, 6, 1),  # before the goal period even starts
            )

        assert progress.daily == []
        assert progress.current_distance_m == 0.0
        assert progress.target_distance_as_of_today_m == 0.0
        assert progress.ahead_behind_m == 0.0

    def test_past_period_caps_target_line_at_the_full_goal(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            progress = compute_progress(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="year",
                period_start="2020",
                sport="running",
                target_distance_m=1_000_000.0,
                as_of=dt.date(2026, 1, 1),  # long after 2020 ended
            )

        assert progress.period_end == "2020-12-31"
        assert progress.target_distance_as_of_today_m == pytest.approx(1_000_000.0)
        assert len(progress.daily) == 366  # 2020 is a leap year

    def test_pct_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _seed_activity(
            engine, activity_id="a1", local_date="2026-01-01", sport="running", distance_m=250_000.0
        )

        with engine.connect() as conn:
            progress = compute_progress(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="year",
                period_start="2026",
                sport="running",
                target_distance_m=1_000_000.0,
                as_of=dt.date(2026, 1, 1),
            )

        assert progress.pct_complete == pytest.approx(0.25)


class TestRepeatedWeekStarts:
    def test_consecutive_seven_day_steps_across_month_and_year_ends(self) -> None:
        from perseverer.goals import repeated_week_starts

        assert repeated_week_starts("2026-12-21", 4) == [
            "2026-12-21",
            "2026-12-28",
            "2027-01-04",
            "2027-01-11",
        ]

    def test_one_week_is_just_the_start(self) -> None:
        from perseverer.goals import repeated_week_starts

        assert repeated_week_starts("2026-09-27", 1) == ["2026-09-27"]

    def test_a_bad_start_raises(self) -> None:
        from perseverer.goals import repeated_week_starts

        with pytest.raises(InvalidPeriod):
            repeated_week_starts("2026", 3)
