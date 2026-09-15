"""Tests for race_readiness.py: the interpolated weekly-distance/long-run targets, the
recency-weighted compliance calculation, and compute_race_readiness's own race-selection and
combination logic. See that module's own docstring for the full model."""

from __future__ import annotations

import datetime as dt
from itertools import pairwise
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, athlete, metadata, performance_daily_rollup, planned_race
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.race_readiness import (
    LONG_RUN_HALF_LIFE_DAYS,
    LONG_RUN_WINDOW_DAYS,
    READINESS_LONG_RUN_WEIGHT,
    READINESS_WEEKLY_DISTANCE_WEIGHT,
    WEEKLY_DISTANCE_HALF_LIFE_DAYS,
    WEEKLY_DISTANCE_WINDOW_DAYS,
    WeekValue,
    _dense_weekly_series,
    _weighted_compliance,
    compute_race_readiness,
    long_run_target_m,
    weekly_distance_target_m,
)

AS_OF = dt.date(2026, 9, 13)


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
                created_at=dt.datetime(2020, 1, 1),
            )
        )
        conn.commit()
    return eng


@pytest.fixture
def conn(engine: Engine):  # type: ignore[no-untyped-def]
    with engine.connect() as c:
        yield c


def _race(conn: Connection, *, athlete_id: str = DEFAULT_ATHLETE_ID, **kw: object) -> int:
    values: dict[str, object] = {
        "athlete_id": athlete_id,
        "sport": "running",
        "created_at": dt.datetime(2026, 1, 1),
        "updated_at": dt.datetime(2026, 1, 1),
        **kw,
    }
    result = conn.execute(planned_race.insert().values(**values))
    conn.commit()
    assert result.inserted_primary_key is not None
    race_id = result.inserted_primary_key[0]
    assert isinstance(race_id, int)
    return race_id


def _run(conn: Connection, *, activity_id: str, local_date: str, distance_m: float) -> None:
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=dt.datetime.fromisoformat(f"{local_date}T07:00:00"),
            utc_offset_s=0,
            local_date=local_date,
            sport="running",
            distance_m=distance_m,
            duration_s=distance_m / 3.0,
            moving_duration_s=distance_m / 3.0,
            primary_source="fit_folder",
            created_at=dt.datetime(2026, 1, 1),
            updated_at=dt.datetime(2026, 1, 1),
        )
    )


# --- targets -----------------------------------------------------------------------------


def test_targets_match_the_standard_distance_anchor_points_exactly() -> None:
    assert weekly_distance_target_m(5_000.0) == 25_000.0
    assert weekly_distance_target_m(10_000.0) == 32_000.0
    assert weekly_distance_target_m(21_097.5) == 40_000.0
    assert weekly_distance_target_m(42_195.0) == 55_000.0
    assert long_run_target_m(5_000.0) == 8_000.0
    assert long_run_target_m(42_195.0) == 29_000.0


def test_targets_interpolate_monotonically_between_anchors() -> None:
    t_10k = weekly_distance_target_m(10_000.0)
    t_15k = weekly_distance_target_m(15_000.0)
    t_half = weekly_distance_target_m(21_097.5)
    assert t_10k < t_15k < t_half


def test_targets_never_extrapolate_past_the_anchor_range() -> None:
    # Shorter than 5k, or longer than a marathon -- clamped to the nearest anchor, never a
    # fabricated value outside the range real published training plans actually cover.
    assert weekly_distance_target_m(1_000.0) == weekly_distance_target_m(5_000.0)
    assert weekly_distance_target_m(100_000.0) == weekly_distance_target_m(42_195.0)
    assert long_run_target_m(1_000.0) == long_run_target_m(5_000.0)
    assert long_run_target_m(100_000.0) == long_run_target_m(42_195.0)


# --- weighted compliance -------------------------------------------------------------------


def _week_start(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def test_weighted_compliance_is_one_when_every_week_meets_target() -> None:
    weeks = {_week_start(AS_OF) - dt.timedelta(days=7 * i): 60_000.0 for i in range(30)}
    assert _weighted_compliance(
        weeks, target_m=55_000.0, as_of=AS_OF, window_days=182, half_life_days=28.0
    ) == pytest.approx(1.0)


def test_weighted_compliance_is_zero_with_no_data_at_all() -> None:
    result = _weighted_compliance(
        {}, target_m=55_000.0, as_of=AS_OF, window_days=182, half_life_days=28.0
    )
    assert result == 0.0


def _compliance(weeks: dict[dt.date, float]) -> float:
    return _weighted_compliance(
        weeks, target_m=55_000.0, as_of=AS_OF, window_days=182, half_life_days=28.0
    )


def test_weighted_compliance_never_gives_more_than_full_credit_for_exceeding_target() -> None:
    # A week at 2x target counts the same as a week exactly at target -- never "200% ready".
    assert _compliance({AS_OF: 110_000.0}) == _compliance({AS_OF: 55_000.0})


def test_weighted_compliance_weighs_recent_weeks_more_than_older_ones() -> None:
    w = _week_start(AS_OF)
    recent_strong: dict[dt.date, float] = {}
    recent_weak: dict[dt.date, float] = {}
    for i in range(26):
        recent_strong[w] = 60_000.0 if i < 8 else 10_000.0
        recent_weak[w] = 10_000.0 if i < 8 else 60_000.0
        w -= dt.timedelta(days=7)

    assert _compliance(recent_strong) > _compliance(recent_weak)


# --- compute_race_readiness ----------------------------------------------------------------


def test_returns_none_without_any_upcoming_race(conn: Connection) -> None:
    assert compute_race_readiness(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF) is None


def test_ignores_a_past_race_and_a_non_running_race(conn: Connection) -> None:
    _race(conn, local_date="2026-01-01", name="Already happened", distance_m=10_000.0)
    _race(
        conn,
        local_date="2026-12-01",
        name="Charity bike ride",
        sport="cycling",
        distance_m=50_000.0,
    )
    assert compute_race_readiness(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF) is None


def test_picks_the_nearest_upcoming_running_race_by_default(conn: Connection) -> None:
    _race(conn, local_date="2026-12-06", name="Later marathon", distance_m=42_195.0)
    nearer_id = _race(conn, local_date="2026-10-11", name="Nearer half", distance_m=21_097.5)

    readiness = compute_race_readiness(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF)
    assert readiness is not None
    assert readiness.race_id == nearer_id
    assert readiness.race_name == "Nearer half"


def test_an_explicit_race_id_overrides_the_nearest_upcoming_default(conn: Connection) -> None:
    _race(conn, local_date="2026-10-11", name="Nearer half", distance_m=21_097.5)
    later_id = _race(conn, local_date="2026-12-06", name="Later marathon", distance_m=42_195.0)

    readiness = compute_race_readiness(
        conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF, race_id=later_id
    )
    assert readiness is not None
    assert readiness.race_id == later_id


def test_an_explicit_race_id_belonging_to_another_athlete_returns_none(
    conn: Connection, engine: Engine
) -> None:
    with engine.connect() as other_conn:
        other_conn.execute(
            athlete.insert().values(
                id="other-athlete",
                display_name="Other",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime(2020, 1, 1),
            )
        )
        other_conn.commit()
        other_race_id = _race(
            other_conn,
            athlete_id="other-athlete",
            local_date="2026-12-06",
            name="Not yours",
            distance_m=42_195.0,
        )

    result = compute_race_readiness(
        conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF, race_id=other_race_id
    )
    assert result is None


def test_readiness_combines_weekly_and_long_run_with_the_documented_weights(
    conn: Connection,
) -> None:
    race_id = _race(conn, local_date="2026-12-06", name="Marathon", distance_m=42_195.0)
    # One perfect week (55km, including a 29km long run) right at as_of -- dominates the
    # recency-weighted average given the short half-life relative to the rest of the empty window.
    _run(conn, activity_id="r1", local_date=AS_OF.isoformat(), distance_m=29_000.0)
    _run(
        conn,
        activity_id="r2",
        local_date=(AS_OF - dt.timedelta(days=1)).isoformat(),
        distance_m=26_000.0,
    )
    conn.commit()

    readiness = compute_race_readiness(
        conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF, race_id=race_id
    )
    assert readiness is not None
    expected = (
        READINESS_WEEKLY_DISTANCE_WEIGHT * readiness.current.weekly_distance_compliance
        + READINESS_LONG_RUN_WEIGHT * readiness.current.long_run_compliance
    )
    assert readiness.current.readiness == pytest.approx(expected)


def test_readiness_reuses_the_existing_vdot_based_prediction(conn: Connection) -> None:
    race_id = _race(conn, local_date="2026-12-06", name="Marathon", distance_m=42_195.0)
    conn.execute(
        performance_daily_rollup.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            local_date="2026-09-01",
            predicted_marathon_s=14_400.0,
            refreshed_at=dt.datetime(2026, 9, 1),
        )
    )
    conn.commit()

    readiness = compute_race_readiness(
        conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF, race_id=race_id
    )
    assert readiness is not None
    assert readiness.predicted_duration_s == 14_400.0


def test_readiness_prediction_is_none_for_a_non_standard_distance(conn: Connection) -> None:
    race_id = _race(conn, local_date="2026-12-06", name="15k fun run", distance_m=15_000.0)
    readiness = compute_race_readiness(
        conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF, race_id=race_id
    )
    assert readiness is not None
    assert readiness.predicted_duration_s is None


def test_history_covers_the_full_weekly_distance_window_and_ends_at_as_of(
    conn: Connection,
) -> None:
    race_id = _race(conn, local_date="2026-12-06", name="Marathon", distance_m=42_195.0)
    readiness = compute_race_readiness(
        conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF, race_id=race_id
    )
    assert readiness is not None
    assert readiness.history[0].as_of == AS_OF - dt.timedelta(days=WEEKLY_DISTANCE_WINDOW_DAYS)
    assert readiness.history[-1].as_of == AS_OF
    assert readiness.history[-1] == readiness.current


# --- dense weekly series -------------------------------------------------------------------


def test_dense_weekly_series_zero_fills_every_week_with_no_data() -> None:
    series = _dense_weekly_series({}, as_of=AS_OF, window_days=70)
    assert len(series) > 1
    assert all(w.distance_m == 0.0 for w in series)
    assert series[0].week_start == _week_start(AS_OF - dt.timedelta(days=70))
    assert series[-1].week_start == _week_start(AS_OF)


def test_dense_weekly_series_carries_real_values_through_unomitted() -> None:
    w = _week_start(AS_OF)
    series = _dense_weekly_series({w: 42_000.0}, as_of=AS_OF, window_days=70)
    assert series[-1] == WeekValue(week_start=w, distance_m=42_000.0)
    # Every other week in the window is still present, just zero -- never omitted.
    assert all(v.distance_m == 0.0 for v in series[:-1])


def test_dense_weekly_series_is_ordered_oldest_first_one_entry_per_week() -> None:
    series = _dense_weekly_series({}, as_of=AS_OF, window_days=70)
    starts = [w.week_start for w in series]
    assert starts == sorted(starts)
    for a, b in pairwise(starts):
        assert (b - a).days == 7


def test_readiness_exposes_the_realized_weekly_and_long_run_series(conn: Connection) -> None:
    race_id = _race(conn, local_date="2026-12-06", name="Marathon", distance_m=42_195.0)
    _run(conn, activity_id="r1", local_date=AS_OF.isoformat(), distance_m=29_000.0)
    _run(
        conn,
        activity_id="r2",
        local_date=(AS_OF - dt.timedelta(days=1)).isoformat(),
        distance_m=26_000.0,
    )
    conn.commit()

    readiness = compute_race_readiness(
        conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF, race_id=race_id
    )
    assert readiness is not None
    week = _week_start(AS_OF)
    # weekly_distance_series sums both runs into this week's total; long_run_series carries only
    # the longest single run that week, not the sum -- this app's own stand-in for a "long run"
    # tag it doesn't have.
    assert readiness.weekly_distance_series[-1] == WeekValue(week_start=week, distance_m=55_000.0)
    assert readiness.long_run_series[-1] == WeekValue(week_start=week, distance_m=29_000.0)
    assert readiness.weekly_distance_series[0].week_start == _week_start(
        AS_OF - dt.timedelta(days=WEEKLY_DISTANCE_WINDOW_DAYS)
    )
    assert readiness.long_run_series[0].week_start == _week_start(
        AS_OF - dt.timedelta(days=LONG_RUN_WINDOW_DAYS)
    )
    assert len(readiness.weekly_distance_series) > len(readiness.long_run_series)


def test_module_constants_are_the_documented_values() -> None:
    # Pins the exact policy numbers this module's own docstring cites, so a future edit to them
    # is a deliberate, visible change rather than an accidental drift.
    assert WEEKLY_DISTANCE_WINDOW_DAYS == 182
    assert LONG_RUN_WINDOW_DAYS == 70
    assert WEEKLY_DISTANCE_HALF_LIFE_DAYS == 28.0
    assert LONG_RUN_HALF_LIFE_DAYS == 14.0
    assert READINESS_WEEKLY_DISTANCE_WEIGHT + READINESS_LONG_RUN_WEIGHT == 1.0
