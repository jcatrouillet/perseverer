"""Tests for performance.refresh_vdot: running-only scoping, GAP-adjusted-vs-fallback
computation, full-recompute-clears-stale-rows behavior (a sport correction moving an activity out
of "running" must not leave a stray VDOT row behind), and idempotency.
"""

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import Connection, Engine, select

from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity, activity_metric, activity_stream, athlete, metadata
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.fit.types import StreamPoint
from sporthealth.performance import VDOT_METRIC_KEY, refresh_vdot
from sporthealth.streams import write_activity_stream


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


def _add_activity(
    conn: Connection,
    *,
    activity_id: str,
    sport: str = "running",
    distance_m: float | None = 5000.0,
    moving_duration_s: float | None = 1800.0,
) -> None:
    now = dt.datetime.now(dt.UTC)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=now,
            utc_offset_s=0,
            local_date="2026-01-01",
            sport=sport,
            duration_s=moving_duration_s,
            moving_duration_s=moving_duration_s,
            distance_m=distance_m,
            elevation_gain_m=0.0,
            calories=200.0,
            primary_source="fit_folder",
            created_at=now,
            updated_at=now,
        )
    )


def _add_flat_stream(parquet_dir: Path, conn: Connection, *, activity_id: str) -> None:
    points = [
        StreamPoint(
            timestamp_utc=dt.datetime(2026, 1, 1, tzinfo=dt.UTC) + dt.timedelta(seconds=i * 10),
            values={"distance_m": float(i * 100), "altitude_m": 50.0},
        )
        for i in range(11)
    ]
    relative_path, n_samples, channels = write_activity_stream(
        parquet_dir, DEFAULT_ATHLETE_ID, activity_id, points
    )
    conn.execute(
        activity_stream.insert().values(
            activity_id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            parquet_path=relative_path,
            n_samples=n_samples,
            channels=json.dumps(channels),
        )
    )


def test_writes_vdot_only_for_running_activities(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    parquet_dir = tmp_path / "parquet"
    with engine.connect() as conn:
        _add_activity(conn, activity_id="run1", sport="running")
        _add_activity(conn, activity_id="ride1", sport="cycling")
        conn.commit()

        written = refresh_vdot(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        assert written == 1
        rows = conn.execute(
            select(activity_metric.c.activity_id).where(
                activity_metric.c.metric_key == VDOT_METRIC_KEY
            )
        ).fetchall()
        assert [r.activity_id for r in rows] == ["run1"]


def test_falls_back_to_unadjusted_vdot_when_no_stream_exists(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    parquet_dir = tmp_path / "parquet"
    with engine.connect() as conn:
        _add_activity(conn, activity_id="run1")
        conn.commit()

        refresh_vdot(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        row = conn.execute(
            select(activity_metric.c.value_num).where(
                activity_metric.c.metric_key == VDOT_METRIC_KEY
            )
        ).fetchone()
        assert row is not None
        assert row.value_num is not None


def test_uses_the_stream_for_gap_adjustment_when_available(tmp_path: Path) -> None:
    # Two identical (distance/duration) running activities, one with a flat stream attached and
    # one without -- a flat stream has a GAP factor of exactly 1.0, so it should produce the same
    # VDOT as the no-stream fallback (also an implicit factor of 1.0).
    engine = _engine(tmp_path)
    parquet_dir = tmp_path / "parquet"
    with engine.connect() as conn:
        _add_activity(conn, activity_id="with_stream")
        _add_flat_stream(parquet_dir, conn, activity_id="with_stream")
        _add_activity(conn, activity_id="without_stream")
        conn.commit()

        refresh_vdot(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        rows = conn.execute(
            select(activity_metric.c.activity_id, activity_metric.c.value_num).where(
                activity_metric.c.metric_key == VDOT_METRIC_KEY
            )
        ).fetchall()
        values: dict[str, float | None] = {r.activity_id: r.value_num for r in rows}

    assert values["with_stream"] == values["without_stream"]


def test_full_recompute_clears_a_stale_row_for_an_activity_no_longer_running(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    parquet_dir = tmp_path / "parquet"
    with engine.connect() as conn:
        _add_activity(conn, activity_id="run1", sport="running")
        conn.commit()
        refresh_vdot(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        # Simulate a sport correction moving the activity out of "running".
        conn.execute(activity.update().where(activity.c.id == "run1").values(sport="cycling"))
        conn.commit()

        refresh_vdot(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        rows = conn.execute(
            select(activity_metric.c.id).where(activity_metric.c.metric_key == VDOT_METRIC_KEY)
        ).fetchall()
        assert rows == []


def test_skips_an_activity_with_no_distance(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    parquet_dir = tmp_path / "parquet"
    with engine.connect() as conn:
        _add_activity(conn, activity_id="run1", distance_m=None)
        conn.commit()

        written = refresh_vdot(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        assert written == 0


def test_idempotent_across_repeated_calls(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    parquet_dir = tmp_path / "parquet"
    with engine.connect() as conn:
        _add_activity(conn, activity_id="run1")
        conn.commit()

        refresh_vdot(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        refresh_vdot(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        rows = conn.execute(
            select(activity_metric.c.id).where(activity_metric.c.metric_key == VDOT_METRIC_KEY)
        ).fetchall()
        assert len(rows) == 1
