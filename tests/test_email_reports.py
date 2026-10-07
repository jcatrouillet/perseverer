"""Tests for email_reports.py (report building + email-safe rendering) and email_delivery.py
(the SMTP wire modes), with smtplib monkeypatched -- no real network in the suite.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from sqlalchemy import Connection, Engine

from perseverer.config import Settings
from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    athlete,
    athlete_email_report_config,
    athlete_running_load_config,
    health_metric_daily_rollup,
    metadata,
    metric_definition,
    note,
    performance_daily_rollup,
    period_rollup,
    planned_race,
    planned_workout,
    sleep_session,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.email_reports import (
    _ACCENT,
    _BAR_TRACK_PX,
    NoteLine,
    PlannedRaceLine,
    RunLine,
    _bar,
    _clock_hm,
    _coming_week_forecast,
    _coming_week_notes,
    _future_race_date_label,
    _future_race_goal,
    _running_distance_total_m,
    _week_runs,
    athletes_opted_in,
    build_monthly_report,
    build_weekly_report,
    render_monthly_email,
    render_weekly_email,
    send_report_email,
)
from perseverer.weather_forecast import ForecastDay

# The Sunday the weekly job fires on; the Mon-Sun week that just ended is 2026-09-07..2026-09-13.
SUNDAY = dt.date(2026, 9, 13)
PREV_WEEK_START = "2026-09-07"
COMING_WEEK_START = dt.date(2026, 9, 14)
COMING_WEEK_END = dt.date(2026, 9, 20)
WEEK_BEFORE_START = "2026-08-31"
WEEK_BEFORE_END = "2026-09-06"


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


def _race(conn: Connection, **kw: object) -> None:
    conn.execute(
        planned_race.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            sport="running",
            created_at=dt.datetime(2026, 9, 1),
            updated_at=dt.datetime(2026, 9, 1),
            **kw,
        )
    )


def _note(conn: Connection, *, entity_id: str, body: str, author: str | None = None) -> None:
    conn.execute(
        note.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            entity_type="week",
            entity_id=entity_id,
            body=body,
            author=author,
            created_at=dt.datetime(2026, 9, 1),
            updated_at=dt.datetime(2026, 9, 1),
        )
    )


def _set_home_location(conn: Connection, *, lat: float, lon: float, timezone: str = "UTC") -> None:
    conn.execute(
        athlete.update()
        .where(athlete.c.id == DEFAULT_ATHLETE_ID)
        .values(home_lat=lat, home_lon=lon, timezone=timezone)
    )


def _activity(
    conn: Connection,
    *,
    activity_id: str,
    local_date: str,
    sport: str,
    sub_sport: str | None = None,
    distance_m: float | None = None,
    duration_s: float | None = None,
) -> None:
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=dt.datetime.fromisoformat(f"{local_date}T07:00:00"),
            utc_offset_s=0,
            local_date=local_date,
            sport=sport,
            sub_sport=sub_sport,
            distance_m=distance_m,
            duration_s=duration_s,
            moving_duration_s=duration_s,
            primary_source="fit_folder",
            created_at=dt.datetime(2026, 9, 12, 8, 0),
            updated_at=dt.datetime(2026, 9, 12, 8, 0),
        )
    )


def _steps(conn: Connection, *, local_date: str, metric_key: str, value_sum: float) -> None:
    existing = conn.execute(
        metric_definition.select().where(metric_definition.c.metric_key == metric_key)
    ).fetchone()
    if existing is None:
        conn.execute(
            metric_definition.insert().values(
                metric_key=metric_key,
                display_name=metric_key,
                category="health",
                value_type="numeric",
                first_seen_at=dt.datetime(2026, 9, 12, 8, 0),
                first_seen_source="garmin_connect",
            )
        )
    conn.execute(
        health_metric_daily_rollup.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            local_date=local_date,
            metric_key=metric_key,
            value_sum=value_sum,
            n_observations=1,
            refreshed_at=dt.datetime(2026, 9, 12, 8, 0),
        )
    )


def _sleep(
    conn: Connection, *, local_date: str, total_sleep_s: float, source: str = "garmin_connect"
) -> None:
    start = dt.datetime.fromisoformat(f"{local_date}T23:00:00")
    conn.execute(
        sleep_session.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            local_date=local_date,
            start_time_utc=start,
            end_time_utc=start + dt.timedelta(seconds=total_sleep_s),
            total_sleep_s=total_sleep_s,
            source=source,
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


def test_recorded_yoga_activity_labeled_yoga_not_training(conn: Connection) -> None:
    # A real recorded yoga session is stored sport="training"/sub_sport="yoga" (Garmin's own FIT
    # taxonomy uses "training" as a generic container for indoor cardio/strength/mindfulness
    # work) -- the sport breakdown must show "Yoga", never the device's own generic container.
    _activity(conn, activity_id="y1", local_date="2026-09-09", sport="training", sub_sport="yoga")
    conn.commit()

    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert [s.sport for s in report.sports] == ["yoga"]

    rendered = render_weekly_email(report)
    assert "Yoga" in rendered.html and "Yoga" in rendered.text
    assert "Training" not in rendered.html and "Training" not in rendered.text


def test_sport_breakdown_merges_multiple_training_sub_sports_separately(
    conn: Connection,
) -> None:
    _activity(conn, activity_id="y1", local_date="2026-09-09", sport="training", sub_sport="yoga")
    _activity(
        conn,
        activity_id="y2",
        local_date="2026-09-10",
        sport="training",
        sub_sport="strength_training",
    )
    conn.commit()

    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert {s.sport for s in report.sports} == {"yoga", "strength_training"}
    assert all(s.count == 1 for s in report.sports)


def test_build_weekly_report_is_empty_when_no_rollup_row(conn: Connection) -> None:
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert report.totals.activity_count == 0
    assert report.totals.distance_m is None
    assert report.sports == []
    assert report.coming_workouts == []
    assert report.coming_forecast == []
    assert report.coming_week_notes == []


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


def test_coming_week_notes_reads_the_coming_mondays_week_notes_only(conn: Connection) -> None:
    _note(conn, entity_id=COMING_WEEK_START.isoformat(), body="Taper this week.", author="Jerome")
    _note(conn, entity_id=COMING_WEEK_START.isoformat(), body="Legs still sore Monday.")
    # A note on last week (a different entity_id) must not leak into the coming week's list.
    _note(conn, entity_id=PREV_WEEK_START, body="Last week's note.")
    conn.commit()

    notes = _coming_week_notes(conn, DEFAULT_ATHLETE_ID, COMING_WEEK_START.isoformat())
    assert notes == [
        NoteLine(body="Taper this week.", author="Jerome"),
        NoteLine(body="Legs still sore Monday.", author=None),
    ]


def test_build_weekly_report_includes_coming_week_notes(conn: Connection) -> None:
    _note(conn, entity_id=COMING_WEEK_START.isoformat(), body="Recovery week.")
    conn.commit()
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert report.coming_week_notes == [NoteLine(body="Recovery week.", author=None)]


def test_coming_week_forecast_unavailable_without_a_home_location(conn: Connection) -> None:
    forecast = _coming_week_forecast(
        conn, DEFAULT_ATHLETE_ID, SUNDAY, COMING_WEEK_START, COMING_WEEK_END
    )
    assert forecast == []


def test_coming_week_forecast_fetches_in_the_athletes_own_timezone_and_days(
    conn: Connection,
) -> None:
    _set_home_location(conn, lat=48.8566, lon=2.3522, timezone="America/Los_Angeles")
    conn.commit()
    captured: dict[str, str] = {}
    raw = {
        "daily": {
            "time": [
                "2026-09-13",
                "2026-09-14",
                "2026-09-15",
                "2026-09-16",
                "2026-09-17",
                "2026-09-18",
                "2026-09-19",
                "2026-09-20",
            ],
            "weathercode": [0, 3, 3, 61, 0, 0, 2, 3],
            "temperature_2m_max": [25.0, 26.0, 27.0, 20.0, 24.0, 23.0, 22.0, 21.0],
            "temperature_2m_min": [15.0, 16.0, 17.0, 14.0, 15.0, 14.0, 13.0, 12.0],
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.url.params)
        return httpx.Response(200, json=raw)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    forecast = _coming_week_forecast(
        conn, DEFAULT_ATHLETE_ID, SUNDAY, COMING_WEEK_START, COMING_WEEK_END, client=client
    )

    assert captured["timezone"] == "America/Los_Angeles"
    assert captured["forecast_days"] == "8"
    # Sept 13 (today, day offset 0) is dropped -- only the coming Mon..Sun week itself.
    assert [f.local_date.isoformat() for f in forecast] == [
        "2026-09-14",
        "2026-09-15",
        "2026-09-16",
        "2026-09-17",
        "2026-09-18",
        "2026-09-19",
        "2026-09-20",
    ]


def test_build_weekly_report_includes_coming_forecast(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_home_location(conn, lat=48.8566, lon=2.3522)
    conn.commit()
    fake_days = [ForecastDay(COMING_WEEK_START, 3, 12.0, 20.0)]
    monkeypatch.setattr("perseverer.email_reports.fetch_forecast", lambda *a, **kw: fake_days)
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert report.coming_forecast == fake_days


def test_running_distance_by_day_and_average_pace(conn: Connection) -> None:
    # r1: Tue 2026-09-08, 12 km in 3600s. r2: Thu 2026-09-10, 18 km in 4800s. Strength day (s1)
    # has no distance and must not contribute to either the daily series or the pace average.
    _seed_week_rollup(conn)

    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)

    by_date = {p.local_date: p.value for p in report.running_distance_by_day}
    assert by_date == {
        "2026-09-07": None,
        "2026-09-08": 12000.0,
        "2026-09-09": None,
        "2026-09-10": 18000.0,
        "2026-09-11": None,
        "2026-09-12": None,
        "2026-09-13": None,
    }
    # Total 30 km in 8400s -- 280 s/km, i.e. 4:40 /km.
    assert report.avg_running_pace_s_per_km == 280.0


def test_running_distance_by_day_covers_every_day_even_with_no_runs(conn: Connection) -> None:
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert len(report.running_distance_by_day) == 7
    assert all(p.value is None for p in report.running_distance_by_day)
    assert report.avg_running_pace_s_per_km is None
    assert report.running_distance_week_before_m is None
    assert report.week_runs == []


def test_running_distance_total_m_sums_only_running_and_none_when_no_runs(
    conn: Connection,
) -> None:
    _seed_week_rollup(conn)
    assert (
        _running_distance_total_m(conn, DEFAULT_ATHLETE_ID, PREV_WEEK_START, "2026-09-13")
        == 30000.0
    )
    assert (
        _running_distance_total_m(conn, DEFAULT_ATHLETE_ID, WEEK_BEFORE_START, WEEK_BEFORE_END)
        is None
    )


def test_build_weekly_report_running_distance_vs_week_before(conn: Connection) -> None:
    _seed_week_rollup(conn)  # 30 km running in the week that just ended
    _activity(
        conn,
        activity_id="w1",
        local_date=WEEK_BEFORE_END,
        sport="running",
        distance_m=20000.0,
        duration_s=6000.0,
    )
    conn.commit()
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert report.running_distance_week_before_m == 20000.0


def test_week_runs_lists_each_run_and_skips_a_run_missing_duration(conn: Connection) -> None:
    _seed_week_rollup(conn)  # r1: Tue 12 km/3600s; r2: Thu 18 km/4800s; s1: strength (not running)
    _activity(
        conn,
        activity_id="r3",
        local_date="2026-09-11",
        sport="running",
        distance_m=5000.0,
        duration_s=None,
    )
    conn.commit()

    runs = _week_runs(conn, DEFAULT_ATHLETE_ID, PREV_WEEK_START, "2026-09-13")
    assert runs == [
        RunLine(
            local_date="2026-09-08", distance_m=12000.0, duration_s=3600.0, pace_s_per_km=300.0
        ),
        RunLine(
            local_date="2026-09-10",
            distance_m=18000.0,
            duration_s=4800.0,
            pace_s_per_km=4800.0 / 18.0,
        ),
    ]


def test_build_weekly_report_includes_week_runs(conn: Connection) -> None:
    _seed_week_rollup(conn)
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert [r.local_date for r in report.week_runs] == ["2026-09-08", "2026-09-10"]


def test_weekly_email_shows_runs_of_the_week_before_next_to_the_week_just_ended(
    conn: Connection,
) -> None:
    _seed_week_rollup(conn)  # runs on 2026-09-08 and 2026-09-10 (the week just ended)
    _activity(
        conn,
        activity_id="old-run",
        local_date="2026-09-02",
        sport="running",
        distance_m=7000.0,
        duration_s=2100.0,
    )
    conn.commit()
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert [r.local_date for r in report.week_before_runs] == ["2026-09-02"]

    rendered = render_weekly_email(report)
    assert "Runs this week" in rendered.html
    assert "Runs the week before" in rendered.html
    assert "Mon 31 Aug - Sun 6 Sep" in rendered.html
    assert "Runs the week before (Mon 31 Aug - Sun 6 Sep)" in rendered.text


def test_weekly_email_omits_the_week_before_table_without_runs(conn: Connection) -> None:
    _seed_week_rollup(conn)
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert report.week_before_runs == []
    assert "Runs the week before" not in render_weekly_email(report).html


def test_steps_by_day_prefers_the_higher_priority_alias_on_a_shared_date(
    conn: Connection,
) -> None:
    _steps(
        conn,
        local_date="2026-09-08",
        metric_key="garmin.export.UDSFile.totalSteps",
        value_sum=1000.0,
    )
    _steps(
        conn,
        local_date="2026-09-08",
        metric_key="garmin.daily_summary.totalSteps",
        value_sum=9500.0,
    )
    _steps(
        conn,
        local_date="2026-09-09",
        metric_key="garmin.daily_summary.totalSteps",
        value_sum=8200.0,
    )
    conn.commit()

    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    by_date = {p.local_date: p.value for p in report.steps_by_day}
    assert by_date["2026-09-08"] == 9500.0  # daily_summary wins over the export alias
    assert by_date["2026-09-09"] == 8200.0
    assert by_date["2026-09-07"] is None


def test_sleep_hours_by_day(conn: Connection) -> None:
    _sleep(conn, local_date="2026-09-08", total_sleep_s=7 * 3600 + 30 * 60)  # 7h30m
    _sleep(conn, local_date="2026-09-10", total_sleep_s=6 * 3600)
    conn.commit()

    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    by_date = {p.local_date: p.value for p in report.sleep_hours_by_day}
    assert by_date["2026-09-08"] == 7.5
    assert by_date["2026-09-10"] == 6.0
    assert by_date["2026-09-07"] is None
    assert len(report.sleep_hours_by_day) == 7


def test_sleep_hours_by_day_takes_the_max_when_two_sources_share_a_date(
    conn: Connection,
) -> None:
    _sleep(conn, local_date="2026-09-08", total_sleep_s=6 * 3600, source="garmin_export")
    _sleep(conn, local_date="2026-09-08", total_sleep_s=7 * 3600, source="garmin_connect")
    conn.commit()

    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    by_date = {p.local_date: p.value for p in report.sleep_hours_by_day}
    assert by_date["2026-09-08"] == 7.0


def test_bar_uses_literal_pixel_widths_not_percentages() -> None:
    # Regression: a percentage-width <div>, and then a percentage-width nested <table
    # width="100%">, both confirmed LIVE (in a real rendered browser preview, not just by
    # reading the markup -- an assertion on the string alone can't catch this class of bug,
    # same as the identical `.time-in-zone__fill` bug this codebase's own share pages already
    # hit) to collapse to 0 rendered width inside _bar_rows' own containing <td>, which has no
    # width of its own for the outer table's auto layout to resolve a percentage against.
    # Literal pixel widths have nothing to resolve, so they render correctly regardless of the
    # parent's own layout algorithm.
    bar_html = _bar(40)
    assert 'width="40%"' not in bar_html
    assert f'width="{round(_BAR_TRACK_PX * 0.4)}"' in bar_html
    assert f'width="{_BAR_TRACK_PX - round(_BAR_TRACK_PX * 0.4)}"' in bar_html


def test_bar_at_0_and_100_percent_draws_only_one_segment() -> None:
    empty = _bar(0)
    assert f'width="{_BAR_TRACK_PX}"' in empty
    assert empty.count("<td") == 1  # only the track, no zero-width fill segment

    full = _bar(100)
    assert f'width="{_BAR_TRACK_PX}"' in full
    assert full.count("<td") == 1  # only the fill, no zero-width track segment


def test_clock_hm_drops_a_trailing_00_seconds_only_when_there_is_an_hours_component() -> None:
    assert _clock_hm(4 * 3600) == "4:00"  # marathon goal: 4:00:00 -> 4:00
    assert _clock_hm(4 * 3600 + 5 * 60) == "4:05"  # 4:05:00 -> 4:05
    assert _clock_hm(4 * 3600 + 5 * 60 + 30) == "4:05:30"  # real seconds precision kept
    assert _clock_hm(22 * 60 + 30) == "22:30"  # no hours component -- kept as-is
    assert _clock_hm(45 * 60) == "45:00"  # no hours component -- kept as-is, not stripped


def test_future_race_goal_and_date_label_match_the_worked_example() -> None:
    # A real marathon (42.195 km) with a 4-hour goal: 14400s / 42.195km = 341.4s/km = 5:41/km.
    race = PlannedRaceLine(
        local_date="2026-12-06",
        name="California International Marathon",
        distance_m=42195.0,
        target_duration_s=4 * 3600.0,
        predicted_duration_s=None,
    )
    assert _future_race_goal(race) == "4:00 goal (5:41 /km)"
    assert _future_race_date_label(race, dt.date(2026, 9, 13)) == ("Sun 06 Dec 2026 (84d)")


def test_future_race_goal_is_none_without_a_target() -> None:
    race = PlannedRaceLine(
        local_date="2026-12-06",
        name="Fun Run",
        distance_m=5000.0,
        target_duration_s=None,
        predicted_duration_s=None,
    )
    assert _future_race_goal(race) is None


def test_future_races_query_is_unbounded_and_excludes_today_and_the_past(
    conn: Connection,
) -> None:
    _race(conn, local_date="2026-09-13", name="Today", distance_m=5000.0)  # excluded: not future
    _race(conn, local_date="2026-09-01", name="Past", distance_m=5000.0)  # excluded: in the past
    _race(conn, local_date="2026-09-20", name="Next Sunday", distance_m=10000.0)
    _race(conn, local_date="2027-06-01", name="Way out", distance_m=42195.0)
    conn.commit()

    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert [r.name for r in report.future_races] == ["Next Sunday", "Way out"]


def test_weekly_email_shows_future_races(conn: Connection) -> None:
    _race(
        conn,
        local_date="2026-12-06",
        name="California International Marathon",
        distance_m=42195.0,
        target_duration_s=4 * 3600.0,
    )
    conn.commit()

    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)

    assert "Future races" in rendered.html and "Future races" in rendered.text
    assert "California International Marathon" in rendered.html
    assert "Sun 06 Dec 2026 (84d)" in rendered.html
    assert "4:00 goal (5:41 /km)" in rendered.html
    assert "Sun 06 Dec 2026 (84d): California International Marathon - 4:00 goal (5:41 /km)" in (
        rendered.text
    )


def test_weekly_email_omits_future_races_section_when_there_are_none(
    conn: Connection,
) -> None:
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)
    assert "Future races" not in rendered.html
    assert "Future races" not in rendered.text


def test_weekly_email_shows_sleep_bar_chart(conn: Connection) -> None:
    _seed_week_rollup(conn)
    _sleep(conn, local_date="2026-09-08", total_sleep_s=7.5 * 3600)
    conn.commit()
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)

    assert "Sleep this week" in rendered.html and "Sleep this week" in rendered.text
    assert "7h 30m" in rendered.html and "7h 30m" in rendered.text


def test_weekly_email_omits_sleep_section_when_theres_no_data(conn: Connection) -> None:
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)
    assert "Sleep this week" not in rendered.html
    assert "Sleep this week" not in rendered.text


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


def test_coming_workout_comment_shown_in_both_html_and_text(conn: Connection) -> None:
    _plan(
        conn,
        local_date="2026-09-15",
        sport="running",
        name="Intervals",
        source_text="Warmup 10m",
        comment="Base-building phase -- keep it aerobic.",
        estimated_duration_s=600.0,
    )
    conn.commit()
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert report.coming_workouts[0].comment == "Base-building phase -- keep it aerobic."

    rendered = render_weekly_email(report)
    assert "Base-building phase -- keep it aerobic." in rendered.html
    assert "Base-building phase -- keep it aerobic." in rendered.text


def test_weekly_email_shows_a_weather_icon_and_range_for_each_coming_day(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_home_location(conn, lat=48.8566, lon=2.3522)
    conn.commit()
    fake_days = [
        ForecastDay(dt.date(2026, 9, 15), 61, 12.0, 18.0),  # Tue: rain
        ForecastDay(dt.date(2026, 9, 18), 0, 10.0, 22.0),  # Fri: clear
    ]
    monkeypatch.setattr("perseverer.email_reports.fetch_forecast", lambda *a, **kw: fake_days)
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)

    # The rain emoji + range for Tuesday, the clear-sky emoji + range for Friday -- both days
    # show up even though neither has a scheduled workout.
    assert "🌧️" in rendered.html and "12-18°C" in rendered.html
    assert "☀️" in rendered.html and "10-22°C" in rendered.html
    assert "🌧️" in rendered.text and "12-18°C" in rendered.text
    assert "☀️" in rendered.text and "10-22°C" in rendered.text


def test_weekly_email_omits_weather_when_no_home_location_is_set(conn: Connection) -> None:
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)
    assert "°C" not in rendered.html
    assert "°C" not in rendered.text


def test_weekly_email_shows_coming_week_notes_and_omits_the_section_when_empty(
    conn: Connection,
) -> None:
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)
    assert "Notes for the coming week" not in rendered.html
    assert "Notes for the coming week" not in rendered.text

    _note(conn, entity_id=COMING_WEEK_START.isoformat(), body="Focus on hill work.")
    conn.commit()
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)
    assert "Notes for the coming week" in rendered.html
    assert "Focus on hill work." in rendered.html
    assert "Notes for the coming week" in rendered.text
    assert "Focus on hill work." in rendered.text


def test_coming_week_race_appears_with_target_and_prediction(conn: Connection) -> None:
    conn.execute(
        performance_daily_rollup.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            local_date="2026-09-01",
            predicted_10k_s=2350.0,
            refreshed_at=dt.datetime(2026, 9, 1),
        )
    )
    _race(
        conn,
        local_date="2026-09-16",
        name="Fall 10K",
        distance_m=10000.0,
        target_duration_s=2400.0,
    )
    # Outside the coming week -- must not appear.
    _race(conn, local_date="2026-10-01", name="Way later", distance_m=5000.0)
    conn.commit()

    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert len(report.coming_races) == 1
    race = report.coming_races[0]
    assert race.name == "Fall 10K"
    assert race.target_duration_s == 2400.0
    assert race.predicted_duration_s == 2350.0

    rendered = render_weekly_email(report)
    assert "Fall 10K" in rendered.html and "Fall 10K" in rendered.text
    assert "Races this week" in rendered.html and "Races this week" in rendered.text
    # Predicted (39:10) is faster than the 40:00 target -- reads as on track.
    assert "on track" in rendered.html


def test_weekly_email_shows_running_bar_chart_and_average_pace(conn: Connection) -> None:
    _seed_week_rollup(conn)
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)

    assert "Running this week" in rendered.html and "Running this week" in rendered.text
    assert "Average pace: 4:40 /km" in rendered.html
    assert "avg pace 4:40 /km" in rendered.text
    # One bar row per weekday, Tue (12.0 km) and Thu (18.0 km) among them.
    assert "12.0 km" in rendered.html and "18.0 km" in rendered.html


def test_weekly_email_shows_running_distance_vs_the_week_before(conn: Connection) -> None:
    _seed_week_rollup(conn)  # 30 km running in the week that just ended
    _activity(
        conn,
        activity_id="w1",
        local_date=WEEK_BEFORE_END,
        sport="running",
        distance_m=20000.0,
        duration_s=6000.0,
    )
    conn.commit()
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)

    assert "30.0 km vs 20.0 km the week before" in rendered.html
    assert "30.0 km vs 20.0 km the week before" in rendered.text


def test_weekly_email_omits_the_week_before_comparison_with_no_prior_data(
    conn: Connection,
) -> None:
    _seed_week_rollup(conn)
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)
    assert "the week before" not in rendered.html
    assert "the week before" not in rendered.text


def test_weekly_email_shows_a_runs_table_with_the_bests_highlighted(conn: Connection) -> None:
    # Tue: 12 km / 3600s (5:00/km). Thu: 18 km / 4800s (4:27/km) -- farthest, fastest, and
    # longest all land on Thursday in this fixture.
    _seed_week_rollup(conn)
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)

    assert "Runs this week" in rendered.html and "Runs this week" in rendered.text
    assert "Tue 8" in rendered.html
    assert "Thu 10" in rendered.html
    # Thursday's own distance/pace/duration cells are the bold/accent-colored ones.
    highlighted_18km = f'color:{_ACCENT};font-weight:700">18.0 km'
    highlighted_pace = f'color:{_ACCENT};font-weight:700">4:27 /km'
    highlighted_duration = f'color:{_ACCENT};font-weight:700">1h 20m'
    assert highlighted_18km in rendered.html
    assert highlighted_pace in rendered.html
    assert highlighted_duration in rendered.html
    # Tuesday's own cells, not being any of the week's bests, are plain (not accent-colored).
    assert f'color:{_ACCENT};font-weight:700">12.0 km' not in rendered.html
    # Plaintext marks the same bests with a plain-language tag instead of color.
    assert "(farthest, fastest, longest)" in rendered.text


def test_weekly_email_highlights_ties_on_every_tied_run(conn: Connection) -> None:
    # Two runs, same distance/pace/duration -- both are "the week's fastest run" equally.
    _activity(
        conn,
        activity_id="r1",
        local_date="2026-09-08",
        sport="running",
        distance_m=10000.0,
        duration_s=3000.0,
    )
    _activity(
        conn,
        activity_id="r2",
        local_date="2026-09-10",
        sport="running",
        distance_m=10000.0,
        duration_s=3000.0,
    )
    conn.commit()
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)
    assert rendered.html.count(f'color:{_ACCENT};font-weight:700">10.0 km') == 2


def test_weekly_email_omits_runs_table_when_theres_no_running(conn: Connection) -> None:
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)
    assert "Runs this week" not in rendered.html
    assert "Runs this week" not in rendered.text


def test_weekly_email_shows_steps_bar_chart(conn: Connection) -> None:
    _seed_week_rollup(conn)
    _steps(
        conn,
        local_date="2026-09-08",
        metric_key="garmin.daily_summary.totalSteps",
        value_sum=9432.0,
    )
    conn.commit()
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)

    assert "Steps this week" in rendered.html and "Steps this week" in rendered.text
    assert "9,432" in rendered.html and "9,432" in rendered.text


def test_weekly_email_omits_running_and_steps_sections_when_theres_no_data(
    conn: Connection,
) -> None:
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    rendered = render_weekly_email(report)

    assert "Running this week" not in rendered.html
    assert "Running this week" not in rendered.text
    assert "Runs this week" not in rendered.html
    assert "Runs this week" not in rendered.text
    assert "Steps this week" not in rendered.html
    assert "Steps this week" not in rendered.text


def test_no_coming_races_omits_the_races_section(conn: Connection) -> None:
    _seed_week_rollup(conn)
    report = build_weekly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=SUNDAY)
    assert report.coming_races == []
    rendered = render_weekly_email(report)
    assert "Races this week" not in rendered.html
    assert "Races this week" not in rendered.text


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
    report = build_monthly_report(conn, athlete_id=DEFAULT_ATHLETE_ID, today=dt.date(2026, 9, 30))
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


def test_send_report_email_starttls_path(conn: Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_week_rollup(conn)
    monkeypatch.setattr("perseverer.email_delivery.smtplib.SMTP", _FakeSMTP)
    settings = Settings(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="me@example.com",
        smtp_password="secret",
        smtp_from="me@example.com",
        smtp_security="starttls",
    )
    send_report_email(settings, conn, athlete_id=DEFAULT_ATHLETE_ID, kind="weekly", today=SUNDAY)
    smtp = _FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.example.com", 587)
    assert smtp.started_tls is True
    assert smtp.logged_in == ("me@example.com", "secret")
    assert smtp.sent is not None
    assert smtp.sent["To"] == "jerome@example.com"  # type: ignore[index]


def test_send_report_email_ssl_path(conn: Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_week_rollup(conn)
    monkeypatch.setattr("perseverer.email_delivery.smtplib.SMTP_SSL", _FakeSMTP)
    settings = Settings(
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_username="me@example.com",
        smtp_password="secret",
        smtp_from="me@example.com",
        smtp_security="ssl",
    )
    send_report_email(settings, conn, athlete_id=DEFAULT_ATHLETE_ID, kind="weekly", today=SUNDAY)
    smtp = _FakeSMTP.instances[0]
    assert smtp.port == 465
    assert smtp.started_tls is False
    assert smtp.logged_in == ("me@example.com", "secret")


def test_send_report_email_raises_without_a_recipient(conn: Connection, engine: Engine) -> None:
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
