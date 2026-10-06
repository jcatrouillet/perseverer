"""garmin_export tests: nested-directory FIT discovery, nested-zip FIT discovery (real GDPR
export shape -- see docs/adr/0005-phase-2-garmin-export-real-data.md), filename-based
external_id, GDPR export health JSON recognition, last_full_export_at, and idempotent
re-import — all using the committed synthetic fixtures, never real personal data.
"""

import datetime as dt
import json
import shutil
import zipfile
from pathlib import Path

from sqlalchemy import Engine, select

from perseverer.adapters.garmin_export import (
    _derive_export_external_id,
    import_garmin_export,
    report_kind_from_filename,
)
from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity_source_link,
    athlete,
    day_rollup,
    health_observation,
    metadata,
    raw_object,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID

FIXTURE = Path(__file__).parent.parent / "fixtures" / "fit" / "synthetic_run.fit"
HEALTH_FIXTURE = Path(__file__).parent.parent / "fixtures" / "fit" / "synthetic_health.fit"

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
        "sleepEndTimestampGMT": "2023-01-13T14:00:00.0",
        "deepSleepSeconds": 3600,
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


def _make_export_tree(tmp_path: Path) -> Path:
    # Mirrors a real Garmin export's nesting (DI_CONNECT/DI-Connect-Fitness/<year>/...) and
    # includes a non-health-JSON file (DI-Connect-User, not recognized) to exercise raw-only
    # archiving.
    fitness_dir = tmp_path / "src" / "DI_CONNECT" / "DI-Connect-Fitness" / "2024"
    fitness_dir.mkdir(parents=True)
    shutil.copy(FIXTURE, fitness_dir / "55501234_ACTIVITY.fit")

    user_dir = tmp_path / "src" / "DI_CONNECT" / "DI-Connect-User"
    user_dir.mkdir(parents=True)
    (user_dir / "sample.json").write_text('{"fake": "account data"}', encoding="utf-8")

    return tmp_path / "src"


def test_recursive_fit_discovery_and_filename_external_id(tmp_path: Path) -> None:
    root = _make_export_tree(tmp_path)
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        summary = import_garmin_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        link = conn.execute(
            select(activity_source_link.c.external_id).where(
                activity_source_link.c.source == "garmin_export"
            )
        ).scalar_one()
        json_raw = conn.execute(
            select(raw_object.c.kind).where(raw_object.c.kind == "garmin_export_json")
        ).scalar_one_or_none()
        rollup_rows = conn.execute(select(day_rollup)).fetchall()

    assert summary.items_seen == 2  # one .fit, one .json
    assert summary.items_new == 1
    assert summary.errors == []
    assert link == "55501234"  # from the filename, not derived from FIT content
    assert json_raw == "garmin_export_json"  # DI-Connect-User: not recognized, archived raw
    # Proves the rollup-refresh wiring end to end (ADR 0006 decision 3), not just in isolation.
    assert len(rollup_rows) == 1
    assert rollup_rows[0].activity_count == 1


def test_nested_zip_fit_is_discovered_and_ingested(tmp_path: Path) -> None:
    """Real GDPR exports bury FIT files one zip-level deeper (see ADR 0005) -- confirm the
    recursive extraction pre-pass finds them and routes them through the unified dispatch,
    deriving external_id from the real "<email>_<id>.fit" naming."""
    uploaded_dir = tmp_path / "src" / "DI_CONNECT" / "DI-Connect-Uploaded-Files"
    uploaded_dir.mkdir(parents=True)
    nested_zip = uploaded_dir / "UploadedFiles_0-_Part1.zip"
    with zipfile.ZipFile(nested_zip, "w") as zf:
        zf.write(HEALTH_FIXTURE, "someone@example.com_999888777.fit")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        summary = import_garmin_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=tmp_path / "src",
        )
        sleep_rows = conn.execute(select(health_observation.c.metric_key)).fetchall()

    assert summary.errors == []
    assert summary.items_new >= 1
    assert len(sleep_rows) > 0  # the nested health FIT's fields made it into health_observation


def test_export_health_json_is_parsed_into_observations(tmp_path: Path) -> None:
    """DI-Connect-Wellness JSON (the real GDPR export's day-record-array shape) is parsed via
    the generic export JSON parser, namespaced by the report kind derived from the filename."""
    wellness_dir = tmp_path / "src" / "DI_CONNECT" / "DI-Connect-Wellness"
    wellness_dir.mkdir(parents=True)
    (wellness_dir / "2023-01-12_2023-04-22_87061520_sleepData.json").write_text(
        json.dumps(SLEEP_DATA_RECORDS), encoding="utf-8"
    )

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        summary = import_garmin_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=tmp_path / "src",
        )
        obs = conn.execute(
            select(health_observation.c.metric_key, health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.export.sleepData.deepSleepSeconds"
            )
        ).fetchall()
        raw_kind = conn.execute(
            select(raw_object.c.kind).where(raw_object.c.kind == "garmin_export_health_json")
        ).scalar_one_or_none()

    assert summary.errors == []
    assert {value for _, value in obs} == {4320.0, 3600.0}
    assert raw_kind == "garmin_export_health_json"  # still archived raw, per raw-first


def test_derive_export_external_id_tries_both_patterns_then_falls_back() -> None:
    assert _derive_export_external_id("55501234_ACTIVITY.fit") == "55501234"
    assert _derive_export_external_id("someone@example.com_999888777.fit") == "999888777"
    assert _derive_export_external_id("someone@example.com_LhaBackup.fit") is None


def testreport_kind_from_filename() -> None:
    assert report_kind_from_filename("2023-01-12_2023-04-22_87061520_sleepData.json") == (
        "sleepData"
    )
    assert report_kind_from_filename("UDSFile_2022-10-03_2023-01-11.json") == "UDSFile"
    assert (
        report_kind_from_filename("ActivityVo2Max_20241204_20250314_87061520.json")
        == "ActivityVo2Max"
    )
    assert (
        report_kind_from_filename("TrainingReadinessDTO_20241204_20250314_87061520.json")
        == "TrainingReadinessDTO"
    )


def test_last_full_export_at_is_set_on_success(tmp_path: Path) -> None:
    root = _make_export_tree(tmp_path)
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        before = conn.execute(select(athlete.c.last_full_export_at)).scalar_one()
        assert before is None

        import_garmin_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        after = conn.execute(select(athlete.c.last_full_export_at)).scalar_one()
        assert after is not None


def test_reimporting_an_overlapping_zip_is_a_clean_noop(tmp_path: Path) -> None:
    root = _make_export_tree(tmp_path)
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in root.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(root))

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        first = import_garmin_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        second = import_garmin_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=zip_path,  # same content, delivered as a zip this time
        )

    assert first.items_new == 1
    assert second.items_new == 0
    assert second.errors == []
