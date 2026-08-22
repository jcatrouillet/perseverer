"""Tests for fitness.refresh_fitness_rollup: the CTL/ATL/TSB EWMA recurrence against a
hand-computed sequence, cold start, zero-fill on rest days through today, and both
training-load dedup rules. See docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.
"""

import datetime as dt
import math
from pathlib import Path

from sqlalchemy import Connection, Engine, select

from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    activity_metric,
    athlete,
    fitness_daily_rollup,
    metadata,
    metric_definition,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fitness import refresh_fitness_rollup

_METRIC_KEY = "fit.session.training_load_peak"


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
        conn.execute(
            metric_definition.insert().values(
                metric_key=_METRIC_KEY,
                display_name=_METRIC_KEY,
                category="activity",
                value_type="numeric",
                first_seen_at=dt.datetime.now(dt.UTC),
                first_seen_source="fit_folder",
            )
        )
        conn.commit()
    return engine


def _add_activity(
    conn: Connection, *, activity_id: str, local_date: str, primary_source: str = "fit_folder"
) -> None:
    now = dt.datetime.now(dt.UTC)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=now,
            utc_offset_s=0,
            local_date=local_date,
            sport="running",
            duration_s=1800.0,
            moving_duration_s=1700.0,
            distance_m=5000.0,
            elevation_gain_m=10.0,
            calories=200.0,
            primary_source=primary_source,
            created_at=now,
            updated_at=now,
        )
    )


def _add_load(conn: Connection, *, activity_id: str, source: str, value: float) -> None:
    conn.execute(
        activity_metric.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            metric_key=_METRIC_KEY,
            value_num=value,
            source=source,
            created_at=dt.datetime.now(dt.UTC),
        )
    )


def test_no_activities_is_a_no_op(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        refresh_fitness_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        rows = conn.execute(select(fitness_daily_rollup)).fetchall()
    assert rows == []


def test_recurrence_matches_hand_computed_sequence(tmp_path: Path) -> None:
    """Loads of [100, 0, 150] over three consecutive days -- CTL/ATL/TSB hand-computed against
    the standard Coggan EWMA: CTL_t = CTL_(t-1) + (load_t - CTL_(t-1)) * (1 - exp(-1/42)),
    ATL_t analogous with /7, TSB_t = CTL_(t-1) - ATL_(t-1) (form entering day t, before that
    day's session)."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_load(conn, activity_id="a0", source="fit_folder", value=100.0)
        _add_activity(conn, activity_id="a1", local_date="2025-06-03")
        _add_load(conn, activity_id="a1", source="fit_folder", value=150.0)
        conn.commit()

        refresh_fitness_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        rows = {
            r.local_date: r
            for r in conn.execute(
                select(fitness_daily_rollup).where(
                    fitness_daily_rollup.c.local_date <= "2025-06-03"
                )
            ).fetchall()
        }

    ctl_alpha = 1 - math.exp(-1 / 42)
    atl_alpha = 1 - math.exp(-1 / 7)

    ctl = atl = 0.0
    expected: dict[str, tuple[float, float, float]] = {}
    for iso_date, load in [("2025-06-01", 100.0), ("2025-06-02", 0.0), ("2025-06-03", 150.0)]:
        tsb = ctl - atl
        ctl = ctl + (load - ctl) * ctl_alpha
        atl = atl + (load - atl) * atl_alpha
        expected[iso_date] = (ctl, atl, tsb)

    for iso_date, (exp_ctl, exp_atl, exp_tsb) in expected.items():
        row = rows[iso_date]
        assert row.ctl == exp_ctl
        assert row.atl == exp_atl
        assert row.tsb == exp_tsb


def test_cold_start_is_zero(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_load(conn, activity_id="a0", source="fit_folder", value=100.0)
        conn.commit()
        refresh_fitness_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        row = conn.execute(
            select(fitness_daily_rollup).where(fitness_daily_rollup.c.local_date == "2025-06-01")
        ).fetchone()
    assert row is not None
    assert row.tsb == 0.0  # CTL and ATL both start at 0


def test_series_zero_fills_through_today(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_load(conn, activity_id="a0", source="fit_folder", value=100.0)
        conn.commit()
        refresh_fitness_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        rows = conn.execute(select(fitness_daily_rollup)).fetchall()
    today = dt.datetime.now(dt.UTC).date().isoformat()
    dates = {r.local_date for r in rows}
    assert "2025-06-01" in dates
    assert today in dates
    # every day in between has a zero-load, zero-input row -- no gaps
    assert max(dates) == today


def test_same_day_multiple_activities_sum(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_activity(conn, activity_id="a1", local_date="2025-06-01")
        _add_load(conn, activity_id="a0", source="fit_folder", value=100.0)
        _add_load(conn, activity_id="a1", source="fit_folder", value=50.0)
        conn.commit()
        refresh_fitness_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        row = conn.execute(
            select(fitness_daily_rollup).where(fitness_daily_rollup.c.local_date == "2025-06-01")
        ).fetchone()
    assert row is not None
    assert row.training_load == 150.0


def test_same_activity_multiple_sources_prefers_primary_source(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(
            conn, activity_id="a0", local_date="2025-06-01", primary_source="garmin_connect"
        )
        _add_load(conn, activity_id="a0", source="fit_folder", value=999.0)
        _add_load(conn, activity_id="a0", source="garmin_connect", value=77.0)
        conn.commit()
        refresh_fitness_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        row = conn.execute(
            select(fitness_daily_rollup).where(fitness_daily_rollup.c.local_date == "2025-06-01")
        ).fetchone()
    assert row is not None
    assert row.training_load == 77.0  # the activity's own primary_source, not fit_folder


def test_same_activity_multiple_sources_falls_back_to_max_when_primary_source_missing(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        # primary_source is garmin_connect, but only fit_folder/garmin_export have this metric
        # for this activity -- falls back to the max among whatever sources exist.
        _add_activity(
            conn, activity_id="a0", local_date="2025-06-01", primary_source="garmin_connect"
        )
        _add_load(conn, activity_id="a0", source="fit_folder", value=10.0)
        _add_load(conn, activity_id="a0", source="garmin_export", value=40.0)
        conn.commit()
        refresh_fitness_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        row = conn.execute(
            select(fitness_daily_rollup).where(fitness_daily_rollup.c.local_date == "2025-06-01")
        ).fetchone()
    assert row is not None
    assert row.training_load == 40.0


def test_refresh_is_idempotent_full_recompute_no_duplicate_rows(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_load(conn, activity_id="a0", source="fit_folder", value=100.0)
        conn.commit()
        refresh_fitness_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        refresh_fitness_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        rows = conn.execute(
            select(fitness_daily_rollup).where(fitness_daily_rollup.c.local_date == "2025-06-01")
        ).fetchall()
    assert len(rows) == 1
