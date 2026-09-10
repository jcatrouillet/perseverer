"""Tests for email_reports.py (report building + email-safe rendering) and email_delivery.py
(the SMTP wire modes), with smtplib monkeypatched -- no real network in the suite.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy import Connection, Engine

from perseverer.config import Settings
from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    athlete,
    athlete_email_report_config,
    athlete_running_load_config,
    metadata,
    period_rollup,
    planned_workout,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.email_reports import (
    athletes_opted_in,
    build_monthly_report,
    build_weekly_report,
    render_monthly_email,
    render_weekly_email,
    send_report_email,
)

# The Sunday the weekly job fires on; the Mon-Sun week that just ended is 2026-09-07..2026-09-13.
SUNDAY = dt.date(2026, 9, 13)
PREV_WEEK_START = "2026-09-07"
COMING_WEEK_START = dt.date(2026, 9, 14)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    eng = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(eng)
    with eng.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Jerome",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime(2020, 1, 1),
                email="jerome@example.com",
            )
        )
        conn.commit()
    return eng


@pytest.fixture
def conn(engine: Engine):  # type: ignore[no-untyped-def]
    with engine.connect() as c:
        yield c


def _seed_week_rollup(conn: Connection) -> None:
    conn.execute(
        period_rollup.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            period_type="week",
            period_start=PREV_WEEK_START,
            period_end="2026-09-13",
            activity_count=3,
            activity_duration_s=10800.0,
            activity_moving_duration_s=10200.0,
            activity_distance_m=42000.0,
            activity_elevation_gain_m=310.0,
            activity_calories=2600.0,
            activity_days_count=3,
            sleep_total_s=90000.0,
            refreshed_at=dt.datetime(2026, 9, 13, 4, 15),
        )
    )
    for aid, day, sport, dist, dur in [
        ("r1", "2026-09-08", "running", 12000.0, 3600.0),
        ("r2", "2026-09-10", "running", 18000.0, 4800.0),
        ("s1", "2026-09-11", "strength_training", None, 2400.0),
    ]:
        conn.execute(
            activity.insert().values(
                id=aid,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=dt.datetime.fromisoformat(f"{day}T07:00:00"),
                utc_offset_s=0,
                local_date=day,
                sport=sport,
                distance_m=dist,
                duration_s=dur,
                moving_duration_s=dur,
                primary_source="fit_folder",
                created_at=dt.datetime(2026, 9, 12, 8, 0),
                updated_at=dt.datetime(2026, 9, 12, 8, 0),
            )
        )
    conn.commit()


def _plan(conn: Connection, **kw: object) -> None:
    conn.execute(
        planned_workout.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            push_status="draft",
            created_at=dt.datetime(2026, 9, 1),
            updated_at=dt.datetime(2026, 9, 1),
            **kw,
        )
    )


def test_build_weekly_report_totals_and_sport_breakdown(conn: Connection) -> None:
    _seed_week_rollup(conn)
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)

    assert report.athlete_name == "Jerome"
    assert report.prev_start == dt.date(2026, 9, 7)
    assert report.prev_end == dt.date(2026, 9, 13)
    assert report.coming_start == COMING_WEEK_START
    assert report.totals.activity_count == 3
    assert report.totals.distance_m == 42000.0
    assert report.totals.active_days == 3
    # Sorted distance-desc: running (30 km) before strength (no distance).
    assert [s.sport for s in report.sports] == ["running", "strength_training"]
    assert report.sports[0].count == 2
    assert report.sports[0].distance_m == 30000.0


def test_build_weekly_report_is_empty_when_no_rollup_row(conn: Connection) -> None:
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert report.totals.activity_count == 0
    assert report.totals.distance_m is None
    assert report.sports == []
    assert report.coming_workouts == []


def test_weekly_report_coming_workouts_with_running_estimate(conn: Connection) -> None:
    conn.execute(
        athlete_running_load_config.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            threshold_pace_sec_per_km=281.0,
            updated_at=dt.datetime(2026, 9, 1),
        )
    )
    _plan(
        conn,
        local_date="2026-09-15",
        sport="running",
        name="Intervals",
        source_text="Warmup 10m 6:00/km Pace\n4x\n3m 4:30/km Pace\n2m 6:30/km Pace",
        scheduled_time="10:00",
        estimated_duration_s=1800.0,
    )
    _plan(
        conn,
        local_date="2026-09-17",
        sport="yoga",
        name="Evening yoga",
        source_text="Just breathe",
        estimated_duration_s=1800.0,
    )
    # A workout outside the coming week is not included.
    _plan(
        conn,
        local_date="2026-09-28",
        sport="running",
        name="Way later",
        source_text="30m 5:30/km Pace",
        estimated_duration_s=1800.0,
    )
    conn.commit()

    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    names = [w.name for w in report.coming_workouts]
    assert names == ["Intervals", "Evening yoga"]
    running = report.coming_workouts[0]
    assert running.estimate_line is not None
    assert "km" in running.estimate_line and "Load" in running.estimate_line
    assert report.coming_workouts[1].estimate_line is None


def test_render_weekly_email_is_email_safe_and_has_the_figures(conn: Connection) -> None:
    _seed_week_rollup(conn)
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)

    assert "42.0 km" in rendered.subject
    assert "42.0 km" in rendered.html
    assert "42.0 km" in rendered.text
    # No external resources -- email clients block them and it leaks read receipts.
    assert "src=http" not in rendered.html
    assert 'href="http' not in rendered.html
    assert "<style" not in rendered.html
    assert "Coming week" in rendered.html


def test_render_monthly_email_has_no_planned_section(conn: Connection) -> None:
    conn.execute(
        period_rollup.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            period_type="month",
            period_start="2026-09-01",
            period_end="2026-09-30",
            activity_count=12,
            activity_distance_m=160000.0,
            activity_days_count=10,
            refreshed_at=dt.datetime(2026, 9, 30, 4, 15),
        )
    )
    conn.commit()
    report = build_monthly_report(
        conn, athlete_id=DEFAULT_ATHLETE_ID, today=dt.date(2026, 9, 30)
    )
    assert report.month_label == "September 2026"
    rendered = render_monthly_email(report)
    assert "September 2026" in rendered.subject
    assert "Coming week" not in rendered.html
    assert "160.0 km" in rendered.html


def test_athletes_opted_in(conn: Connection) -> None:
    assert athletes_opted_in(conn, kind="weekly") == []
    conn.execute(
        athlete_email_report_config.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            weekly_enabled=True,
            monthly_enabled=False,
            updated_at=dt.datetime(2026, 9, 1),
        )
    )
    conn.commit()
    assert athletes_opted_in(conn, kind="weekly") == [DEFAULT_ATHLETE_ID]
    assert athletes_opted_in(conn, kind="monthly") == []


# --- delivery -----------------------------------------------------------------------------


class _FakeSMTP:
    instances: ClassVar[list[_FakeSMTP]] = []

    def __init__(self, host: str, port: int, timeout: float = 0, context: object = None) -> None:
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.sent: object | None = None
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self, context: object = None) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, msg: object) -> None:
        self.sent = msg


@pytest.fixture(autouse=True)
def _reset_fake_smtp() -> None:
    _FakeSMTP.instances.clear()


def test_send_report_email_starttls_path(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_rollup(conn)
    monkeypatch.setattr("perseverer.email_delivery.smtplib.SMTP", _FakeSMTP)
    settings = Settings(
        smtp_host="ssl0.ovh.net",
        smtp_port=587,
        smtp_username="me@example.com",
        smtp_password="secret",
        smtp_from="me@example.com",
        smtp_security="starttls",
    )
    send_report_email(
        settings, conn, athlete_id=DEFAULT_ATHLETE_ID, kind="weekly", today=SUNDAY
    )
    smtp = _FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("ssl0.ovh.net", 587)
    assert smtp.started_tls is True
    assert smtp.logged_in == ("me@example.com", "secret")
    assert smtp.sent is not None
    assert smtp.sent["To"] == "jerome@example.com"  # type: ignore[index]


def test_send_report_email_ssl_path(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_rollup(conn)
    monkeypatch.setattr("perseverer.email_delivery.smtplib.SMTP_SSL", _FakeSMTP)
    settings = Settings(
        smtp_host="ssl0.ovh.net",
        smtp_port=465,
        smtp_username="me@example.com",
        smtp_password="secret",
        smtp_from="me@example.com",
        smtp_security="ssl",
    )
    send_report_email(
        settings, conn, athlete_id=DEFAULT_ATHLETE_ID, kind="weekly", today=SUNDAY
    )
    smtp = _FakeSMTP.instances[0]
    assert smtp.port == 465
    assert smtp.started_tls is False
    assert smtp.logged_in == ("me@example.com", "secret")


def test_send_report_email_raises_without_a_recipient(
    conn: Connection, engine: Engine
) -> None:
    conn.execute(athlete.update().where(athlete.c.id == DEFAULT_ATHLETE_ID).values(email=None))
    conn.commit()
    with pytest.raises(ValueError, match="no email"):
        send_report_email(
            Settings(smtp_host="h", smtp_username="u", smtp_password="p", smtp_from="f"),
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            kind="weekly",
            today=SUNDAY,
        )
