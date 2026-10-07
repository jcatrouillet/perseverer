"""Schema creation and the mandated athlete-scoping invariant.

Every data table must carry athlete_id — this test fails mechanically if a new table is
added without it, rather than relying on code review to catch a tenancy leak.
"""

from pathlib import Path

from sqlalchemy import text

from perseverer.db.engine import make_engine
from perseverer.db.schema import EXEMPT_FROM_ATHLETE_SCOPING, metadata


def test_schema_creates_cleanly(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "test.db")
    metadata.create_all(engine)
    with engine.connect() as conn:
        tables = set(
            conn.execute(text("select name from sqlite_master where type='table'")).scalars()
        )
    assert "activity" in tables
    assert "raw_object" in tables
    assert "metric_definition" in tables


def test_every_data_table_is_athlete_scoped() -> None:
    missing = [
        table.name
        for table in metadata.tables.values()
        if table.name not in EXEMPT_FROM_ATHLETE_SCOPING and "athlete_id" not in table.columns
    ]
    assert missing == [], f"tables missing athlete_id: {missing}"


def test_exempt_tables_are_the_expected_small_set() -> None:
    """Guards the exemption list from silently growing — a new exemption should be a
    deliberate, reviewed decision (docs/ARCHITECTURE.md), not an accident. `auth_login_attempt`
    joined this set deliberately: a login attempt against a
    nonexistent username has no athlete row to attach it to, and must still be counted -- that's
    exactly the case a brute-force attempt usually is. `oauth_client` is a
    dynamically-registered *application* (e.g. claude.ai), which exists before any athlete has
    logged in -- a shared catalog like `metric_definition`, not an athlete's data."""
    assert (
        frozenset({"athlete", "metric_definition", "auth_login_attempt", "oauth_client"})
        == EXEMPT_FROM_ATHLETE_SCOPING
    )
