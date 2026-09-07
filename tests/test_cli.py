"""Tests for the second-athlete CLI additions: `sync athlete create` (the only command that
actually INSERTs a new `athlete` row -- every other athlete_app command only UPDATEs one that
already exists) and the `--athlete-id` override now threaded through the ingestion commands
(representative coverage via `import garmin-connect`, mirroring `sync rebuild`'s pre-existing
option -- not exhaustive over every backfill/import command).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sqlalchemy import Engine, select

from perseverer.adapters.fit_folder import IngestRunSummary
from perseverer.cli import athlete_create, import_garmin_connect_cmd
from perseverer.config import Settings
from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, metadata
from perseverer.db.seed import DEFAULT_ATHLETE_ID


def _engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Original",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    return engine


def test_athlete_create_inserts_a_new_row_with_a_fresh_id(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    settings = Settings(data_dir=tmp_path)
    with (
        patch("perseverer.cli.get_settings", return_value=settings),
        patch("perseverer.cli.make_engine", return_value=engine),
    ):
        athlete_create(display_name="Erwan", timezone="Europe/Paris", unit_preference="metric")

    with engine.connect() as conn:
        rows = conn.execute(select(athlete.c.id, athlete.c.display_name)).fetchall()
    assert len(rows) == 2
    ids = {r.id for r in rows}
    names = {r.display_name for r in rows}
    assert DEFAULT_ATHLETE_ID in ids
    assert "Erwan" in names
    new_id = next(r.id for r in rows if r.display_name == "Erwan")
    assert new_id != DEFAULT_ATHLETE_ID
    assert len(new_id) == 26  # a real ULID, matching every other id in this schema


def test_athlete_create_twice_creates_two_distinct_rows(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    settings = Settings(data_dir=tmp_path)
    with (
        patch("perseverer.cli.get_settings", return_value=settings),
        patch("perseverer.cli.make_engine", return_value=engine),
    ):
        athlete_create(display_name="Erwan", timezone="UTC", unit_preference="metric")
        athlete_create(display_name="Erwan", timezone="UTC", unit_preference="metric")

    with engine.connect() as conn:
        rows = conn.execute(
            select(athlete.c.id).where(athlete.c.display_name == "Erwan")
        ).fetchall()
    assert len({r.id for r in rows}) == 2


def test_import_garmin_connect_threads_the_athlete_id_flag(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    second_athlete_id = "01SECONDATHLETE0000000000"
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=second_athlete_id,
                display_name="Second",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()

    settings = Settings(data_dir=tmp_path)
    captured: dict[str, Any] = {}

    def fake_sync_garmin_connect(
        conn: Any,
        raw_dir: Any,
        parquet_dir: Any,
        tokenstore_dir: Any,
        *,
        athlete_id: str,
        **kw: Any,
    ) -> IngestRunSummary:
        captured["athlete_id"] = athlete_id
        captured["tokenstore_dir"] = tokenstore_dir
        return IngestRunSummary(run_id=0)

    with (
        patch("perseverer.cli.get_settings", return_value=settings),
        patch("perseverer.cli.make_engine", return_value=engine),
        patch("perseverer.cli.sync_garmin_connect", side_effect=fake_sync_garmin_connect),
    ):
        import_garmin_connect_cmd(athlete_id=second_athlete_id)

    assert captured["athlete_id"] == second_athlete_id
    assert captured["tokenstore_dir"] == settings.garmin_tokenstore_dir_for(second_athlete_id)
