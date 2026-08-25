"""SQLite engine creation: WAL mode + foreign keys on, both off by default in SQLite."""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event

# WAL still only allows one writer at a time (it just stops that writer from blocking readers) --
# without a busy_timeout, two genuinely concurrent write transactions fail immediately with
# "database is locked" rather than one briefly waiting for the other. Found live: triggering
# Settings-page "Sync now" and "Rebuild" close together (both real background writers, see
# api/routers/settings.py) reliably reproduced this once the Settings page made overlapping
# background jobs a first-class, easily-triggered scenario for the first time -- previously
# every sync/rebuild path was CLI-only and inherently sequential (one command at a time, by
# hand). 10s comfortably covers this app's own write transactions (none are long enough to
# need more) without masking a genuinely stuck lock as a hang.
_BUSY_TIMEOUT_MS = 10_000


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        cursor.close()

    return engine
