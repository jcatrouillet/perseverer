"""Mileage accounting and alerts for athlete-owned gear.

Shoes are assigned as the active default for a sport.  Mileage is computed from immutable
activity summaries on read, never copied into a mutable counter: correcting an activity's sport
or distance immediately corrects the pair's total.  Assignment time is the ledger boundary, so
past activities are never guessed onto a newly-created pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Any

from sqlalchemy import Connection, and_, func, select

from perseverer.config import Settings
from perseverer.db.schema import activity, athlete_default_shoe, shoe
from perseverer.email_delivery import send_email


@dataclass(frozen=True)
class ShoeMileage:
    shoe_id: str
    distance_m: float
    default_sports: list[str]


def shoe_mileages(conn: Connection, athlete_id: str) -> dict[str, ShoeMileage]:
    """Mileage from explicit activity choices, falling back to the dated sport default."""
    explicit_rows = conn.execute(
        select(
            activity.c.shoe_id,
            func.coalesce(func.sum(activity.c.distance_m), 0.0).label("distance_m"),
        )
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            activity.c.shoe_id.is_not(None),
        )
        .group_by(activity.c.shoe_id)
    ).fetchall()
    default_rows = conn.execute(
        select(
            athlete_default_shoe.c.shoe_id,
            athlete_default_shoe.c.sport,
            func.coalesce(func.sum(activity.c.distance_m), 0.0).label("distance_m"),
        )
        .select_from(
            athlete_default_shoe.outerjoin(
                activity,
                and_(
                    activity.c.athlete_id == athlete_default_shoe.c.athlete_id,
                    activity.c.sport == athlete_default_shoe.c.sport,
                    activity.c.start_time_utc >= athlete_default_shoe.c.assigned_at,
                    activity.c.deleted_at.is_(None),
                    activity.c.shoe_id.is_(None),
                ),
            )
        )
        .where(athlete_default_shoe.c.athlete_id == athlete_id)
        .group_by(athlete_default_shoe.c.shoe_id, athlete_default_shoe.c.sport)
    ).fetchall()
    by_shoe: dict[str, ShoeMileage] = {
        row.shoe_id: ShoeMileage(row.shoe_id, float(row.distance_m or 0.0), [])
        for row in explicit_rows
    }
    for row in default_rows:
        current = by_shoe.get(row.shoe_id, ShoeMileage(row.shoe_id, 0.0, []))
        by_shoe[row.shoe_id] = ShoeMileage(
            row.shoe_id,
            current.distance_m + float(row.distance_m or 0.0),
            [*current.default_sports, row.sport],
        )
    return by_shoe


def resolve_activity_shoe(
    conn: Connection,
    athlete_id: str,
    sport: str,
    start_time_utc: datetime,
    explicit_shoe_id: str | None,
) -> tuple[str | None, bool]:
    """The shoe that actually applies to one activity: its own explicit choice if set, else the
    athlete's dated sport default when the activity falls on or after that default's own
    `assigned_at` -- the same ledger boundary `shoe_mileages` already enforces in aggregate,
    resolved here for a single activity instead of summed across all of them. Returns
    `(shoe_id, is_default)` so a caller (the activity-detail page's own shoe picker) can show the
    resolved shoe either way -- an athlete shouldn't have to open the picker to discover a default
    is already covering an activity -- while still knowing whether saving as-is would merely
    confirm the existing default or actually write a new explicit choice."""
    if explicit_shoe_id is not None:
        return explicit_shoe_id, False
    row = conn.execute(
        select(athlete_default_shoe.c.shoe_id).where(
            athlete_default_shoe.c.athlete_id == athlete_id,
            athlete_default_shoe.c.sport == sport,
            athlete_default_shoe.c.assigned_at <= start_time_utc,
        )
    ).fetchone()
    return (row.shoe_id, True) if row is not None else (None, False)


def default_shoes(conn: Connection, athlete_id: str) -> dict[str, str]:
    return {
        str(row.sport): str(row.shoe_id)
        for row in conn.execute(
            select(athlete_default_shoe.c.sport, athlete_default_shoe.c.shoe_id).where(
                athlete_default_shoe.c.athlete_id == athlete_id
            )
        ).fetchall()
    }


def over_limit_shoes(conn: Connection, athlete_id: str) -> list[tuple[Any, ShoeMileage]]:
    mileages = shoe_mileages(conn, athlete_id)
    rows = conn.execute(
        select(shoe).where(
            shoe.c.athlete_id == athlete_id,
            shoe.c.retired_at.is_(None),
            shoe.c.max_distance_m.is_not(None),
        )
    ).fetchall()
    return [
        (row, mileages[row.id])
        for row in rows
        if row.id in mileages
        and row.initial_distance_m + mileages[row.id].distance_m >= row.max_distance_m
    ]


def mark_alert_emailed(conn: Connection, shoe_id: str) -> None:
    conn.execute(
        shoe.update()
        .where(shoe.c.id == shoe_id)
        .values(alert_emailed_at=datetime.now(UTC).replace(tzinfo=None))
    )


def send_over_limit_alerts(
    conn: Connection, settings: Settings, athlete_id: str, recipient: str | None
) -> int:
    """Email each newly-over-limit pair once.  The visible app banner remains live separately."""
    if not settings.smtp_configured or not recipient:
        return 0
    sent = 0
    for pair, mileage in over_limit_shoes(conn, athlete_id):
        if pair.alert_emailed_at is not None:
            continue
        used_km = mileage.distance_m / 1000 + pair.initial_distance_m / 1000
        max_km = pair.max_distance_m / 1000
        name = f"{pair.brand} {pair.model}"
        send_email(
            settings,
            to=recipient,
            subject=f"Perseverer: replace {name}",
            text_body=(
                f"{name} has reached {used_km:.0f} km, over its {max_km:.0f} km limit. "
                "Open Gear in Perseverer to add and select a replacement pair."
            ),
            html_body=(
                f"<p><strong>{escape(name)}</strong> has reached "
                f"<strong>{used_km:.0f} km</strong>, "
                f"over its {max_km:.0f} km limit.</p><p>Open Gear in Perseverer to add "
                "and select a replacement pair.</p>"
            ),
        )
        mark_alert_emailed(conn, pair.id)
        conn.commit()
        sent += 1
    return sent
