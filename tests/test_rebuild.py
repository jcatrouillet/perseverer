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
from sporthealth.adapters.strava_export import import_strava_export
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity, athlete, health_observation, metadata
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


_STRAVA_GPX_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="StravaGPX" version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
 <trk>
  <trkseg>
   <trkpt lat="37.6656890" lon="-112.1102740">
    <ele>2082.6</ele><time>2026-04-17T22:17:09Z</time>
   </trkpt>
   <trkpt lat="37.6657130" lon="-112.1102920">
    <ele>2085.0</ele><time>2026-04-17T22:47:09Z</time>
   </trkpt>
  </trkseg>
 </trk>
</gpx>
"""

_STRAVA_CSV_HEADER = (
    "Activity ID,Activity Date,Activity Name,Activity Type,Elapsed Time,Distance,"
    "Moving Time,Elevation Gain,Calories,Relative Effort,Filename"
)


def test_rebuild_replays_strava_export_gpx_and_manual_entry(tmp_path: Path) -> None:
    """Both strava_export_gpx and manual-entry (file-less) rows were previously silently
    dropped by `sync rebuild`: the gpx raw_object kind fell into the catch-all `else:
    continue`, and a manual-entry row has *no* distinguishing raw_object at all -- its
    archived bytes are byte-identical to its own strava_export_csv_row, so
    archive_raw_bytes's content-addressed idempotency never creates a second row for it (see
    rebuild.py, ADR 0013)."""
    root = tmp_path / "strava"
    (root / "activities").mkdir(parents=True)
    (root / "activities" / "999111.gpx").write_bytes(_STRAVA_GPX_BODY)
    rows = [
        "999111,\"Apr 17, 2026, 10:17:09 PM\",Bryce Canyon hike,Hike,1800,4800.5,1800,50,300,10,"
        "activities/999111.gpx",
        "999222,\"Apr 18, 2026, 6:00:00 AM\",Gym session,Weight Training,2400,0,2400,50,300,10,",
    ]
    (root / "activities.csv").write_text(_STRAVA_CSV_HEADER + "\n" + "\n".join(rows) + "\n")

    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    with engine.connect() as conn:
        summary = import_strava_export(
            conn,
            archive_root,
            parquet_dir,
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        before = sorted(
            conn.execute(select(activity.c.sport, activity.c.name)).fetchall()
        )
    assert summary.errors == []
    assert len(before) == 2

    engine2 = make_engine(tmp_path / "db2.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = sorted(conn.execute(select(activity.c.sport, activity.c.name)).fetchall())

    assert after == before
    assert ("hiking", "Bryce Canyon hike") in after
    assert ("training", "Gym session") in after


def test_rebuild_on_a_db_with_existing_insight_rows_does_not_fk_crash(tmp_path: Path) -> None:
    """A real, previously-unknown bug found via a real `sync rebuild` run against the live
    database: `insight.activity_id` is FK-constrained to `activity.id`, but `insight` was
    missing from `_REBUILDABLE_TABLES` (added after that list was last written, in Phase 8) --
    so rebuilding *in place* against a database that already has insight rows (i.e. every real
    rebuild after the first `refresh_insights` call ever ran) FK-crashed on `DELETE FROM
    activity`. Rebuilding into a fresh, empty database never hit this, which is why it wasn't
    caught earlier. See ADR 0013."""
    root = tmp_path / "strava"
    (root / "activities").mkdir(parents=True)
    (root / "activities" / "999111.gpx").write_bytes(_STRAVA_GPX_BODY)
    (root / "activities.csv").write_text(
        _STRAVA_CSV_HEADER + "\n"
        "999111,\"Apr 17, 2026, 10:17:09 PM\",Bryce Canyon hike,Hike,1800,4800.5,1800,50,300,10,"
        "activities/999111.gpx\n"
    )

    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    with engine.connect() as conn:
        # import_strava_export calls refresh_insights internally once touched_dates is
        # non-empty, so this activity already has real insight rows pointing at it -- exactly
        # the state that crashed a real `sync rebuild`.
        import_strava_export(
            conn,
            archive_root,
            parquet_dir,
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        before = sorted(conn.execute(select(activity.c.sport, activity.c.name)).fetchall())

        # Rebuild in place, against the SAME database that already has those insight rows --
        # this is what a real rebuild does, unlike the other tests here which rebuild into a
        # fresh empty database and would never have exercised this FK path.
        rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = sorted(conn.execute(select(activity.c.sport, activity.c.name)).fetchall())

    assert after == before
    assert ("hiking", "Bryce Canyon hike") in after
