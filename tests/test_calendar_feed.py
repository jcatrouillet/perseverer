"""Tests for calendar_feed.py: token generate/hash/resolve, and build_ics_feed's VEVENT
rendering (timed vs. all-day, per-sport-tier description, UID stability, empty feed).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, cast

from icalendar import Calendar, vDDDTypes
from icalendar.cal import Component
from sqlalchemy import Engine

from perseverer.calendar_feed import (
    build_ics_feed,
    generate_feed_token,
    hash_feed_token,
    resolve_feed_token,
)
from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, metadata
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.planned_workouts import PlannedStepLike, save_planned_workout


def _engine(tmp_path: Path, *, timezone: str = "America/Los_Angeles") -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone=timezone,
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    return engine


def _prop_dt(event: Component, key: str) -> Any:
    """event[key] is typed as a broad union of every icalendar property-value class -- only
    vDDDTypes (date/datetime/duration-valued properties) actually carries `.dt`, so narrow to it
    explicitly rather than fighting mypy's strict-mode union at every call site. Callers cast
    (or plain-compare, which doesn't care) the result to whichever of date/datetime they expect."""
    return cast(vDDDTypes, event[key]).dt


class TestTokens:
    def test_generate_feed_token_produces_distinct_values(self) -> None:
        assert generate_feed_token() != generate_feed_token()

    def test_hash_feed_token_is_deterministic(self) -> None:
        token = generate_feed_token()
        assert hash_feed_token(token) == hash_feed_token(token)

    def test_resolve_feed_token_none_when_unset(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            assert resolve_feed_token(conn, "anything") is None

    def test_resolve_feed_token_matches_and_returns_timezone(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, timezone="Europe/Paris")
        raw_token = generate_feed_token()
        with engine.connect() as conn:
            conn.execute(
                athlete.update()
                .where(athlete.c.id == DEFAULT_ATHLETE_ID)
                .values(
                    calendar_feed_token_hash=hash_feed_token(raw_token),
                    calendar_feed_created_at=dt.datetime.now(dt.UTC),
                )
            )
            conn.commit()
            assert resolve_feed_token(conn, raw_token) == (DEFAULT_ATHLETE_ID, "Europe/Paris")
            assert resolve_feed_token(conn, "wrong-token") is None


class TestBuildIcsFeed:
    def test_empty_feed_has_no_events_but_is_valid(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            ics_bytes = build_ics_feed(conn, athlete_id=DEFAULT_ATHLETE_ID, timezone_name="UTC")
        cal = Calendar.from_ical(ics_bytes)
        assert cal.walk("VEVENT") == []
        assert str(cal.get("prodid")) == "-//Perseverer//Planned Workouts//EN"

    def test_timed_running_workout_uses_athlete_timezone(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, timezone="America/Los_Angeles")
        with engine.connect() as conn:
            save_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-10",
                sport="running",
                name="Easy run",
                source_text="30m easy",
                scheduled_time="07:00",
            )
            conn.commit()
            ics_bytes = build_ics_feed(
                conn, athlete_id=DEFAULT_ATHLETE_ID, timezone_name="America/Los_Angeles"
            )
        cal = Calendar.from_ical(ics_bytes)
        events = cal.walk("VEVENT")
        assert len(events) == 1
        event = events[0]
        assert str(event["summary"]) == "Running: Easy run"
        start = cast(dt.datetime, _prop_dt(event, "dtstart"))
        assert start.year == 2026 and start.month == 9 and start.day == 10
        assert start.hour == 7 and start.minute == 0
        assert str(start.tzinfo) == "America/Los_Angeles"
        end = cast(dt.datetime, _prop_dt(event, "dtend"))
        assert (end - start) == dt.timedelta(minutes=30)
        assert "UID" in event

    def test_workout_level_comment_is_prepended_to_the_description(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            save_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-10",
                sport="running",
                name="Easy run",
                source_text="30m easy",
                comment="Legs still sore -- cut it short if needed.",
            )
            conn.commit()
            ics_bytes = build_ics_feed(conn, athlete_id=DEFAULT_ATHLETE_ID, timezone_name="UTC")
        cal = Calendar.from_ical(ics_bytes)
        description = str(cal.walk("VEVENT")[0]["description"])
        assert description.startswith("Legs still sore -- cut it short if needed.")
        assert "30m easy" in description

    def test_no_comment_leaves_the_description_unchanged(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            save_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-10",
                sport="running",
                name="Easy run",
                source_text="30m easy",
            )
            conn.commit()
            ics_bytes = build_ics_feed(conn, athlete_id=DEFAULT_ATHLETE_ID, timezone_name="UTC")
        cal = Calendar.from_ical(ics_bytes)
        assert str(cal.walk("VEVENT")[0]["description"]) == "30m easy"

    def test_all_day_yoga_workout_has_no_time(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            save_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-11",
                sport="yoga",
                name=None,
                source_text="Vinyasa flow",
                duration_minutes=60,
            )
            conn.commit()
            ics_bytes = build_ics_feed(conn, athlete_id=DEFAULT_ATHLETE_ID, timezone_name="UTC")
        cal = Calendar.from_ical(ics_bytes)
        event = cal.walk("VEVENT")[0]
        assert str(event["summary"]) == "Yoga"
        assert _prop_dt(event, "dtstart") == dt.date(2026, 9, 11)
        assert _prop_dt(event, "dtend") == dt.date(2026, 9, 12)
        assert str(event["description"]) == "Vinyasa flow"

    def test_running_without_scheduled_time_falls_back_to_default_duration(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            # Empty source_text -> estimated_duration_s ends up 0.0/None, so the timed event
            # (scheduled_time set) must fall back to the 60-minute default rather than a
            # zero-length event.
            save_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-12",
                sport="running",
                name=None,
                source_text="",
                scheduled_time="06:30",
            )
            conn.commit()
            ics_bytes = build_ics_feed(conn, athlete_id=DEFAULT_ATHLETE_ID, timezone_name="UTC")
        cal = Calendar.from_ical(ics_bytes)
        event = cal.walk("VEVENT")[0]
        start = cast(dt.datetime, _prop_dt(event, "dtstart"))
        end = cast(dt.datetime, _prop_dt(event, "dtend"))
        assert (end - start) == dt.timedelta(hours=1)

    def test_hiit_workout_renders_exercise_steps_as_description(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        steps = [
            PlannedStepLike(
                0, "reps", None, None, None, None, None, None, None, None, None, None, None,
                duration_reps=12, exercise_category="SQUAT", exercise_name="", weight_kg=40.0,
            ),
            # The repeat marker's own step_index comes AFTER the block it covers
            # (repeat_from_step=0..step_index-1), same convention planned_workout_step/
            # activity_workout_step both use throughout this codebase.
            PlannedStepLike(
                1, "repeat_until_steps_cmplt", None, None, None, None, None, None, None, None,
                None, 0, 3,
            ),
        ]
        with engine.connect() as conn:
            save_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-13",
                sport="hiit",
                name="Leg day",
                source_text=None,
                steps=steps,
            )
            conn.commit()
            ics_bytes = build_ics_feed(conn, athlete_id=DEFAULT_ATHLETE_ID, timezone_name="UTC")
        cal = Calendar.from_ical(ics_bytes)
        event = cal.walk("VEVENT")[0]
        assert str(event["summary"]) == "HIIT: Leg day"
        description = str(event["description"])
        assert "3x:" in description
        assert "Squat" in description
        assert "12 reps" in description
        assert "40kg" in description

    def test_running_inline_comment_shows_up_verbatim_in_description(self, tmp_path: Path) -> None:
        # running/yoga/bouldering need no calendar_feed.py code of their own for comments --
        # source_text already carries the raw text, inline "#" comments included, straight
        # through to DESCRIPTION verbatim.
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            save_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-16",
                sport="running",
                name=None,
                source_text="30m easy # new shoes today",
            )
            conn.commit()
            ics_bytes = build_ics_feed(conn, athlete_id=DEFAULT_ATHLETE_ID, timezone_name="UTC")
        cal = Calendar.from_ical(ics_bytes)
        event = cal.walk("VEVENT")[0]
        assert str(event["description"]) == "30m easy # new shoes today"

    def test_hiit_step_comment_appended_to_that_steps_own_line(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        steps = [
            PlannedStepLike(
                0, "reps", None, None, None, None, None, None, None, None, None, None, None,
                duration_reps=10, exercise_category="PUSH_UP", exercise_name="", weight_kg=None,
                comment="Bring the resistance bands",
            ),
        ]
        with engine.connect() as conn:
            save_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-17",
                sport="hiit",
                name=None,
                source_text=None,
                steps=steps,
            )
            conn.commit()
            ics_bytes = build_ics_feed(conn, athlete_id=DEFAULT_ATHLETE_ID, timezone_name="UTC")
        cal = Calendar.from_ical(ics_bytes)
        event = cal.walk("VEVENT")[0]
        description = str(event["description"])
        assert "Push Up" in description
        assert description.endswith("Bring the resistance bands")

    def test_hiit_group_comment_appended_to_its_repeat_header(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        steps = [
            PlannedStepLike(
                0, "reps", None, None, None, None, None, None, None, None, None, None, None,
                duration_reps=12, exercise_category="SQUAT", exercise_name="", weight_kg=None,
            ),
            PlannedStepLike(
                1, "repeat_until_steps_cmplt", None, None, None, None, None, None, None, None,
                None, 0, 3, comment="superset, no rest between rounds",
            ),
        ]
        with engine.connect() as conn:
            save_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-18",
                sport="hiit",
                name=None,
                source_text=None,
                steps=steps,
            )
            conn.commit()
            ics_bytes = build_ics_feed(conn, athlete_id=DEFAULT_ATHLETE_ID, timezone_name="UTC")
        cal = Calendar.from_ical(ics_bytes)
        event = cal.walk("VEVENT")[0]
        description = str(event["description"])
        assert "3x: superset, no rest between rounds" in description

    def test_uid_is_stable_and_unique_per_workout(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            save_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-14",
                sport="yoga",
                name=None,
                source_text=None,
                duration_minutes=45,
            )
            save_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-15",
                sport="yoga",
                name=None,
                source_text=None,
                duration_minutes=45,
            )
            conn.commit()
            ics_bytes = build_ics_feed(conn, athlete_id=DEFAULT_ATHLETE_ID, timezone_name="UTC")
        cal = Calendar.from_ical(ics_bytes)
        uids = {str(e["uid"]) for e in cal.walk("VEVENT")}
        assert len(uids) == 2
