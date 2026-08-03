"""Auto-registering metric catalog. Every field a parser sees gets a `metric_definition` row
on first sighting — this is how new watches/fields are supported for free, and it's the
mechanism behind "never drop an unknown field" (CLAUDE.md).
"""

from datetime import UTC, datetime

from sqlalchemy import Connection, select

from sporthealth.db.schema import metric_definition


def get_or_register_metric(
    conn: Connection,
    *,
    metric_key: str,
    source: str,
    display_name: str | None = None,
    unit_si: str | None = None,
    category: str = "unknown",
    value_type: str = "numeric",
) -> None:
    """Idempotently ensure `metric_key` exists in the registry.

    No-op if already present: first-seen wins. Promoting or renaming a metric is a
    deliberate editorial action, not something ingest should silently do on every re-run.
    """
    existing = conn.execute(
        select(metric_definition.c.metric_key).where(
            metric_definition.c.metric_key == metric_key
        )
    ).scalar_one_or_none()
    if existing is not None:
        return

    conn.execute(
        metric_definition.insert().values(
            metric_key=metric_key,
            display_name=display_name or metric_key,
            unit_si=unit_si,
            category=category,
            value_type=value_type,
            chart_hints=None,
            first_seen_at=datetime.now(UTC),
            first_seen_source=source,
            is_promoted=False,
        )
    )
