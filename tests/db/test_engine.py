"""make_engine's sqlite pragmas -- journal_mode=WAL, foreign_keys=ON, and busy_timeout (the
last found missing live: two genuinely concurrent write transactions, from the Settings page's
"Sync now" and "Rebuild" triggers -- both real background writers, see api/routers/settings.py
-- failed immediately with "database is locked" instead of one briefly waiting for the other,
since WAL only allows one writer at a time and there was no timeout configured for that wait.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, metadata


def test_pragmas_are_set_on_every_connection(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "db.sqlite")
    with engine.connect() as conn:
        journal_mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        foreign_keys = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
        busy_timeout = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()

    assert journal_mode == "wal"
    assert foreign_keys == 1
    assert busy_timeout == 10_000


def test_a_concurrent_writer_waits_instead_of_failing_immediately(tmp_path: Path) -> None:
    """The regression this pragma fixes: without busy_timeout, this test reliably raised
    `sqlite3.OperationalError: database is locked` instead of succeeding once the first writer
    committed."""
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)

    holder_ready = threading.Event()
    release_holder = threading.Event()
    second_writer_result: list[BaseException | None] = []

    def hold_a_write_transaction() -> None:
        with engine.connect() as conn:
            conn.execute(
                athlete.insert().values(
                    id="holder",
                    display_name="Holder",
                    timezone="UTC",
                    unit_preference="metric",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
            holder_ready.set()
            release_holder.wait(timeout=5)
            conn.commit()

    def attempt_a_second_write() -> None:
        holder_ready.wait(timeout=5)
        try:
            with engine.connect() as conn:
                conn.execute(
                    athlete.insert().values(
                        id="second",
                        display_name="Second",
                        timezone="UTC",
                        unit_preference="metric",
                        created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    )
                )
                conn.commit()
            second_writer_result.append(None)
        except BaseException as e:
            second_writer_result.append(e)

    holder = threading.Thread(target=hold_a_write_transaction)
    second = threading.Thread(target=attempt_a_second_write)
    holder.start()
    holder_ready.wait(timeout=5)
    second.start()
    time.sleep(0.2)  # give the second writer a moment to actually hit the lock and start waiting
    release_holder.set()
    holder.join(timeout=5)
    second.join(timeout=5)

    assert second_writer_result == [None]  # waited, then succeeded -- not "database is locked"
