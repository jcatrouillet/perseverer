"""Tests for ingest_dispatch.ingest_fit_bytes: the unified per-.fit-file entry point that
`fit_folder`, `garmin_export`, and `rebuild` all route through (see docs/adr/0004).

Uses the same committed synthetic fixtures as fit/test_parser.py and health/test_fit_parser.py
-- no real personal data.
"""

import datetime as dt
from pathlib import Path

from sqlalchemy import Engine, select

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, athlete, health_observation, metadata, sleep_session
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.ingest_dispatch import ingest_fit_bytes

FIXTURES = Path(__file__).parent / "fixtures" / "fit"
ACTIVITY_FIT = (FIXTURES / "synthetic_run.fit").read_bytes()
HEALTH_FIT = (FIXTURES / "synthetic_health.fit").read_bytes()
EMPTY_FIT = (FIXTURES / "synthetic_health_empty.fit").read_bytes()


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


def test_activity_fit_routes_to_activity_ingest_only(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        result = ingest_fit_bytes(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            content=ACTIVITY_FIT,
        )
        conn.commit()
        activity_rows = conn.execute(select(activity.c.id)).scalars().all()
        health_rows = conn.execute(select(health_observation.c.id)).scalars().all()

    assert result.activity_result is not None
    assert result.health_result is None
    assert result.created is True
    assert len(activity_rows) == 1
    assert len(health_rows) == 0


def test_health_fit_routes_to_health_ingest_only(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        result = ingest_fit_bytes(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            content=HEALTH_FIT,
        )
        conn.commit()
        activity_rows = conn.execute(select(activity.c.id)).scalars().all()
        health_rows = conn.execute(select(health_observation.c.id)).scalars().all()
        sleep_rows = conn.execute(select(sleep_session.c.id)).scalars().all()

    assert result.activity_result is None
    assert result.health_result is not None
    assert result.created is True
    assert len(activity_rows) == 0
    assert len(health_rows) > 0
    assert len(sleep_rows) == 1


def test_neither_path_double_processes_the_other(tmp_path: Path) -> None:
    """Feeding both kinds into the same run must not cross-contaminate: the activity file
    creates exactly one activity and zero health rows, and vice versa."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        ingest_fit_bytes(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            content=ACTIVITY_FIT,
        )
        ingest_fit_bytes(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            content=HEALTH_FIT,
        )
        conn.commit()
        activity_rows = conn.execute(select(activity.c.id)).scalars().all()
        sleep_rows = conn.execute(select(sleep_session.c.id)).scalars().all()

    assert len(activity_rows) == 1
    assert len(sleep_rows) == 1


def test_truly_unrecognized_fit_is_archived_but_not_further_ingested(tmp_path: Path) -> None:
    """A .fit file that's neither an activity nor carries any modeled health message (just a
    bare file_id) is still archived (raw-first holds regardless) but produces no
    activity/health ingest result -- a genuine no-op, not an error."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        result = ingest_fit_bytes(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            content=EMPTY_FIT,
        )
        conn.commit()

    assert result.raw_object_id is not None
    assert result.activity_result is None
    assert result.health_result is None
    assert result.created is False


def test_reingesting_the_same_health_fit_is_idempotent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        first = ingest_fit_bytes(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            content=HEALTH_FIT,
        )
        conn.commit()
        second = ingest_fit_bytes(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            content=HEALTH_FIT,
        )
        conn.commit()
        health_rows = conn.execute(select(health_observation.c.id)).scalars().all()
        sleep_rows = conn.execute(select(sleep_session.c.id)).scalars().all()

    assert first.raw_object_id == second.raw_object_id
    assert second.created is False
    assert len(sleep_rows) == 1  # not duplicated
    assert len(health_rows) > 0
