"""Tests for adapters/apple_health_export.py against a small synthetic export.xml fixture (no
real export data -- see tests/health/test_apple_health_parser.py for the record shapes)."""

import datetime as dt
import zipfile
from pathlib import Path

from sqlalchemy import Engine, select

from perseverer.adapters.apple_health_export import (
    detect_weight_cutoff_from_eufy,
    import_apple_health_export,
)
from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, health_observation, ingest_run, metadata, raw_object
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.metrics.registry import get_or_register_metric

CUTOFF = "2020-11-12"

FIXTURE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="en_US">
<Record type="HKQuantityTypeIdentifierBodyMass" sourceName="SmarTrack" sourceVersion="045"
 unit="kg" creationDate="2020-09-25 08:12:00 -0700" startDate="2020-09-25 08:12:00 -0700"
 endDate="2020-09-25 08:12:00 -0700" value="83.9"/>
<Record type="HKQuantityTypeIdentifierBodyMass" sourceName="eufy Life" sourceVersion="316"
 unit="kg" creationDate="2020-11-11 19:32:09 -0700" startDate="2020-11-11 19:32:09 -0700"
 endDate="2020-11-11 19:32:09 -0700" value="83.8"/>
<Record type="HKQuantityTypeIdentifierBodyMass" sourceName="SmarTrack" sourceVersion="045"
 unit="kg" creationDate="2021-01-01 08:00:00 -0700" startDate="2021-01-01 08:00:00 -0700"
 endDate="2021-01-01 08:00:00 -0700" value="80.0"/>
<Record type="HKQuantityTypeIdentifierBloodPressureSystolic" sourceName="Health"
 sourceVersion="18.5" unit="mmHg" creationDate="2025-06-15 23:02:49 -0700"
 startDate="2025-06-15 23:02:00 -0700" endDate="2025-06-15 23:02:00 -0700" value="124"/>
<Record type="HKQuantityTypeIdentifierBloodPressureDiastolic" sourceName="Health"
 sourceVersion="18.5" unit="mmHg" creationDate="2025-06-15 23:02:49 -0700"
 startDate="2025-06-15 23:02:00 -0700" endDate="2025-06-15 23:02:00 -0700" value="66"/>
</HealthData>
"""


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


def _make_db(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    return engine


def test_full_import_archives_and_ingests(tmp_path: Path) -> None:
    xml_path = tmp_path / "export.xml"
    xml_path.write_bytes(FIXTURE_XML)
    engine = _make_db(tmp_path)

    with engine.connect() as conn:
        summary = import_apple_health_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=xml_path,
            weight_before=CUTOFF,
        )

    # 5 records in the fixture, one excluded (eufy Life), one excluded (post-cutoff) -> 3 seen/new.
    assert summary.items_seen == 3
    assert summary.items_new == 3
    assert summary.errors == []

    with engine.connect() as conn:
        raw_rows = conn.execute(select(raw_object)).fetchall()
        assert len(raw_rows) == 1
        assert raw_rows[0].kind == "apple_health_export_xml"

        obs = conn.execute(select(health_observation)).fetchall()
        keys = {o.metric_key for o in obs}
        assert keys == {
            "apple_health.body_mass",
            "apple_health.blood_pressure_systolic",
            "apple_health.blood_pressure_diastolic",
        }

        run = conn.execute(
            select(ingest_run).where(ingest_run.c.source == "apple_health_export")
        ).fetchone()
        assert run is not None
        assert run.status == "success"


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    xml_path = tmp_path / "export.xml"
    xml_path.write_bytes(FIXTURE_XML)
    engine = _make_db(tmp_path)

    with engine.connect() as conn:
        import_apple_health_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=xml_path,
            weight_before=CUTOFF,
        )

    with engine.connect() as conn:
        summary2 = import_apple_health_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=xml_path,
            weight_before=CUTOFF,
        )

    assert summary2.items_seen == 3
    assert summary2.items_new == 0  # nothing new second time around

    with engine.connect() as conn:
        raw_rows = conn.execute(select(raw_object)).fetchall()
        assert len(raw_rows) == 1  # content-addressed, not duplicated

        obs = conn.execute(select(health_observation)).fetchall()
        assert len(obs) == 3  # upserted, not duplicated


def test_accepts_zip_input_matching_the_real_export_layout(tmp_path: Path) -> None:
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("apple_health_export/export.xml", FIXTURE_XML)
    engine = _make_db(tmp_path)

    with engine.connect() as conn:
        summary = import_apple_health_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=zip_path,
            weight_before=CUTOFF,
        )

    assert summary.items_seen == 3
    assert summary.errors == []


def test_weight_before_none_still_imports_blood_pressure(tmp_path: Path) -> None:
    xml_path = tmp_path / "export.xml"
    xml_path.write_bytes(FIXTURE_XML)
    engine = _make_db(tmp_path)

    with engine.connect() as conn:
        summary = import_apple_health_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=xml_path,
            weight_before=None,
        )

    assert summary.items_seen == 2  # only the two BP records
    with engine.connect() as conn:
        obs = conn.execute(select(health_observation)).fetchall()
        keys = {o.metric_key for o in obs}
        assert keys == {
            "apple_health.blood_pressure_systolic",
            "apple_health.blood_pressure_diastolic",
        }


def test_detect_weight_cutoff_from_eufy_returns_none_when_no_eufy_data(tmp_path: Path) -> None:
    engine = _make_db(tmp_path)
    with engine.connect() as conn:
        assert detect_weight_cutoff_from_eufy(conn, DEFAULT_ATHLETE_ID) is None


def test_detect_weight_cutoff_from_eufy_returns_earliest_local_date(tmp_path: Path) -> None:
    engine = _make_db(tmp_path)
    with engine.connect() as conn:
        get_or_register_metric(
            conn, metric_key="eufy.scale.weight", source="eufy", category="health"
        )
        conn.execute(
            health_observation.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                metric_key="eufy.scale.weight",
                observed_at_utc=dt.datetime(2020, 11, 12, 2, 32, 9),
                local_date="2020-11-12",
                aggregation="instant",
                value_num=83.8,
                unit="kg",
                source="eufy",
            )
        )
        conn.execute(
            health_observation.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                metric_key="eufy.scale.weight",
                observed_at_utc=dt.datetime(2021, 1, 1, 8, 0, 0),
                local_date="2021-01-01",
                aggregation="instant",
                value_num=80.0,
                unit="kg",
                source="eufy",
            )
        )
        conn.commit()
        assert detect_weight_cutoff_from_eufy(conn, DEFAULT_ATHLETE_ID) == "2020-11-12"
