"""Adapter idempotency and full-archive-rebuild tests, using the committed synthetic fixture
(never real personal FIT files — see tests/fit/test_parser.py's docstring).
"""

import datetime as dt
import shutil
from pathlib import Path

from sqlalchemy import Engine, select

from sporthealth.adapters.fit_folder import import_from_folder
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity, athlete, metadata
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.rebuild import rebuild_database

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

    assert first.items_seen == 1
    assert first.items_new == 1
    assert first.errors == []
    assert second.items_new == 0
    assert len(activity_ids) == 1


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
