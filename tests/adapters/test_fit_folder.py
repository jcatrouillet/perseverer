"""Adapter idempotency and full-archive-rebuild tests, using the committed synthetic fixture
(never real personal FIT files — see tests/fit/test_parser.py's docstring).
"""

import datetime as dt
import json
import shutil
from pathlib import Path

from sqlalchemy import Engine, select

from sporthealth.adapters.fit_folder import _local_date, import_from_folder
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import (
    activity,
    athlete,
    day_rollup,
    health_observation,
    metadata,
    sleep_session,
)
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.rebuild import rebuild_database

FIXTURE = Path(__file__).parent.parent / "fixtures" / "fit" / "synthetic_run.fit"
HEALTH_FIXTURE = Path(__file__).parent.parent / "fixtures" / "fit" / "synthetic_health.fit"

DAILY_SUMMARY_JSON = {
    "calendarDate": "2025-06-01",
    "wellnessEndTimeGmt": "2025-06-02T06:00:00.0",
    "totalSteps": 8421,
}


def _seed_athlete(engine: Engine) -> None:
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


def _setup(tmp_path: Path) -> tuple[Engine, Path]:
    import_dir = tmp_path / "fitsrc"
    import_dir.mkdir()
    shutil.copy(FIXTURE, import_dir / "synthetic_run.fit")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    return engine, import_dir


def test_import_is_idempotent(tmp_path: Path) -> None:
    engine, import_dir = _setup(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"

    with engine.connect() as conn:
        first = import_from_folder(
            conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID, folder=import_dir
        )
        second = import_from_folder(
            conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID, folder=import_dir
        )
        activity_ids = conn.execute(select(activity.c.id)).scalars().all()
        rollup_rows = conn.execute(select(day_rollup)).fetchall()

    # Proves the rollup-refresh wiring end to end (ADR 0006 decision 3), not just in isolation.
    assert len(rollup_rows) == 1
    assert rollup_rows[0].activity_count == 1

    assert first.items_seen == 1
    assert first.items_new == 1
    assert first.errors == []
    assert second.items_new == 0
    assert len(activity_ids) == 1


def test_local_date_is_offset_adjusted_not_raw_utc_date() -> None:
    # 2024-06-01 03:00 UTC, offset -8h -> 2024-05-31 19:00 local: a real evening activity that
    # would otherwise roll into the wrong (next) UTC calendar day. See ADR 0009 decision 8 --
    # this is exactly the bug real Garmin/intervals.icu reconciliation surfaced.
    crossing = dt.datetime(2024, 6, 1, 3, 0, tzinfo=dt.UTC)
    assert _local_date(crossing, -28800) == "2024-05-31"

    # The synthetic_run.fit fixture's own timestamp (08:00 UTC, offset -8h -> local midnight)
    # happens not to cross a boundary -- confirms the no-crossing case is unaffected.
    non_crossing = dt.datetime(2024, 6, 1, 8, 0, tzinfo=dt.UTC)
    assert _local_date(non_crossing, -28800) == "2024-06-01"

    # UTC athletes (utc_offset_s=0, the default for every existing fixture/seed) are
    # unaffected -- offset-adjustment is a no-op.
    assert _local_date(non_crossing, 0) == "2024-06-01"


def test_import_uses_offset_adjusted_local_date_for_rollups(tmp_path: Path) -> None:
    # synthetic_run.fit's start_time_utc (2024-06-01 08:00 UTC) with its baked-in utc_offset_s
    # of -28800 lands on local midnight, so the UTC date and local date happen to coincide --
    # this just confirms the real ingest path calls the offset-adjusted helper and stores its
    # result, not that day-crossing itself round-trips (covered above as a pure function).
    engine, import_dir = _setup(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"

    with engine.connect() as conn:
        import_from_folder(
            conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID, folder=import_dir
        )
        row = conn.execute(
            select(activity.c.local_date, activity.c.utc_offset_s)
        ).one()

    assert row.utc_offset_s == -28800
    assert row.local_date == "2024-06-01"


def test_rebuild_from_archive_after_deleting_the_database(tmp_path: Path) -> None:
    engine, import_dir = _setup(tmp_path)
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"

    with engine.connect() as conn:
        import_from_folder(
            conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID, folder=import_dir
        )
        before = conn.execute(
            select(activity.c.sport, activity.c.distance_m, activity.c.duration_s)
        ).fetchall()
    engine.dispose()  # release the file handle — Windows locks it exclusively otherwise

    # Simulate "delete the database entirely" — never touches archive_root.
    (tmp_path / "db.sqlite").unlink()
    shutil.rmtree(parquet_dir)

    engine2 = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        replayed = rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = conn.execute(
            select(activity.c.sport, activity.c.distance_m, activity.c.duration_s)
        ).fetchall()

    assert replayed == 1
    assert after == before


def test_import_recognizes_health_fit_and_daily_summary_json(tmp_path: Path) -> None:
    engine, import_dir = _setup(tmp_path)
    shutil.copy(HEALTH_FIXTURE, import_dir / "WELLNESS.fit")
    (import_dir / "daily_summary_2025-06-01.json").write_text(
        json.dumps(DAILY_SUMMARY_JSON), encoding="utf-8"
    )
    # A generic .json file must NOT be picked up -- fit_folder's JSON recognition is narrow.
    (import_dir / "notes.json").write_text('{"ignored": true}', encoding="utf-8")

    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    with engine.connect() as conn:
        summary = import_from_folder(
            conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID, folder=import_dir
        )
        sleep_rows = conn.execute(select(sleep_session.c.id)).scalars().all()
        steps_obs = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_summary.totalSteps"
            )
        ).scalar_one()

    assert summary.items_seen == 3  # synthetic_run.fit, WELLNESS.fit, daily_summary_*.json
    assert summary.errors == []
    assert len(sleep_rows) == 1
    assert steps_obs == 8421.0


def test_rebuild_reproduces_health_data_after_deleting_the_database(tmp_path: Path) -> None:
    engine, import_dir = _setup(tmp_path)
    shutil.copy(HEALTH_FIXTURE, import_dir / "WELLNESS.fit")
    (import_dir / "daily_summary_2025-06-01.json").write_text(
        json.dumps(DAILY_SUMMARY_JSON), encoding="utf-8"
    )
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"

    with engine.connect() as conn:
        import_from_folder(
            conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID, folder=import_dir
        )
        before_sleep = conn.execute(select(sleep_session.c.local_date)).fetchall()
        before_obs = conn.execute(
            select(health_observation.c.metric_key, health_observation.c.value_num)
        ).fetchall()
    engine.dispose()

    (tmp_path / "db.sqlite").unlink()
    shutil.rmtree(parquet_dir)

    engine2 = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after_sleep = conn.execute(select(sleep_session.c.local_date)).fetchall()
        after_obs = conn.execute(
            select(health_observation.c.metric_key, health_observation.c.value_num)
        ).fetchall()

    assert before_sleep and after_sleep == before_sleep
    assert before_obs and set(after_obs) == set(before_obs)
