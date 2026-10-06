"""Export freshness and ingest staleness — "a broken adapter is a data-loss clock, not an
inconvenience" (project spec §2). Two independent checks, both surfaced as a generic outbound
webhook (a WhatsApp gateway or similar consumes the JSON body — kept deliberately generic).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import Connection, desc, select

from perseverer.db.schema import athlete, ingest_run

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StalenessAlert:
    source: str  # "garmin_connect" | "garmin_export"
    severity: str  # "warning" | "critical"
    message: str
    detected_at: datetime


def check_garmin_connect_staleness(
    conn: Connection, athlete_id: str, *, escalate_after_days: int
) -> StalenessAlert | None:
    """None if the most recent garmin_connect run succeeded (or none has run yet — that's not
    "stale", just not started). Otherwise a warning, escalated to critical once it's been
    failing for more than `escalate_after_days`.
    """
    latest = conn.execute(
        select(ingest_run.c.status)
        .where(ingest_run.c.athlete_id == athlete_id, ingest_run.c.source == "garmin_connect")
        .order_by(desc(ingest_run.c.started_at))
        .limit(1)
    ).fetchone()
    if latest is None or latest.status == "success":
        return None

    last_success_at = conn.execute(
        select(ingest_run.c.finished_at)
        .where(
            ingest_run.c.athlete_id == athlete_id,
            ingest_run.c.source == "garmin_connect",
            ingest_run.c.status == "success",
        )
        .order_by(desc(ingest_run.c.finished_at))
        .limit(1)
    ).scalar_one_or_none()

    now = datetime.now(UTC).replace(tzinfo=None)
    if last_success_at is None:
        return StalenessAlert(
            source="garmin_connect",
            severity="warning",
            message="garmin_connect has never completed a successful sync",
            detected_at=now,
        )

    days_failing = (now - last_success_at).days
    severity = "critical" if days_failing > escalate_after_days else "warning"
    return StalenessAlert(
        source="garmin_connect",
        severity=severity,
        message=f"garmin_connect sync has been failing for {days_failing} day(s)",
        detected_at=now,
    )


def check_export_freshness(
    conn: Connection, athlete_id: str, *, freshness_days: int
) -> StalenessAlert | None:
    """An alert when the last full Garmin export is missing or older than `freshness_days`."""
    last_export_at = conn.execute(
        select(athlete.c.last_full_export_at).where(athlete.c.id == athlete_id)
    ).scalar_one_or_none()

    now = datetime.now(UTC).replace(tzinfo=None)
    if last_export_at is None:
        return StalenessAlert(
            source="garmin_export",
            severity="warning",
            message="no Garmin export archive has ever been imported",
            detected_at=now,
        )

    days_since = (now - last_export_at).days
    if days_since <= freshness_days:
        return None
    return StalenessAlert(
        source="garmin_export",
        severity="warning",
        message=f"last full Garmin export was {days_since} day(s) ago "
        f"(nag threshold: {freshness_days})",
        detected_at=now,
    )


def check_staleness(
    conn: Connection, athlete_id: str, *, escalate_after_days: int, freshness_days: int
) -> list[StalenessAlert]:
    """Every staleness alert currently raised for the athlete."""
    alerts = [
        check_garmin_connect_staleness(conn, athlete_id, escalate_after_days=escalate_after_days),
        check_export_freshness(conn, athlete_id, freshness_days=freshness_days),
    ]
    return [a for a in alerts if a is not None]


def notify_webhook(url: str, alert: StalenessAlert) -> None:
    """Best-effort: a failed webhook POST must never crash the worker process itself."""
    try:
        httpx.post(
            url,
            json={
                "source": alert.source,
                "severity": alert.severity,
                "message": alert.message,
                "detected_at": alert.detected_at.isoformat(),
            },
            timeout=10.0,
        )
    except httpx.HTTPError:
        logger.exception("Failed to deliver staleness webhook for %s", alert.source)
