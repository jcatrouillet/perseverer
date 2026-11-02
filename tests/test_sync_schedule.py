"""Per-source automatic sync times: the pure due/next logic and the worker tick."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, select

from perseverer.adapters.fit_folder import IngestRunSummary
from perseverer.config import Settings
from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, metadata, sync_schedule
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.sync_schedule import (
    SourceSchedule,
    is_due,
    load_schedules,
    mark_run,
    next_run,
    parse_time,
)
from perseverer.worker.main import run_scheduled_syncs

UTC = dt.UTC
PARIS = ZoneInfo("Europe/Paris")


def _entry(**over: Any) -> SourceSchedule:
    base: dict[str, Any] = {
        "source": "garmin_connect",
        "enabled": True,
        "hour": 6,
        "minute": 30,
        "is_default": False,
        "zone": PARIS,
        "last_run_on": None,
    }
    base.update(over)
    return SourceSchedule(**base)


def test_parse_time() -> None:
    assert parse_time("06:05") == (6, 5)
    for bad in ("6:05", "24:00", "12:60", "noon", ""):
        with pytest.raises(ValueError):
            parse_time(bad)


def test_due_only_in_the_window_after_the_time_on_the_entrys_own_clock() -> None:
    entry = _entry()  # 06:30 Paris = 04:30 UTC in summer
    assert not is_due(entry, dt.datetime(2026, 7, 1, 4, 29, tzinfo=UTC))
    assert is_due(entry, dt.datetime(2026, 7, 1, 4, 30, tzinfo=UTC))
    assert is_due(entry, dt.datetime(2026, 7, 1, 4, 34, tzinfo=UTC))
    assert not is_due(entry, dt.datetime(2026, 7, 1, 4, 35, tzinfo=UTC))
    # Winter: Paris is UTC+1, so the same wall-clock time is an hour later in UTC.
    assert is_due(entry, dt.datetime(2026, 12, 1, 5, 31, tzinfo=UTC))
    assert not is_due(entry, dt.datetime(2026, 12, 1, 4, 31, tzinfo=UTC))


def test_not_due_when_switched_off_or_already_run_today() -> None:
    now = dt.datetime(2026, 7, 1, 4, 31, tzinfo=UTC)
    assert not is_due(_entry(enabled=False), now)
    assert not is_due(_entry(last_run_on="2026-07-01"), now)
    assert is_due(_entry(last_run_on="2026-06-30"), now)


def test_next_run_is_today_if_ahead_else_tomorrow() -> None:
    entry = _entry()
    morning = dt.datetime(2026, 7, 1, 3, 0, tzinfo=UTC)
    assert next_run(entry, morning) == dt.datetime(2026, 7, 1, 4, 30, tzinfo=UTC)
    afternoon = dt.datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert next_run(entry, afternoon) == dt.datetime(2026, 7, 2, 4, 30, tzinfo=UTC)
    # Ahead on the clock but it already ran today (the time was moved later): tomorrow.
    assert next_run(
        _entry(last_run_on="2026-07-01"), dt.datetime(2026, 7, 1, 4, 32, tzinfo=UTC)
    ) == (dt.datetime(2026, 7, 2, 4, 30, tzinfo=UTC))
    assert next_run(_entry(enabled=False), morning) is None


def _engine(tmp_path: Path, timezone: str = "UTC") -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone=timezone,
                unit_preference="metric",
                created_at=dt.datetime.now(UTC),
            )
        )
        conn.commit()
    return engine


def test_load_schedules_defaults_to_the_deployments_time(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "Europe/Paris")
    settings = Settings(data_dir=tmp_path, schedule_hour=4, schedule_minute=15)
    with engine.connect() as conn:
        entries = load_schedules(conn, settings, DEFAULT_ATHLETE_ID, "Europe/Paris")
    assert [e.source for e in entries] == ["garmin_connect", "eufy", "kaya"]
    assert all(e.is_default and e.enabled and (e.hour, e.minute) == (4, 15) for e in entries)
    # The default is read on the deployment's own clock (UTC here), a chosen time on the athlete's.
    assert all(str(e.zone) == "UTC" for e in entries)


def test_a_chosen_time_is_on_the_athletes_clock(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "Europe/Paris")
    settings = Settings(data_dir=tmp_path)
    with engine.connect() as conn:
        conn.execute(
            sync_schedule.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID, source="eufy", local_time="07:45", enabled=True
            )
        )
        conn.commit()
        eufy = next(
            e
            for e in load_schedules(conn, settings, DEFAULT_ATHLETE_ID, "Europe/Paris")
            if e.source == "eufy"
        )
    assert (eufy.hour, eufy.minute, eufy.is_default) == (7, 45, False)
    assert str(eufy.zone) == "Europe/Paris"


def test_mark_run_creates_or_updates_the_row(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        mark_run(conn, DEFAULT_ATHLETE_ID, "kaya", dt.date(2026, 7, 1))
        mark_run(conn, DEFAULT_ATHLETE_ID, "kaya", dt.date(2026, 7, 2))
        conn.commit()
        rows = conn.execute(select(sync_schedule)).fetchall()
    assert len(rows) == 1
    assert (rows[0].last_run_on, rows[0].local_time, rows[0].enabled) == ("2026-07-02", None, True)


# --- the worker tick -----------------------------------------------------------------------


def _tick(tmp_path: Path, engine: Engine, now: dt.datetime, calls: list[str]) -> None:
    settings = Settings(data_dir=tmp_path)
    token_dir = settings.garmin_tokenstore_dir_for(DEFAULT_ATHLETE_ID)
    token_dir.mkdir(parents=True, exist_ok=True)
    (token_dir / "garmin_tokens.json").write_text("{}")

    def fake_garmin(*a: Any, **kw: Any) -> IngestRunSummary:
        calls.append("garmin_connect")
        return IngestRunSummary(run_id=0)

    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.sync_garmin_connect", side_effect=fake_garmin),
        patch("perseverer.sync_schedule.token_store_status", return_value=(True, 0)),
    ):
        run_scheduled_syncs(now)


def test_tick_runs_a_due_connected_source_once_a_day(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            sync_schedule.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID, source="garmin_connect", local_time="09:10"
            )
        )
        conn.commit()
    calls: list[str] = []
    _tick(tmp_path, engine, dt.datetime(2026, 7, 1, 9, 12, tzinfo=UTC), calls)
    assert calls == ["garmin_connect"]
    # The next tick, still in the window (a clock that ran slow), does not run it again.
    _tick(tmp_path, engine, dt.datetime(2026, 7, 1, 9, 14, tzinfo=UTC), calls)
    assert calls == ["garmin_connect"]
    # Tomorrow it does.
    _tick(tmp_path, engine, dt.datetime(2026, 7, 2, 9, 11, tzinfo=UTC), calls)
    assert calls == ["garmin_connect", "garmin_connect"]


def test_tick_skips_other_times_and_a_switched_off_source(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            sync_schedule.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                source="garmin_connect",
                local_time="09:10",
                enabled=False,
            )
        )
        conn.commit()
    calls: list[str] = []
    _tick(tmp_path, engine, dt.datetime(2026, 7, 1, 9, 11, tzinfo=UTC), calls)  # off
    _tick(tmp_path, engine, dt.datetime(2026, 7, 1, 15, 0, tzinfo=UTC), calls)  # not its time
    assert calls == []


def test_tick_follows_the_athletes_own_timezone(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "America/Los_Angeles")
    with engine.connect() as conn:
        conn.execute(
            sync_schedule.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID, source="garmin_connect", local_time="06:00"
            )
        )
        conn.commit()
    calls: list[str] = []
    # 06:00 in Los Angeles (PDT, UTC-7) is 13:00 UTC.
    _tick(tmp_path, engine, dt.datetime(2026, 7, 1, 6, 1, tzinfo=UTC), calls)
    assert calls == []
    _tick(tmp_path, engine, dt.datetime(2026, 7, 1, 13, 1, tzinfo=UTC), calls)
    assert calls == ["garmin_connect"]


def test_a_failing_source_is_not_retried_the_same_day(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    settings = Settings(data_dir=tmp_path)
    attempts: list[int] = []

    def boom(*a: Any, **kw: Any) -> None:
        attempts.append(1)
        raise RuntimeError("garmin said no")

    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.sync_garmin_connect", side_effect=boom),
        patch("perseverer.sync_schedule.token_store_status", return_value=(True, 0)),
    ):
        for minute in (16, 18):  # default 04:15 UTC
            run_scheduled_syncs(dt.datetime(2026, 7, 1, 4, minute, tzinfo=UTC))
    assert attempts == [1]
