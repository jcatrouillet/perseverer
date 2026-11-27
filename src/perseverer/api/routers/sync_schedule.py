"""GET/PUT /settings/sync-schedule -- when each data source (Garmin Connect, Eufy, Kaya) syncs by
itself, per athlete (sync_schedule.py).  Times are on the athlete's own clock (their profile
timezone); a source with no chosen time uses the deployment's default."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.sync_schedule import (
    SyncScheduleIn,
    SyncScheduleOut,
    SyncSourceOut,
)
from perseverer.config import Settings, get_settings
from perseverer.db.schema import athlete, sync_schedule
from perseverer.sync_schedule import (
    SOURCE_LABELS,
    TICK_MINUTES,
    SourceSchedule,
    athlete_zone,
    load_schedules,
    next_activity_check,
    next_run,
    source_configured,
)

router = APIRouter()


def _athlete_timezone(conn: Connection, athlete_id: str) -> str | None:
    return conn.execute(
        select(athlete.c.timezone).where(athlete.c.id == athlete_id)
    ).scalar_one_or_none()


def _out(conn: Connection, settings: Settings, athlete_id: str) -> SyncScheduleOut:
    timezone = _athlete_timezone(conn, athlete_id)
    zone = athlete_zone(timezone, settings.schedule_timezone)
    now = datetime.now(UTC)
    sources: list[SyncSourceOut] = []
    for entry in load_schedules(conn, settings, athlete_id, timezone):
        configured = source_configured(conn, settings, athlete_id, entry.source)
        # A default time is the deployment's own clock; shown on the athlete's, so the page reads
        # in one timezone.
        shown = _on_clock(entry, zone, now)
        sources.append(
            SyncSourceOut(
                source=entry.source,  # type: ignore[arg-type]
                label=SOURCE_LABELS[entry.source],
                configured=configured,
                enabled=entry.enabled,
                time=f"{shown.hour:02d}:{shown.minute:02d}",
                is_default=entry.is_default,
                timezone=str(zone),
                next_run_at=next_run(entry, now) if configured else None,
                activity_check_hours=entry.activity_check_hours,
                next_activity_check_at=next_activity_check(entry, now) if configured else None,
            )
        )
    return SyncScheduleOut(sources=sources, window_minutes=TICK_MINUTES)


def _on_clock(entry: SourceSchedule, zone: ZoneInfo, now: datetime) -> datetime:
    """The entry's time today, expressed on `zone`'s clock."""
    local = now.astimezone(entry.zone)
    at = local.replace(hour=entry.hour, minute=entry.minute, second=0, microsecond=0)
    return at.astimezone(zone)


@router.get("/settings/sync-schedule")
def get_sync_schedule(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> SyncScheduleOut:
    return _out(conn, settings, athlete_id)


@router.put("/settings/sync-schedule")
def put_sync_schedule(
    payload: SyncScheduleIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> SyncScheduleOut:
    """Saves the time (null = the default), on/off and, for Garmin Connect, the hourly new-activity
    check interval (null = off; left out = unchanged) of each source listed.  The date a source
    last ran is kept, so moving a time after today's run does not make it run again today."""
    for item in payload.sources:
        values: dict[str, object] = {"local_time": item.time, "enabled": item.enabled}
        if "activity_check_hours" in item.model_fields_set:
            if item.source != "garmin_connect" and item.activity_check_hours is not None:
                raise HTTPException(
                    status_code=422,
                    detail="activity_check_hours applies to garmin_connect only",
                )
            values["activity_check_hours"] = item.activity_check_hours
            values["activity_check_next_at"] = None  # due at the next tick after a change
        updated = conn.execute(
            sync_schedule.update()
            .where(sync_schedule.c.athlete_id == athlete_id, sync_schedule.c.source == item.source)
            .values(**values)
        )
        if updated.rowcount == 0:
            conn.execute(
                sync_schedule.insert().values(
                    athlete_id=athlete_id, source=item.source, last_run_on=None, **values
                )
            )
    conn.commit()
    return _out(conn, settings, athlete_id)
