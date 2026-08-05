"""Tests for rebuild.py's garmin_export_health_json replay (Phase 6, ADR 0009) -- this raw
object kind was previously silently skipped by `sync rebuild` (fell through to the catch-all
`else: continue`), which would have dropped every garmin.export.* health observation on any
rebuild. See docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import Engine, select

from sporthealth.adapters.garmin_export import import_garmin_export
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import athlete, health_observation, metadata
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.rebuild import rebuild_database

SLEEP_DATA_RECORDS = [
    {
        "userProfilePK": 87061520,
        "calendarDate": "2023-01-12",
        "sleepEndTimestampGMT": "2023-01-12T15:31:00.0",
        "deepSleepSeconds": 4320,
        "sleepScores": {"overallScore": 57},
    },
    {
        "userProfilePK": 87061520,
        "calendarDate": "2023-01-13",
        "sleepEndTimestampGMT": "2023-01-13T14:02:00.0",
        "deepSleepSeconds": 3600,
        "sleepScores": {"overallScore": 61},
    },
]


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


def test_rebuild_replays_garmin_export_health_json(tmp_path: Path) -> None:
    wellness_dir = tmp_path / "src" / "DI_CONNECT" / "DI-Connect-Wellness"
    wellness_dir.mkdir(parents=True)
    (wellness_dir / "2023-01-12_2023-04-22_87061520_sleepData.json").write_text(
        json.dumps(SLEEP_DATA_RECORDS), encoding="utf-8"
    )

    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    with engine.connect() as conn:
        summary = import_garmin_export(
            conn,
            archive_root,
            parquet_dir,
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=tmp_path / "src",
        )
        before = conn.execute(
            select(health_observation.c.metric_key, health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.export.sleepData.deepSleepSeconds"
            )
        ).fetchall()
    assert summary.errors == []
    assert {value for _, value in before} == {4320.0, 3600.0}

    # Rebuild into a fresh database, purely from the raw archive -- proves the raw-first
    # invariant for this raw_object kind specifically (previously it silently dropped here).
    engine2 = make_engine(tmp_path / "db2.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        replayed = rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = conn.execute(
            select(health_observation.c.metric_key, health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.export.sleepData.deepSleepSeconds"
            )
        ).fetchall()

    assert replayed == 1
    assert set(after) == set(before)
