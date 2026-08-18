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
from sporthealth.archive import archive_raw_bytes
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity, athlete, health_observation, metadata, sleep_session
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.health.eufy_parser import parse_eufy_scale_reading
from sporthealth.health.ingest import ingest_health_batch
from sporthealth.health.json_parser import (
    parse_daily_hrv_json,
    parse_daily_race_predictions_json,
    parse_daily_sleep_json,
    parse_daily_summary_json,
    parse_daily_training_readiness_json,
    parse_daily_training_status_json,
    parse_hydration_json,
)
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


def test_rebuild_replays_garmin_connect_daily_summary_json(tmp_path: Path) -> None:
    """The live-fetched wellness JSON garmin_connect.py now archives (same shape as
    fit_folder's own daily_summary_json, different provenance) must survive a rebuild the same
    way -- reuses parse_daily_summary_json, no new parser."""
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    content = json.dumps({"calendarDate": "2026-08-05", "restingHeartRate": 46}).encode("utf-8")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    with engine.connect() as conn:
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            kind="garmin_connect_daily_summary_json",
            content=content,
            locator="daily-summary/2026-08-05",
        )
        ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            batch=parse_daily_summary_json(content),
        )
        conn.commit()
        before = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_summary.restingHeartRate"
            )
        ).scalar_one()

    engine2 = make_engine(tmp_path / "db2.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        replayed = rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_summary.restingHeartRate"
            )
        ).scalar_one()

    assert replayed == 1
    assert after == before == 46.0


def test_rebuild_replays_garmin_connect_daily_sleep_json(tmp_path: Path) -> None:
    """This raw object kind was missing from rebuild.py entirely until now -- exactly the same
    class of bug the garmin_export_health_json test above already caught for a different kind
    (see docs/adr/0009-...): a `sync rebuild` would have silently dropped every sleep_session
    row garmin_connect.py's live sleep fetch ever produced."""
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    content = json.dumps(
        {
            "dailySleepDTO": {
                "calendarDate": "2026-08-05",
                "sleepStartTimestampGMT": 1000,
                "sleepEndTimestampGMT": 1000 + 8 * 3600 * 1000,
                "sleepTimeSeconds": 8 * 3600,
                "sleepScores": {"overall": {"value": 80}},
            },
            "sleepLevels": [],
        }
    ).encode("utf-8")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    with engine.connect() as conn:
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            kind="garmin_connect_daily_sleep_json",
            content=content,
            locator="daily-sleep/2026-08-05",
        )
        ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            batch=parse_daily_sleep_json(content),
        )
        conn.commit()
        before = conn.execute(
            select(sleep_session.c.total_sleep_s, sleep_session.c.sleep_score).where(
                sleep_session.c.source == "garmin_connect",
                sleep_session.c.local_date == "2026-08-05",
            )
        ).one()

    engine2 = make_engine(tmp_path / "db2.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        replayed = rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = conn.execute(
            select(sleep_session.c.total_sleep_s, sleep_session.c.sleep_score).where(
                sleep_session.c.source == "garmin_connect",
                sleep_session.c.local_date == "2026-08-05",
            )
        ).one()

    assert replayed == 1
    assert after == before == (8 * 3600, 80.0)


def test_rebuild_replays_garmin_connect_daily_hrv_json(tmp_path: Path) -> None:
    """Same class of bug as the daily-sleep test above -- this raw object kind was missing from
    rebuild.py entirely until now, which would have silently dropped every
    garmin.daily_hrv.* observation garmin_connect.py's live HRV fetch ever produced."""
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    content = json.dumps(
        {
            "hrvSummary": {
                "calendarDate": "2026-08-05",
                "weeklyAvg": 41,
                "lastNightAvg": 37,
                "status": "BALANCED",
            }
        }
    ).encode("utf-8")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    with engine.connect() as conn:
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            kind="garmin_connect_daily_hrv_json",
            content=content,
            locator="daily-hrv/2026-08-05",
        )
        ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            batch=parse_daily_hrv_json(content),
        )
        conn.commit()
        before = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_hrv.lastNightAvg",
                health_observation.c.local_date == "2026-08-05",
            )
        ).scalar_one()

    engine2 = make_engine(tmp_path / "db2.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        replayed = rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_hrv.lastNightAvg",
                health_observation.c.local_date == "2026-08-05",
            )
        ).scalar_one()

    assert replayed == 1
    assert after == before == 37.0


def test_rebuild_replays_garmin_connect_daily_training_readiness_json(tmp_path: Path) -> None:
    """Same class of bug as the daily-sleep/HRV tests above -- this raw object kind was missing
    from rebuild.py entirely until now, which would have silently dropped every
    garmin.daily_training_readiness.* observation garmin_connect.py's live readiness fetch ever
    produced."""
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    content = json.dumps(
        [{"calendarDate": "2026-08-05", "timestamp": "2026-08-05T08:00:00.0", "score": 62}]
    ).encode("utf-8")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    with engine.connect() as conn:
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            kind="garmin_connect_daily_training_readiness_json",
            content=content,
            locator="daily-training-readiness/2026-08-05",
        )
        ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            batch=parse_daily_training_readiness_json(content),
        )
        conn.commit()
        before = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_training_readiness.score",
                health_observation.c.local_date == "2026-08-05",
            )
        ).scalar_one()

    engine2 = make_engine(tmp_path / "db2.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        replayed = rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_training_readiness.score",
                health_observation.c.local_date == "2026-08-05",
            )
        ).scalar_one()

    assert replayed == 1
    assert after == before == 62.0


def test_rebuild_replays_garmin_connect_daily_training_status_json(tmp_path: Path) -> None:
    """Same class of bug -- this raw object kind was missing from rebuild.py entirely until
    now, which would have silently dropped every garmin.daily_vo2max.*/
    garmin.daily_heat_altitude.*/garmin.daily_training_status.* observation garmin_connect.py's
    live training-status fetch ever produced."""
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    content = json.dumps(
        {
            "mostRecentVO2Max": {
                "generic": {"calendarDate": "2026-08-05", "vo2MaxValue": 48.0},
            },
        }
    ).encode("utf-8")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    with engine.connect() as conn:
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            kind="garmin_connect_daily_training_status_json",
            content=content,
            locator="daily-training-status/2026-08-05",
        )
        ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            batch=parse_daily_training_status_json(content),
        )
        conn.commit()
        before = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_vo2max.vo2MaxValue",
                health_observation.c.local_date == "2026-08-05",
            )
        ).scalar_one()

    engine2 = make_engine(tmp_path / "db2.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        replayed = rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_vo2max.vo2MaxValue",
                health_observation.c.local_date == "2026-08-05",
            )
        ).scalar_one()

    assert replayed == 1
    assert after == before == 48.0


def test_rebuild_replays_garmin_connect_daily_hydration_json(tmp_path: Path) -> None:
    """This provenance of hydration_json was missing from rebuild.py's dispatch until now --
    the fit_folder-sourced `hydration_json` kind was already handled, but the live
    garmin_connect-sourced kind was not, which would have silently dropped every
    garmin.hydration.* observation the live sync's own hydration fetch ever produced."""
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    content = json.dumps(
        {"calendarDate": "2026-08-05", "valueInML": 1500.0, "goalInML": 2000.0}
    ).encode("utf-8")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    with engine.connect() as conn:
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            kind="garmin_connect_daily_hydration_json",
            content=content,
            locator="daily-hydration/2026-08-05",
        )
        ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            batch=parse_hydration_json(content),
        )
        conn.commit()
        before = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.hydration.valueInML",
                health_observation.c.local_date == "2026-08-05",
            )
        ).scalar_one()

    engine2 = make_engine(tmp_path / "db2.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        replayed = rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.hydration.valueInML",
                health_observation.c.local_date == "2026-08-05",
            )
        ).scalar_one()

    assert replayed == 1
    assert after == before == 1500.0


def test_rebuild_replays_garmin_connect_race_predictions_json(tmp_path: Path) -> None:
    """This raw object kind was missing from rebuild.py entirely until now, which would have
    silently dropped every garmin.daily_race_predictions.* observation garmin_connect.py's live
    race-predictions fetch ever produced."""
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    content = json.dumps([{"calendarDate": "2026-08-05", "time5K": 1320}]).encode("utf-8")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    with engine.connect() as conn:
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            kind="garmin_connect_race_predictions_json",
            content=content,
            locator="race-predictions/2026-08-01_2026-08-05",
        )
        ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            batch=parse_daily_race_predictions_json(content),
        )
        conn.commit()
        before = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_race_predictions.time5K",
                health_observation.c.local_date == "2026-08-05",
            )
        ).scalar_one()

    engine2 = make_engine(tmp_path / "db2.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        replayed = rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_race_predictions.time5K",
                health_observation.c.local_date == "2026-08-05",
            )
        ).scalar_one()

    assert replayed == 1
    assert after == before == 1320.0


def test_rebuild_replays_eufy_scale_reading_json(tmp_path: Path) -> None:
    """A eufy_scale_reading_json raw object (see adapters/eufy.py) must survive a rebuild the
    same way every other health JSON kind does -- reuses parse_eufy_scale_reading."""
    archive_root = tmp_path / "archive"
    parquet_dir = tmp_path / "parquet"
    content = json.dumps(
        {
            "id": "record-1",
            "device_id": "dev",
            "user_id": "user",
            "customer_id": "cust",
            "group_id": "",
            "create_time": 1717200000,
            "scale_data": {"weight": 772, "bmi": 23.1},
            "status": 0,
        }
    ).encode("utf-8")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    with engine.connect() as conn:
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="eufy",
            kind="eufy_scale_reading_json",
            content=content,
            locator="reading/record-1",
            external_id="record-1",
        )
        ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="eufy",
            batch=parse_eufy_scale_reading(content),
        )
        conn.commit()
        before = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "eufy.scale.weight"
            )
        ).scalar_one()

    engine2 = make_engine(tmp_path / "db2.sqlite")
    metadata.create_all(engine2)
    _seed_athlete(engine2)
    with engine2.connect() as conn:
        replayed = rebuild_database(conn, archive_root, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
        after = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "eufy.scale.weight"
            )
        ).scalar_one()

    assert replayed == 1
    assert after == before == 77.2


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
