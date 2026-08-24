"""Tests for activity_trim.py: recomputing an activity's distance/duration/elevation/heart-rate/
route/laps from a trimmed window of its own Parquet stream, surviving a simulated rebuild, and
undoing back to the pristine pre-trim state via the archived raw FIT bytes. See that module's own
docstring for why calories/training load are cleared (not estimated) by a trim but fully
recoverable on undo.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import Connection, Engine, select

from perseverer.activity_trim import (
    _restore_from_parsed,
    apply_activity_trim_overrides,
    clear_activity_trim,
    set_activity_trim,
)
from perseverer.archive import archive_raw_bytes
from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    activity_metric,
    activity_source_link,
    activity_stream,
    activity_trim_override,
    athlete,
    lap,
    metadata,
    route_geom,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import CanonicalActivity, ParsedLap, ParsedMetric
from perseverer.ingest_dispatch import FIT_KIND
from perseverer.metrics.registry import get_or_register_metric

FIXTURE = Path(__file__).parent / "fixtures" / "fit" / "synthetic_run.fit"
ACTIVITY_ID = "01TESTACTIVITY0000000000TR"
ACTIVITY_START = dt.datetime(2024, 6, 1, 8, 0, 0)


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


def _write_parquet(
    path: Path, *, n_samples: int = 200, start: dt.datetime = ACTIVITY_START
) -> None:
    """One sample per second: a straight line north (lat climbs, lon fixed), steady climb in
    altitude, heart rate ramping, and a device-recorded cumulative distance_m -- everything
    _recompute_window needs, with values simple enough to hand-check expected results."""
    timestamps = [start + dt.timedelta(seconds=i) for i in range(n_samples)]
    table = pa.table(
        {
            "timestamp_utc": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
            "lat": [37.0 + i * 0.0001 for i in range(n_samples)],
            "lon": [-122.0] * n_samples,
            "distance_m": [float(i * 2) for i in range(n_samples)],  # 2 m/s pace
            "altitude_m": [100.0 + i * 0.1 for i in range(n_samples)],  # steady climb
            "heart_rate": [float(100 + (i % 40)) for i in range(n_samples)],
        }
    )
    pq.write_table(table, path)


def _seed_activity(
    conn: Connection, archive_root: Path, parquet_path: Path, *, n_samples: int = 200
) -> None:
    """A fully-wired activity: real archived raw bytes (so clear_activity_trim can reparse
    them), a source link, a hand-crafted Parquet stream (so set_activity_trim's recompute has
    known ground truth), two laps (one that will end up entirely outside a trim window, one that
    will be clipped), a route, and avg/max-heart-rate + training-load metrics -- all set to
    values distinct from both the fixture's real parsed values and the Parquet's own recomputed
    values, so a test failure can't accidentally pass by leaving a field untouched."""
    content = FIXTURE.read_bytes()
    raw_object_id = archive_raw_bytes(
        conn,
        archive_root,
        athlete_id=DEFAULT_ATHLETE_ID,
        source="fit_folder",
        kind=FIT_KIND,
        content=content,
    )
    now = dt.datetime.now(dt.UTC)
    conn.execute(
        activity.insert().values(
            id=ACTIVITY_ID,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=ACTIVITY_START,
            utc_offset_s=0,
            local_date="2024-06-01",
            sport="hiking",
            sub_sport="generic",
            name="Pre-trim name",
            duration_s=99999.0,
            moving_duration_s=99999.0,
            distance_m=99999.0,
            elevation_gain_m=999.0,
            calories=1234.0,
            primary_source="fit_folder",
            created_at=now,
            updated_at=now,
        )
    )
    conn.execute(
        activity_source_link.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=ACTIVITY_ID,
            source="fit_folder",
            external_id="test-external-id",
            raw_object_id=raw_object_id,
            ingested_at=dt.datetime.now(dt.UTC),
        )
    )
    _write_parquet(parquet_path, n_samples=n_samples)
    conn.execute(
        activity_stream.insert().values(
            activity_id=ACTIVITY_ID,
            athlete_id=DEFAULT_ATHLETE_ID,
            parquet_path=parquet_path.name,
            n_samples=n_samples,
            channels='["lat", "lon", "distance_m", "altitude_m", "heart_rate"]',
        )
    )
    conn.execute(
        lap.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=ACTIVITY_ID,
            lap_index=0,
            start_time_utc=ACTIVITY_START,
            duration_s=50.0,
            distance_m=100.0,
        )
    )
    conn.execute(
        lap.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=ACTIVITY_ID,
            lap_index=1,
            start_time_utc=ACTIVITY_START + dt.timedelta(seconds=100),
            duration_s=50.0,
            distance_m=100.0,
        )
    )
    conn.execute(
        route_geom.insert().values(
            activity_id=ACTIVITY_ID,
            athlete_id=DEFAULT_ATHLETE_ID,
            encoded_polyline="pretrim",
            simplified_polyline="pretrim",
            min_lat=1.0,
            min_lng=1.0,
            max_lat=2.0,
            max_lng=2.0,
            start_lat=1.0,
            start_lng=1.0,
            end_lat=2.0,
            end_lng=2.0,
        )
    )
    for key, value in (
        ("fit.session.avg_heart_rate", 111.0),
        ("fit.session.max_heart_rate", 222.0),
        ("fit.session.training_load_peak", 55.0),
        ("fit.session.total_descent", 888.0),
    ):
        get_or_register_metric(conn, metric_key=key, source="fit_folder", category="activity")
        conn.execute(
            activity_metric.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=ACTIVITY_ID,
                metric_key=key,
                value_num=value,
                source="fit_folder",
                created_at=now,
            )
        )
    conn.commit()


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


def test_set_activity_trim_recomputes_distance_duration_elevation_hr(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    with engine.connect() as conn:
        _seed_activity(conn, archive_root, parquet_dir / f"{ACTIVITY_ID}.parquet")

        # Keep seconds [50, 150] of the 200-second recording.
        set_activity_trim(
            conn,
            con,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=ACTIVITY_ID,
            trim_start_s=50.0,
            trim_end_s=150.0,
        )
        conn.commit()

        row = conn.execute(select(activity).where(activity.c.id == ACTIVITY_ID)).fetchone()
        assert row is not None
        assert row.duration_s == pytest.approx(100.0, abs=1)
        # 2 m/s pace over [50,150] elapsed -> distance_m[150] - distance_m[50] = 300 - 100 = 200.
        assert row.distance_m == pytest.approx(200.0, abs=1)
        # altitude climbs 0.1/s monotonically -> gain over 100s window is exactly 100 * 0.1.
        assert row.elevation_gain_m == pytest.approx(10.0, abs=1)
        assert row.moving_duration_s == pytest.approx(100.0, abs=1)
        # Peak within the kept window only, not the full recording's own max (119.9 at i=199) --
        # altitude_m[150] = 100.0 + 150*0.1 = 115.0, the window's last (and highest) sample.
        assert row.max_altitude_m == pytest.approx(115.0, abs=0.5)


def test_set_activity_trim_clears_calories_and_training_load(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    with engine.connect() as conn:
        _seed_activity(conn, archive_root, parquet_dir / f"{ACTIVITY_ID}.parquet")

        set_activity_trim(
            conn,
            con,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=ACTIVITY_ID,
            trim_start_s=50.0,
            trim_end_s=150.0,
        )
        conn.commit()

        row = conn.execute(
            select(activity.c.calories).where(activity.c.id == ACTIVITY_ID)
        ).fetchone()
        assert row is not None
        assert row.calories is None

        tl_row = conn.execute(
            select(activity_metric).where(
                activity_metric.c.activity_id == ACTIVITY_ID,
                activity_metric.c.metric_key == "fit.session.training_load_peak",
            )
        ).fetchone()
        assert tl_row is None

        # Avg/max heart rate ARE recomputable from the stream -- corrected, not cleared.
        avg_row = conn.execute(
            select(activity_metric.c.value_num).where(
                activity_metric.c.activity_id == ACTIVITY_ID,
                activity_metric.c.metric_key == "fit.session.avg_heart_rate",
            )
        ).fetchone()
        assert avg_row is not None
        assert avg_row.value_num != 111.0  # the pre-trim placeholder value


def test_set_activity_trim_recomputes_total_descent(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Caught by a real browser check against the flagged activity: total descent lives in an
    activity_metric row (ActivityStatsGrid.tsx's "Elevation loss" tile), not
    activity.elevation_gain_m's own column -- easy to miss and leave stale after a trim."""
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    with engine.connect() as conn:
        _seed_activity(conn, archive_root, parquet_dir / f"{ACTIVITY_ID}.parquet")

        set_activity_trim(
            conn,
            con,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=ACTIVITY_ID,
            trim_start_s=50.0,
            trim_end_s=150.0,
        )
        conn.commit()

        row = conn.execute(
            select(activity_metric.c.value_num).where(
                activity_metric.c.activity_id == ACTIVITY_ID,
                activity_metric.c.metric_key == "fit.session.total_descent",
            )
        ).fetchone()
        assert row is not None
        # The fixture's altitude climbs monotonically -- no descent in the kept window at all --
        # so the stale 888.0 placeholder should be corrected down to 0.0, not left untouched.
        assert row.value_num == pytest.approx(0.0)


def test_set_activity_trim_recomputes_route(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    with engine.connect() as conn:
        _seed_activity(conn, archive_root, parquet_dir / f"{ACTIVITY_ID}.parquet")

        set_activity_trim(
            conn,
            con,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=ACTIVITY_ID,
            trim_start_s=50.0,
            trim_end_s=150.0,
        )
        conn.commit()

        route_row = conn.execute(
            select(route_geom).where(route_geom.c.activity_id == ACTIVITY_ID)
        ).fetchone()
        assert route_row is not None
        assert route_row.encoded_polyline != "pretrim"
        assert route_row.start_lat == pytest.approx(37.0 + 50 * 0.0001, abs=1e-6)
        assert route_row.end_lat == pytest.approx(37.0 + 150 * 0.0001, abs=1e-6)


def test_set_activity_trim_clips_laps(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    with engine.connect() as conn:
        _seed_activity(conn, archive_root, parquet_dir / f"{ACTIVITY_ID}.parquet")
        # lap 0: [0,50) -- entirely before the trim window, should be dropped.
        # lap 1: [100,150) -- entirely inside [50,150], recomputed but not dropped.

        set_activity_trim(
            conn,
            con,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=ACTIVITY_ID,
            trim_start_s=50.0,
            trim_end_s=150.0,
        )
        conn.commit()

        laps = conn.execute(
            select(lap).where(lap.c.activity_id == ACTIVITY_ID).order_by(lap.c.lap_index)
        ).fetchall()
        assert [row.lap_index for row in laps] == [1]
        assert laps[0].distance_m == pytest.approx(100.0, abs=1)  # 2 m/s * 50s


def test_apply_activity_trim_overrides_survives_simulated_rebuild(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Simulates what `sync rebuild` does to a trimmed activity: the replay loop re-derives
    `activity`/`lap`/`route_geom`/`activity_metric` fresh from raw bytes (here, just reset back
    to the pre-trim placeholder values `_seed_activity` wrote), then the override-reapplication
    pass should bring the trim right back."""
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    with engine.connect() as conn:
        _seed_activity(conn, archive_root, parquet_dir / f"{ACTIVITY_ID}.parquet")
        set_activity_trim(
            conn,
            con,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=ACTIVITY_ID,
            trim_start_s=50.0,
            trim_end_s=150.0,
        )
        conn.commit()

        # "Rebuild": wipe this one activity's derived fields back to the pre-trim placeholders,
        # as if freshly re-parsed from raw bytes (activity_trim_override itself is untouched,
        # matching how sync rebuild never wipes an override table).
        conn.execute(
            activity.update()
            .where(activity.c.id == ACTIVITY_ID)
            .values(
                duration_s=99999.0,
                distance_m=99999.0,
                elevation_gain_m=999.0,
                max_altitude_m=999.0,
                calories=1234.0,
            )
        )
        conn.commit()

        changed = apply_activity_trim_overrides(
            conn, con, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID
        )
        conn.commit()

        assert changed == 1
        row = conn.execute(select(activity).where(activity.c.id == ACTIVITY_ID)).fetchone()
        assert row is not None
        assert row.distance_m == pytest.approx(200.0, abs=1)
        assert row.max_altitude_m == pytest.approx(115.0, abs=0.5)
        assert row.calories is None


def test_clear_activity_trim_restores_original_values(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    with engine.connect() as conn:
        _seed_activity(conn, archive_root, parquet_dir / f"{ACTIVITY_ID}.parquet")
        set_activity_trim(
            conn,
            con,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=ACTIVITY_ID,
            trim_start_s=50.0,
            trim_end_s=150.0,
        )
        conn.commit()

        clear_activity_trim(
            conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID, activity_id=ACTIVITY_ID
        )
        conn.commit()

        # No override row left to re-apply on a future rebuild.
        override_row = conn.execute(
            select(activity_trim_override).where(
                activity_trim_override.c.athlete_id == DEFAULT_ATHLETE_ID
            )
        ).fetchone()
        assert override_row is None

        row = conn.execute(select(activity).where(activity.c.id == ACTIVITY_ID)).fetchone()
        assert row is not None
        # Ground truth: synthetic_run.fit's own real parsed values (see this file's module
        # docstring / the fixture itself), not anything the trim recompute or the pre-trim
        # placeholder ever produced.
        assert row.duration_s == pytest.approx(600.0)
        assert row.distance_m == pytest.approx(1500.0)
        assert row.elevation_gain_m == pytest.approx(5.0)
        assert row.max_altitude_m == pytest.approx(12.0)
        assert row.calories == pytest.approx(80.0)

        laps = conn.execute(
            select(lap).where(lap.c.activity_id == ACTIVITY_ID).order_by(lap.c.lap_index)
        ).fetchall()
        assert len(laps) == 1
        assert laps[0].distance_m == pytest.approx(750.0)


def test_clear_activity_trim_raises_when_no_trim_active(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    with engine.connect() as conn:
        _seed_activity(conn, archive_root, parquet_dir / f"{ACTIVITY_ID}.parquet")
        conn.commit()

        with pytest.raises(ValueError, match="no active trim"):
            clear_activity_trim(
                conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID, activity_id=ACTIVITY_ID
            )


def test_set_activity_trim_rejects_empty_window(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    with engine.connect() as conn:
        _seed_activity(conn, archive_root, parquet_dir / f"{ACTIVITY_ID}.parquet")
        conn.commit()

        with pytest.raises(ValueError, match="trim_end_s must be after trim_start_s"):
            set_activity_trim(
                conn,
                con,
                parquet_dir,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=ACTIVITY_ID,
                trim_start_s=100.0,
                trim_end_s=50.0,
            )


def test_restore_from_parsed_reinstates_a_metric_a_trim_had_deleted(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Unit-level check of the training-load restoration path specifically, since
    synthetic_run.fit's own real bytes carry no training-load field to exercise this through the
    full reparse path -- constructs a small CanonicalActivity directly instead."""
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    with engine.connect() as conn:
        _seed_activity(conn, archive_root, parquet_dir / f"{ACTIVITY_ID}.parquet")
        set_activity_trim(
            conn,
            con,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=ACTIVITY_ID,
            trim_start_s=50.0,
            trim_end_s=150.0,
        )
        conn.commit()
        # Confirm the trim really did delete it first, so this test can't pass by coincidence.
        assert (
            conn.execute(
                select(activity_metric).where(
                    activity_metric.c.activity_id == ACTIVITY_ID,
                    activity_metric.c.metric_key == "fit.session.training_load_peak",
                )
            ).fetchone()
            is None
        )

        parsed = CanonicalActivity(
            start_time_utc=ACTIVITY_START,
            utc_offset_s=0,
            sport="hiking",
            sub_sport="generic",
            name="Restored",
            duration_s=600.0,
            moving_duration_s=600.0,
            distance_m=1500.0,
            elevation_gain_m=5.0,
            max_altitude_m=None,
            calories=80.0,
            device=None,
            extra_metrics=[ParsedMetric(key="fit.session.training_load_peak", value_num=42.0)],
            laps=[
                ParsedLap(
                    lap_index=0,
                    start_time_utc=ACTIVITY_START,
                    duration_s=600.0,
                    moving_duration_s=600.0,
                    distance_m=1500.0,
                    avg_hr=None,
                    max_hr=None,
                    avg_speed_mps=None,
                )
            ],
        )
        _restore_from_parsed(
            conn, athlete_id=DEFAULT_ATHLETE_ID, activity_id=ACTIVITY_ID, parsed=parsed
        )
        conn.commit()

        tl_row = conn.execute(
            select(activity_metric.c.value_num).where(
                activity_metric.c.activity_id == ACTIVITY_ID,
                activity_metric.c.metric_key == "fit.session.training_load_peak",
            )
        ).fetchone()
        assert tl_row is not None
        assert tl_row.value_num == 42.0
