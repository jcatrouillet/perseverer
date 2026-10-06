"""Public iCalendar (.ics) feed of the athlete's own `planned_workout` calendar, so it can be
subscribed to from Google Calendar (Settings > Add calendar > From URL) or any other RFC
5545-reading client. A parallel mechanism to `sharing.py`'s `share_link`, not a third kind grafted
onto it -- this is one standing, athlete-scoped secret (same replace-on-rotate shape as
`athlete.api_key_hash`), not a growing history of one-off shares, and the response is
`text/calendar`, not HTML. See `api/routers/calendar_feed.py` (the public GET route) and
`api/routers/settings.py` (the authenticated create/rotate/revoke routes).

Deliberately never includes completed activities -- only `planned_workout`, the athlete's own
authored training plan (see `planned_workouts.py` / ADR 0015). Google Calendar polls a subscribed
feed URL roughly every 8-24h, not in real time, so this is called fresh on every request with no
caching -- `planned_workout` is a small table with no rollup precedent of its own already (unlike
the ~1,400+ day activity history tables this project does precompute).

Event rendering, per sport tier (`planned_workouts.py::PLACEHOLDER_SPORTS`/`EXERCISE_SPORTS`):
running/yoga/bouldering already have a human-readable `source_text` (workout syntax or freeform
notes respectively -- used verbatim as the event DESCRIPTION); hiit/strength_training never has
`source_text` at all ("steps arrive already-structured, never parsed from text" -- ADR 0015), so
`_render_exercise_description` is a purpose-built renderer for that one case rather than reusing
`workout_syntax.py::steps_to_source_text` (shaped for *recorded*, pace-only steps -- a different
domain from `planned_workout_step`'s reps/exercise/weight fields).

Also includes `planned_race` rows (planned_races.py) -- a race is a calendar event too, just a
different table (no step model, no Garmin push), so it gets its own small `_build_race_event`
alongside `_build_event` rather than a third sport tier grafted onto the workout renderer.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event
from sqlalchemy import Connection, Row, select

from perseverer.db.schema import athlete, planned_race, planned_workout, planned_workout_step
from perseverer.planned_workouts import EXERCISE_SPORTS

_FEED_TOKEN_BYTES = 32
# Only used when scheduled_time is set but estimated_duration_s isn't -- a labeled judgment call,
# not derived from anywhere.
_DEFAULT_EVENT_DURATION_S = 3600.0
# A race with a known start time but no known finish -- long enough to cover the vast majority
# of amateur race durations across every distance this app lets an athlete log.
_DEFAULT_RACE_EVENT_DURATION_S = 4.0 * 3600.0

_SPORT_LABELS: dict[str, str] = {
    "running": "Running",
    "yoga": "Yoga",
    "bouldering": "Bouldering",
    "hiit": "HIIT",
    "strength_training": "Strength training",
}


def generate_feed_token() -> str:
    return secrets.token_urlsafe(_FEED_TOKEN_BYTES)


def hash_feed_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def resolve_feed_token(conn: Connection, raw_token: str) -> tuple[str, str] | None:
    """(athlete_id, timezone) owning this token, or None if it doesn't match any --
    deliberately indistinguishable from "revoked", same posture as
    sharing.py::resolve_share_token. Returns the athlete's own timezone alongside its id since
    build_ics_feed always needs both and this is the one query already looking the athlete up by
    token."""
    token_hash = hash_feed_token(raw_token)
    row = conn.execute(
        select(athlete.c.id, athlete.c.timezone).where(
            athlete.c.calendar_feed_token_hash == token_hash
        )
    ).fetchone()
    return (row.id, row.timezone) if row is not None else None


def _sport_label(sport: str) -> str:
    return _SPORT_LABELS.get(sport, sport.replace("_", " ").title())


def _humanize_exercise_token(token: str) -> str:
    return token.replace("_", " ").title()


def _format_step_amount(
    duration_type: str | None, time_s: float | None, reps: int | None
) -> str | None:
    if duration_type == "reps" and reps is not None:
        return f"{reps} reps"
    if duration_type == "time" and time_s is not None:
        total = round(time_s)
        if total < 60:
            return f"{total}s"
        if total % 60 == 0:
            return f"{total // 60}m"
        return f"{total // 60}m{total % 60}s"
    return None


def _step_line(step: Row) -> str:  # type: ignore[type-arg]
    amount = _format_step_amount(step.duration_type, step.duration_time_s, step.duration_reps)
    if step.exercise_name:
        label = _humanize_exercise_token(step.exercise_name)
    elif step.exercise_category:
        label = _humanize_exercise_token(step.exercise_category)
    elif step.intensity == "rest":
        label = "Rest"
    else:
        label = "Exercise"
    line = f"{label} — {amount}" if amount else label
    if step.weight_kg:
        line += f" @ {step.weight_kg:g}kg"
    if step.comment:
        line += f" — {step.comment}"
    return line


def _render_exercise_description(steps: list[Row]) -> str:  # type: ignore[type-arg]
    """One line per real exercise/rest step (each with its own comment, when set, appended --
    see _step_line), in step_index order, with an "Nx:" header (same "<N>x" repeat-block wording
    workout_syntax.py's own running text syntax uses, its own comment embedded when the athlete
    set one on the set/group) inserted ahead of any block a repeat_until_steps_cmplt marker
    covers -- same consumed-step idiom workout_syntax.py::steps_to_source_text uses for the
    analogous running-syntax case."""
    sorted_steps = sorted(steps, key=lambda s: s.step_index)
    # first step_index of the block -> (repeat_count, the marker's own comment)
    repeat_headers: dict[int, tuple[int, str | None]] = {}
    consumed: set[int] = set()
    for s in sorted_steps:
        if s.duration_type == "repeat_until_steps_cmplt" and s.repeat_from_step is not None:
            repeat_headers[s.repeat_from_step] = (s.repeat_count or 1, s.comment)
            consumed.add(s.step_index)

    lines: list[str] = []
    for s in sorted_steps:
        if s.step_index in consumed:
            continue
        if s.step_index in repeat_headers:
            count, group_comment = repeat_headers[s.step_index]
            header = f"{count}x: {group_comment}" if group_comment else f"{count}x:"
            lines.append(header)
        lines.append(_step_line(s))
    return "\n".join(lines)


def _event_description(row: Row, steps: list[Row]) -> str | None:  # type: ignore[type-arg]
    if row.sport in EXERCISE_SPORTS:
        structured = _render_exercise_description(steps) or None
    else:
        # running, yoga, bouldering: source_text is already the athlete's own human-readable text
        # (workout syntax for running, freeform notes for yoga/bouldering -- see
        # planned_workouts.py::save_planned_workout's own docstring). Any inline "#" comment the
        # athlete added to a line is already part of this raw text verbatim -- no extra handling
        # needed here.
        structured = row.source_text or None
    # The workout's own general-guidance comment (running/hiit/strength_training only, distinct
    # from a step's own comment already folded into `structured` above) -- read before any step,
    # so it goes first here too.
    if row.comment:
        return f"{row.comment}\n\n{structured}" if structured else row.comment
    return structured


def _build_event(row: Row, steps: list[Row], timezone_name: str) -> Event:  # type: ignore[type-arg]
    ev = Event()
    ev.add("uid", f"planned-workout-{row.id}@perseverer")
    summary = _sport_label(row.sport)
    if row.name:
        summary = f"{summary}: {row.name}"
    ev.add("summary", summary)
    ev.add("dtstamp", datetime.now(UTC))

    local_date = date.fromisoformat(row.local_date)
    if row.scheduled_time:
        hour, minute = (int(p) for p in row.scheduled_time.split(":"))
        start = datetime(
            local_date.year,
            local_date.month,
            local_date.day,
            hour,
            minute,
            tzinfo=ZoneInfo(timezone_name),
        )
        duration_s = row.estimated_duration_s or _DEFAULT_EVENT_DURATION_S
        ev.add("dtstart", start)
        ev.add("dtend", start + timedelta(seconds=duration_s))
    else:
        ev.add("dtstart", local_date)
        ev.add("dtend", local_date + timedelta(days=1))

    description = _event_description(row, steps)
    if description:
        ev.add("description", description)
    return ev


def _clock_duration(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _build_race_event(row: Row, timezone_name: str) -> Event:  # type: ignore[type-arg]
    ev = Event()
    ev.add("uid", f"planned-race-{row.id}@perseverer")
    ev.add("summary", f"🏁 {row.name}")
    ev.add("dtstamp", datetime.now(UTC))

    local_date = date.fromisoformat(row.local_date)
    if row.scheduled_time:
        hour, minute = (int(p) for p in row.scheduled_time.split(":"))
        start = datetime(
            local_date.year,
            local_date.month,
            local_date.day,
            hour,
            minute,
            tzinfo=ZoneInfo(timezone_name),
        )
        duration_s = row.target_duration_s or _DEFAULT_RACE_EVENT_DURATION_S
        ev.add("dtstart", start)
        ev.add("dtend", start + timedelta(seconds=duration_s))
    else:
        ev.add("dtstart", local_date)
        ev.add("dtend", local_date + timedelta(days=1))

    description = f"{row.distance_m / 1000:.1f} km"
    if row.target_duration_s:
        description += f" · target {_clock_duration(row.target_duration_s)}"
    ev.add("description", description)
    return ev


def build_ics_feed(conn: Connection, *, athlete_id: str, timezone_name: str) -> bytes:
    """Every planned_workout AND planned_race row for this athlete, all dates, one VEVENT each,
    as a full VCALENDAR. Two bulk queries + Python grouping (same pattern performance_rollup.py
    already uses) rather than one query per workout."""
    workouts = conn.execute(
        select(planned_workout).where(planned_workout.c.athlete_id == athlete_id)
    ).fetchall()

    steps_by_workout: dict[int, list[Row]] = {}  # type: ignore[type-arg]
    if workouts:
        step_rows = conn.execute(
            select(planned_workout_step).where(
                planned_workout_step.c.planned_workout_id.in_([w.id for w in workouts])
            )
        ).fetchall()
        for s in step_rows:
            steps_by_workout.setdefault(s.planned_workout_id, []).append(s)

    races = conn.execute(
        select(planned_race).where(planned_race.c.athlete_id == athlete_id)
    ).fetchall()

    cal = Calendar()
    cal.add("prodid", "-//Perseverer//Planned Workouts//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Perseverer — Planned Workouts")
    cal.add("method", "PUBLISH")
    for row in workouts:
        cal.add_component(_build_event(row, steps_by_workout.get(row.id, []), timezone_name))
    for race_row in races:
        cal.add_component(_build_race_event(race_row, timezone_name))
    # Emits a real VTIMEZONE block (DST rules included) for any TZID actually used above --
    # verified empirically against the installed icalendar version (7.3.0): without this call,
    # DTSTART/DTEND carry a bare TZID= parameter with no accompanying VTIMEZONE definition, which
    # is technically under-specified per RFC 5545 even though widely-used clients (Google
    # Calendar, Apple Calendar) tolerate a bare well-known IANA TZID in practice.
    cal.add_missing_timezones()
    return bytes(cal.to_ical())
