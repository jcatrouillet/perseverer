"""ride_power.py: per-ride best power at import, and FTP / FTP/kg / Critical Power / VO2max over
the trailing window."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import Connection, Engine, select

from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    activity_metric,
    activity_stream,
    athlete,
    health_observation,
    metadata,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import StreamPoint
from perseverer.metrics.registry import get_or_register_metric
from perseverer.ride_power import (
    SIGNATURE_KEY,
    best_power_key,
    compute_best_powers,
    compute_cycling_power,
    compute_max_speed,
    fit_critical_power,
    refresh_ride_power,
)
from perseverer.streams import write_activity_stream

AS_OF = dt.date(2026, 6, 30)


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


def _add_ride(conn: Connection, activity_id: str, local_date: str, sport: str = "cycling") -> None:
    start = dt.datetime.fromisoformat(f"{local_date}T08:00:00")
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start,
            utc_offset_s=0,
            local_date=local_date,
            sport=sport,
            name=activity_id,
            duration_s=3600.0,
            distance_m=30000.0,
            primary_source="fit_folder",
            created_at=start,
            updated_at=start,
        )
    )


def _add_best(conn: Connection, activity_id: str, duration_s: int, watts: float) -> None:
    key = best_power_key(duration_s)
    get_or_register_metric(conn, metric_key=key, source="perseverer", category="performance")
    conn.execute(
        activity_metric.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            metric_key=key,
            value_num=watts,
            source="perseverer",
            created_at=dt.datetime.now(dt.UTC),
        )
    )


def _add_weight(conn: Connection, local_date: str, kg: float) -> None:
    get_or_register_metric(conn, metric_key="eufy.scale.weight", source="eufy", category="health")
    conn.execute(
        health_observation.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            metric_key="eufy.scale.weight",
            observed_at_utc=dt.datetime.fromisoformat(f"{local_date}T07:00:00"),
            local_date=local_date,
            aggregation="instant",
            value_num=kg,
            source="eufy",
        )
    )


def test_critical_power_is_the_slope_of_work_over_time() -> None:
    # Generated from CP = 200 W, W' = 15 kJ: P(t) = 200 + 15000 / t.
    bests = {t: 200 + 15000 / t for t in (180, 300, 720, 1200)}
    cp, w_prime = fit_critical_power(bests)  # type: ignore[misc]
    assert cp == pytest.approx(200)
    assert w_prime == pytest.approx(15000)


def test_critical_power_needs_three_durations_including_twelve_minutes_or_more() -> None:
    assert fit_critical_power({180: 300.0, 300: 280.0}) is None
    assert fit_critical_power({180: 300.0, 300: 280.0, 1200: 0.1}) is None  # non-physical


def test_best_powers_from_a_stream() -> None:
    t = np.arange(0, 1500, 1.0)
    power = np.where((t >= 600) & (t < 900), 300.0, 150.0)  # 5 hard minutes in an easy ride
    bests = compute_best_powers(t, power)
    assert bests[300] == pytest.approx(300)
    assert bests[1200] == pytest.approx((300 * 300 + 150 * 900) / 1200)


def test_refresh_stores_each_rides_bests_and_skips_unchanged_rides(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    parquet_dir = tmp_path / "parquet"
    with engine.connect() as conn:
        _add_ride(conn, "ride", "2026-06-01")
        start = dt.datetime(2026, 6, 1, 8, tzinfo=dt.UTC)
        points = [
            StreamPoint(timestamp_utc=start + dt.timedelta(seconds=i), values={"power": 250.0})
            for i in range(1300)
        ]
        path, n, channels = write_activity_stream(parquet_dir, DEFAULT_ATHLETE_ID, "ride", points)
        conn.execute(
            activity_stream.insert().values(
                activity_id="ride",
                athlete_id=DEFAULT_ATHLETE_ID,
                parquet_path=path,
                n_samples=n,
                channels=json.dumps(channels),
            )
        )
        assert refresh_ride_power(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID) == 1
        stored = {
            r.metric_key: r.value_num
            for r in conn.execute(select(activity_metric.c.metric_key, activity_metric.c.value_num))
        }
        assert stored[best_power_key(1200)] == pytest.approx(250)
        assert SIGNATURE_KEY in stored
        assert refresh_ride_power(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID) == 0


def test_figures_over_the_trailing_window_ignore_someone_elses_weight(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_ride(conn, "old", "2026-01-10")  # outside the 90-day window
        _add_best(conn, "old", 1200, 400.0)
        _add_ride(conn, "recent", "2026-06-20")
        for duration_s, watts in ((180, 320.0), (300, 300.0), (720, 270.0), (1200, 250.0)):
            _add_best(conn, "recent", duration_s, watts)
        for day in ("2026-05-01", "2026-05-15", "2026-06-01"):
            _add_weight(conn, day, 80.0)
        _add_weight(conn, "2026-06-25", 48.0)  # another person on a shared scale
        conn.commit()
        result = compute_cycling_power(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF)

    current = result.current
    assert current is not None
    assert current.ftp_w == pytest.approx(0.95 * 250)  # the old 400 W is outside the window
    assert current.weight_kg == 80.0
    assert current.ftp_w_per_kg == pytest.approx(0.95 * 250 / 80)
    assert current.vo2max == pytest.approx(10.8 * 300 / 80 + 7)
    assert current.cp_w is not None and current.w_prime_j is not None
    assert current.best_power_ride[1200].activity_id == "recent"
    assert [p.as_of for p in result.history] == ["2026-01-10", "2026-06-20"]


def test_no_power_ride_reports_what_is_missing(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        result = compute_cycling_power(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF)
    assert result.current is None
    assert result.history == []
    assert any("No ride with power" in m for m in result.missing)


def test_max_speed_is_the_fastest_five_seconds_not_a_single_gps_spike() -> None:
    t = np.arange(0, 120, 1.0)
    speed = np.full(t.size, 8.0)
    speed[50:58] = 12.0  # 8 seconds at 12 m/s
    speed[90] = 40.0  # a one-sample GPS spike
    distance = np.concatenate(([0.0], np.cumsum(speed[:-1])))
    max_speed = compute_max_speed(t, distance)
    assert max_speed is not None
    assert max_speed < 15.0  # the spike is averaged over 5 seconds
    assert max_speed >= 12.0


def test_exclude_commutes_drops_tagged_rides_from_the_figures(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_ride(conn, "training", "2026-06-10")
        _add_best(conn, "training", 1200, 200.0)
        _add_ride(conn, "commute", "2026-06-20")
        _add_best(conn, "commute", 1200, 260.0)
        conn.execute(activity.update().where(activity.c.id == "commute").values(is_commute=True))
        conn.commit()
        everything = compute_cycling_power(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF)
        filtered = compute_cycling_power(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=AS_OF, exclude_commutes=True
        )
    assert everything.current is not None and everything.current.ftp_w == pytest.approx(247.0)
    assert filtered.current is not None and filtered.current.ftp_w == pytest.approx(190.0)
