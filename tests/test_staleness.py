"""Staleness escalation threshold, export freshness threshold, and webhook payload shape —
all against a fresh in-memory-style SQLite DB, no real network calls (httpx.post is patched).
"""

import datetime as dt
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, ingest_run, metadata
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.staleness import (
    StalenessAlert,
    check_export_freshness,
    check_garmin_connect_staleness,
    check_staleness,
    notify_webhook,
)


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


def _insert_run(
    engine: Engine, *, status: str, started_at: dt.datetime, finished_at: dt.datetime
) -> None:
    with engine.connect() as conn:
        conn.execute(
            ingest_run.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                source="garmin_connect",
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                items_seen=0,
                items_new=0,
            )
        )
        conn.commit()


def test_no_alert_when_no_runs_have_happened_yet(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        alert = check_garmin_connect_staleness(conn, DEFAULT_ATHLETE_ID, escalate_after_days=7)
    assert alert is None


def test_no_alert_when_the_latest_run_succeeded(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    _insert_run(engine, status="success", started_at=now, finished_at=now)
    with engine.connect() as conn:
        alert = check_garmin_connect_staleness(conn, DEFAULT_ATHLETE_ID, escalate_after_days=7)
    assert alert is None


def test_warning_before_escalation_threshold(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    last_success = now - dt.timedelta(days=3)
    _insert_run(engine, status="success", started_at=last_success, finished_at=last_success)
    _insert_run(engine, status="failed", started_at=now, finished_at=now)

    with engine.connect() as conn:
        alert = check_garmin_connect_staleness(conn, DEFAULT_ATHLETE_ID, escalate_after_days=7)
    assert alert is not None
    assert alert.severity == "warning"


def test_escalates_to_critical_past_the_threshold(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    last_success = now - dt.timedelta(days=10)
    _insert_run(engine, status="success", started_at=last_success, finished_at=last_success)
    _insert_run(engine, status="failed", started_at=now, finished_at=now)

    with engine.connect() as conn:
        alert = check_garmin_connect_staleness(conn, DEFAULT_ATHLETE_ID, escalate_after_days=7)
    assert alert is not None
    assert alert.severity == "critical"


def test_export_freshness_no_alert_when_recent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == DEFAULT_ATHLETE_ID)
            .values(last_full_export_at=dt.datetime.now(dt.UTC).replace(tzinfo=None))
        )
        conn.commit()
        assert check_export_freshness(conn, DEFAULT_ATHLETE_ID, freshness_days=90) is None


def test_export_freshness_warns_when_never_exported(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        alert = check_export_freshness(conn, DEFAULT_ATHLETE_ID, freshness_days=90)
    assert alert is not None
    assert alert.message == "no Garmin export archive has ever been imported"


def test_export_freshness_warns_past_threshold(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    stale_date = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=120)
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == DEFAULT_ATHLETE_ID)
            .values(last_full_export_at=stale_date)
        )
        conn.commit()
        alert = check_export_freshness(conn, DEFAULT_ATHLETE_ID, freshness_days=90)
    assert alert is not None
    assert alert.severity == "warning"


def test_check_staleness_combines_both_checks(tmp_path: Path) -> None:
    engine = _engine(tmp_path)  # no runs, no export -> only the export-freshness alert fires
    with engine.connect() as conn:
        alerts = check_staleness(conn, DEFAULT_ATHLETE_ID, escalate_after_days=7, freshness_days=90)
    assert [a.source for a in alerts] == ["garmin_export"]


def test_notify_webhook_posts_expected_json_shape(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> None:
        captured["url"] = url
        captured["json"] = json

    monkeypatch.setattr("httpx.post", fake_post)

    alert = StalenessAlert(
        source="garmin_connect",
        severity="critical",
        message="failing for 10 days",
        detected_at=dt.datetime(2026, 1, 1),
    )
    notify_webhook("https://example.invalid/webhook", alert)

    assert captured["url"] == "https://example.invalid/webhook"
    assert captured["json"] == {
        "source": "garmin_connect",
        "severity": "critical",
        "message": "failing for 10 days",
        "detected_at": "2026-01-01T00:00:00",
    }


def test_notify_webhook_swallows_connection_errors(monkeypatch: Any) -> None:
    import httpx

    def fake_post(*args: Any, **kwargs: Any) -> None:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("httpx.post", fake_post)

    alert = StalenessAlert(
        source="garmin_connect",
        severity="warning",
        message="x",
        detected_at=dt.datetime.now(dt.UTC),
    )
    notify_webhook("https://example.invalid/webhook", alert)  # must not raise
