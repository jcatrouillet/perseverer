"""When each data source syncs automatically, per athlete.

Every source (Garmin Connect, Eufy, Kaya) has its own time of day, in the athlete's own timezone,
and can be switched off.  No row means the deployment's default (`PERSEVERER_SCHEDULE_HOUR` /
`_MINUTE`, read on the wall clock of `PERSEVERER_SCHEDULE_TIMEZONE`, which is how the single daily
sync always ran).  The worker looks every `TICK_MINUTES` minutes at who is due (worker/main.py::
run_scheduled_syncs) and records the local date each source last ran, so a restart cannot run it
twice in a day.  The API reads and writes the same rows (api/routers/sync_schedule.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, select

from perseverer.adapters.eufy import resolve_eufy_credentials
from perseverer.adapters.garmin_connect import token_store_status
from perseverer.adapters.kaya import token_status as kaya_token_status
from perseverer.config import Settings
from perseverer.db.schema import sync_schedule
from perseverer.db.seed import DEFAULT_ATHLETE_ID

# In the order they run when due together: Kaya after Garmin, so a same-day Garmin session is
# there to merge into.
SOURCES: tuple[str, ...] = ("garmin_connect", "eufy", "kaya")
SOURCE_LABELS = {"garmin_connect": "Garmin Connect", "eufy": "Eufy scale", "kaya": "Kaya"}

# The worker checks this often; a source starts within this many minutes after its time.
TICK_MINUTES = 5

_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_time(value: str) -> tuple[int, int]:
    """ "HH:MM" -> (hour, minute); ValueError for anything else."""
    m = _TIME.match(value)
    if m is None:
        raise ValueError(f"not a HH:MM time: {value!r}")
    return int(m.group(1)), int(m.group(2))


def athlete_zone(timezone: str | None, fallback: str) -> ZoneInfo:
    """The athlete's own timezone, or the deployment's when theirs is missing or not a real one."""
    for name in (timezone, fallback):
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except Exception:  # unknown key, malformed name
            continue
    return ZoneInfo("UTC")


@dataclass(frozen=True)
class SourceSchedule:
    source: str
    enabled: bool
    hour: int
    minute: int
    is_default: bool  # no chosen time: the deployment's default
    zone: ZoneInfo  # the wall clock `hour:minute` is read on
    last_run_on: str | None  # the local date it last ran, "YYYY-MM-DD"


def load_schedules(
    conn: Connection, settings: Settings, athlete_id: str, timezone: str | None
) -> list[SourceSchedule]:
    """One entry per source for the athlete, in `SOURCES` order."""
    rows = {
        r.source: r
        for r in conn.execute(
            select(sync_schedule).where(sync_schedule.c.athlete_id == athlete_id)
        ).fetchall()
    }
    own_zone = athlete_zone(timezone, settings.schedule_timezone)
    default_zone = athlete_zone(settings.schedule_timezone, "UTC")
    out: list[SourceSchedule] = []
    for source in SOURCES:
        row = rows.get(source)
        if row is not None and row.local_time:
            hour, minute = parse_time(row.local_time)
            out.append(
                SourceSchedule(
                    source, bool(row.enabled), hour, minute, False, own_zone, row.last_run_on
                )
            )
        else:
            out.append(
                SourceSchedule(
                    source,
                    True if row is None else bool(row.enabled),
                    settings.schedule_hour,
                    settings.schedule_minute,
                    True,
                    default_zone,
                    row.last_run_on if row is not None else None,
                )
            )
    return out


def is_due(entry: SourceSchedule, now_utc: datetime) -> bool:
    """Whether `now_utc` falls in the first `TICK_MINUTES` after the entry's time on its own wall
    clock, and it has not run today (that clock's date)."""
    if not entry.enabled:
        return False
    local = now_utc.astimezone(entry.zone)
    if entry.last_run_on == local.date().isoformat():
        return False
    since = (local.hour * 60 + local.minute) - (entry.hour * 60 + entry.minute)
    return 0 <= since < TICK_MINUTES


def next_run(entry: SourceSchedule, now_utc: datetime) -> datetime | None:
    """The next instant (UTC) the entry is due, or None when it is switched off.  Today's time if
    it is still ahead and has not run today, otherwise tomorrow's."""
    if not entry.enabled:
        return None
    local = now_utc.astimezone(entry.zone)

    def at(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, entry.hour, entry.minute, tzinfo=entry.zone)

    today = at(local.date())
    # A run that is due right now (inside its window) still counts as upcoming only if it has not
    # run: once it has, the next one is tomorrow's.
    if today > local or (is_due(entry, now_utc) and entry.last_run_on != local.date().isoformat()):
        return today.astimezone(UTC)
    return at(local.date() + timedelta(days=1)).astimezone(UTC)


def mark_run(conn: Connection, athlete_id: str, source: str, local_date: date) -> None:
    """Records that `source` ran on `local_date` (creating the row, with the default time, if the
    athlete never chose one)."""
    updated = conn.execute(
        sync_schedule.update()
        .where(sync_schedule.c.athlete_id == athlete_id, sync_schedule.c.source == source)
        .values(last_run_on=local_date.isoformat())
    )
    if updated.rowcount == 0:
        conn.execute(
            sync_schedule.insert().values(
                athlete_id=athlete_id,
                source=source,
                local_time=None,
                enabled=True,
                last_run_on=local_date.isoformat(),
            )
        )


def _eufy_configured(conn: Connection, settings: Settings, athlete_id: str) -> bool:
    email, password, *_ = resolve_eufy_credentials(
        conn,
        athlete_id,
        legacy_athlete_id=DEFAULT_ATHLETE_ID,
        legacy_email=settings.eufy_email,
        legacy_password=settings.eufy_password,
        legacy_device_id=settings.eufy_device_id,
        legacy_customer_id=settings.eufy_customer_id,
    )
    return bool(email and password)


def source_configured(conn: Connection, settings: Settings, athlete_id: str, source: str) -> bool:
    """Whether the athlete has connected `source` (a source they have not connected never runs)."""
    if source == "garmin_connect":
        return token_store_status(settings.garmin_tokenstore_dir_for(athlete_id))[0]
    if source == "eufy":
        return _eufy_configured(conn, settings, athlete_id)
    if source == "kaya":
        return kaya_token_status(settings.kaya_tokenstore_dir_for(athlete_id))[0]
    return False
