"""garmin_export tests: nested-directory FIT discovery, filename-based external_id,
last_full_export_at, and idempotent re-import — all using the committed synthetic fixture,
never real personal data.
"""

import datetime as dt
import shutil
import zipfile
from pathlib import Path

from sqlalchemy import Engine, select

from sporthealth.adapters.garmin_export import import_garmin_export
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity_source_link, athlete, metadata, raw_object
from sporthealth.db.seed import DEFAULT_ATHLETE_ID

FIXTURE = Path(__file__).parent.parent / "fixtures" / "fit" / "synthetic_run.fit"


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
    # includes a non-FIT file to exercise raw-only archiving.
    fitness_dir = tmp_path / "src" / "DI_CONNECT" / "DI-Connect-Fitness" / "2024"
    fitness_dir.mkdir(parents=True)
    shutil.copy(FIXTURE, fitness_dir / "55501234_ACTIVITY.fit")

    wellness_dir = tmp_path / "src" / "DI_CONNECT" / "DI-Connect-Wellness"
    wellness_dir.mkdir(parents=True)
    (wellness_dir / "sample.json").write_text('{"fake": "wellness data"}', encoding="utf-8")

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

    assert summary.items_seen == 2  # one .fit, one .json
    assert summary.items_new == 1
    assert summary.errors == []
    assert link == "55501234"  # from the filename, not derived from FIT content
    assert json_raw == "garmin_export_json"  # non-FIT file archived raw, not parsed


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
