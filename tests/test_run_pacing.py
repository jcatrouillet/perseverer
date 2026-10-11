"""Tests for run_pacing: the pure split computation (halves, start, finish, moving time) and the
incremental refresh (only new or changed runs are recomputed; non-runs lose their rows)."""

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
    activity_trim_override,
    athlete,
    metadata,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import StreamPoint
from perseverer.run_pacing import (
    FINISH_KEY,
    FIRST_HALF_KEY,
    SECOND_HALF_KEY,
    SIGNATURE_KEY,
    START_KEY,
    compute_run_pacing,
    refresh_run_pacing,
)
from perseverer.streams import write_activity_stream

START = dt.datetime(2026, 1, 1, 8, 0)


def _track(segments: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """One sample per second through consecutive (distance_m, pace_s_per_km) segments; a pace of
    0 means standing still for `distance_m` seconds."""
    t, d = [0.0], [0.0]
    for distance, pace in segments:
        if pace == 0:
            for _ in range(int(distance)):
                t.append(t[-1] + 1)
                d.append(d[-1])
            continue
        speed = 1000.0 / pace
        for _ in range(round(distance / speed)):
            t.append(t[-1] + 1)
            d.append(d[-1] + speed)
    return np.array(t), np.array(d)


def test_negative_split_with_a_slow_start_and_a_fast_finish() -> None:
    t, d = _track([(2000, 360), (3000, 330), (3000, 300), (2000, 270)])  # 10 km
    pacing = compute_run_pacing(t, d)
    assert pacing is not None
    assert pacing.start_s_per_km == pytest.approx(360, abs=1)
    assert pacing.finish_s_per_km == pytest.approx(270, abs=1)
    assert pacing.first_half_s_per_km == pytest.approx((720 + 990) / 5, abs=1)
    assert pacing.second_half_s_per_km == pytest.approx((900 + 540) / 5, abs=1)


def test_a_stop_does_not_count_as_running_time() -> None:
    steady = compute_run_pacing(*_track([(6000, 300)]))
    with_stop = compute_run_pacing(*_track([(1000, 300), (90, 0), (5000, 300)]))
    assert steady is not None and with_stop is not None
    assert with_stop.start_s_per_km == pytest.approx(steady.start_s_per_km, abs=1)
    assert with_stop.first_half_s_per_km == pytest.approx(300, abs=1)


def test_seconds_where_gps_distance_stalls_still_count_as_running() -> None:
    t, d = _track([(6000, 300)])
    # GPS distance doesn't advance for one sample in every ten: the runner is still running.
    stalled = d.copy()
    stalled[::10] = np.concatenate(([0.0], d[::10][1:] - 1000 / 300))
    pacing = compute_run_pacing(t, stalled)
    assert pacing is not None
    assert pacing.first_half_s_per_km == pytest.approx(300, abs=2)
    assert pacing.second_half_s_per_km == pytest.approx(300, abs=2)


def test_runs_under_5_km_have_no_pacing() -> None:
    assert compute_run_pacing(*_track([(4900, 300)])) is None
    assert compute_run_pacing(np.array([]), np.array([])) is None


# --- refresh_run_pacing ---------------------------------------------------------------------


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


def _add_run(
    conn: Connection, parquet_dir: Path, activity_id: str, segments: list[tuple[float, float]]
) -> None:
    t, d = _track(segments)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=START,
            utc_offset_s=0,
            local_date="2026-01-01",
            sport="running",
            duration_s=float(t[-1]),
            moving_duration_s=float(t[-1]),
            distance_m=float(d[-1]),
            primary_source="fit_folder",
            created_at=START,
            updated_at=START,
        )
    )
    points = [
        StreamPoint(
            timestamp_utc=(START + dt.timedelta(seconds=float(ti))).replace(tzinfo=dt.UTC),
            values={"distance_m": float(di)},
        )
        for ti, di in zip(t, d, strict=True)
    ]
    path, n_samples, channels = write_activity_stream(
        parquet_dir, DEFAULT_ATHLETE_ID, activity_id, points
    )
    conn.execute(
        activity_stream.insert().values(
            activity_id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            parquet_path=path,
            n_samples=n_samples,
            channels=json.dumps(channels),
        )
    )


def _metrics(conn: Connection, activity_id: str) -> dict[str, float | None]:
    rows = conn.execute(
        select(activity_metric.c.metric_key, activity_metric.c.value_num).where(
            activity_metric.c.activity_id == activity_id
        )
    ).fetchall()
    return {r.metric_key: r.value_num for r in rows}


def test_refresh_stores_paces_and_only_recomputes_what_changed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    parquet_dir = tmp_path / "parquet"
    with engine.connect() as conn:
        _add_run(conn, parquet_dir, "long", [(2000, 360), (6000, 300), (2000, 270)])
        _add_run(conn, parquet_dir, "short", [(3000, 300)])
        assert refresh_run_pacing(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID) == 2
        long_run = _metrics(conn, "long")
        assert long_run[START_KEY] == pytest.approx(360, abs=1)
        assert long_run[FINISH_KEY] == pytest.approx(270, abs=1)
        assert {FIRST_HALF_KEY, SECOND_HALF_KEY, SIGNATURE_KEY} <= long_run.keys()
        assert set(_metrics(conn, "short")) == {SIGNATURE_KEY}  # processed, too short to pace

        # Nothing changed: nothing is read again.
        assert refresh_run_pacing(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID) == 0

        # A trim changes what the start covers: that run alone is recomputed.
        conn.execute(
            activity_trim_override.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_start_time_utc=START,
                trim_start_s=720.0,  # drop the slow first 2 km
                trim_end_s=None,
                created_at=START,
                updated_at=START,
            )
        )
        assert refresh_run_pacing(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID) == 2
        assert _metrics(conn, "long")[START_KEY] == pytest.approx(300, abs=1)

        # A run reclassified as a hike loses its pacing rows.
        conn.execute(activity.update().where(activity.c.id == "long").values(sport="hiking"))
        refresh_run_pacing(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        assert _metrics(conn, "long") == {}
