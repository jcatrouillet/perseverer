"""Weekly / monthly training-report emails -- opt-in per athlete
(`athlete_email_report_config`), delivered via the deployment's shared SMTP relay
(`email_delivery.send_email`, `PERSEVERER_SMTP_*`), scheduled from `worker/main.py`.

- **Weekly** (Sunday 18:00 local): the Mon--Sun week that just ended (activity totals + a
  per-sport breakdown) plus the coming Mon--Sun week's planned workouts and any races
  (`planned_race`, planned_races.py) scheduled in it, target vs. predicted finish time included.
- **Monthly** (month's last day, 18:00): the calendar month that just ended -- totals only, no
  planned-workout/race section.

Headline totals come straight from `period_rollup` (the platform's sanctioned aggregate --
already consistent with the calendar grid); the per-sport split is one extra bounded `activity`
query since the rollup doesn't store it. Running planned workouts are enriched with the same
distance/duration/load estimate the calendar UI shows (`planned_workout_stats.estimate_workout`
over a fresh `workout_syntax.parse_workout_syntax` of `source_text`).

The HTML is deliberately email-client-safe: one inline-styled table layout, a light palette
only, no `<style>` block, no external images or links -- with a plaintext alternative alongside.
A send reads the local DB, whose newest Garmin data is from that morning's 04:15 sync, so the
send day's own activities may not be counted yet -- the footer says as much.
"""

from __future__ import annotations

import calendar as _calendar
import html
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import Connection, and_, func, select

from perseverer.config import Settings
from perseverer.db.schema import (
    activity,
    athlete,
    athlete_email_report_config,
    athlete_hr_zone_config,
    athlete_running_load_config,
    health_metric_daily_rollup,
    period_rollup,
    planned_race,
    planned_workout,
    sleep_session,
)
from perseverer.email_delivery import send_email
from perseverer.planned_races import predicted_duration_s_for_distance
from perseverer.planned_workout_stats import estimate_workout
from perseverer.workout_syntax import parse_workout_syntax

logger = logging.getLogger(__name__)

ReportKind = Literal["weekly", "monthly"]

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_SPORT_LABELS = {
    "running": "Running",
    "trail_running": "Trail running",
    "cycling": "Cycling",
    "walking": "Walking",
    "hiking": "Hiking",
    "swimming": "Swimming",
    "rowing": "Rowing",
    "strength_training": "Strength training",
    "hiit": "HIIT",
    "yoga": "Yoga",
    "bouldering": "Bouldering",
    "rock_climbing": "Climbing",
}


def _sport_label(sport: str) -> str:
    return _SPORT_LABELS.get(sport, sport.replace("_", " ").capitalize())


# FIT's "training" sport is a generic container for indoor cardio/strength/mindfulness work
# (yoga, strength training, breathwork) -- straight port of frontend/src/yearStats.ts::
# displaySport (same GENERIC_CONTAINER_SPORTS substitution, duplicated rather than imported --
# see sharing.py::_display_sport for the identical precedent). Without this, a recorded yoga
# session (sport="training", sub_sport="yoga") showed up in the "By sport" breakdown labeled
# "Training" instead of "Yoga" -- Garmin Connect itself never shows "Training" as a category.
def _display_sport(sport: str, sub_sport: str | None) -> str:
    if sport == "training" and sub_sport:
        return sub_sport
    return sport


# Same alias list/priority as api/routers/health.py::LOGICAL_METRICS["steps"] -- duplicated
# rather than imported to keep this module from depending on the API layer (same precedent
# insights/engine.py's own _RESTING_HR_ALIASES already established).
_STEPS_ALIASES = ("garmin.daily_summary.totalSteps", "garmin.export.UDSFile.totalSteps")


# --- report models ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodTotals:
    activity_count: int
    active_days: int
    distance_m: float | None
    moving_duration_s: float | None
    elevation_gain_m: float | None
    calories: float | None
    sleep_total_s: float | None


@dataclass(frozen=True)
class SportTotals:
    sport: str
    count: int
    distance_m: float | None
    duration_s: float | None


@dataclass(frozen=True)
class DailyMetricPoint:
    local_date: str
    value: float | None  # None (not 0) when there's genuinely nothing recorded that day


@dataclass(frozen=True)
class PlannedWorkoutLine:
    local_date: str
    sport: str
    name: str | None
    scheduled_time: str | None
    estimate_line: str | None  # running only, e.g. "5.8 km · 34 min · Load 36"
    comment: str | None  # the workout's own general-guidance note, read before any step


@dataclass(frozen=True)
class PlannedRaceLine:
    local_date: str
    name: str
    distance_m: float
    target_duration_s: float | None
    predicted_duration_s: float | None  # standard distances only, see planned_races.py


@dataclass(frozen=True)
class WeeklyReport:
    athlete_name: str
    today: date  # the Sunday the job fires on -- also prev_end, kept explicit for clarity
    prev_start: date
    prev_end: date
    coming_start: date
    coming_end: date
    totals: PeriodTotals
    sports: list[SportTotals]
    running_distance_by_day: list[DailyMetricPoint]  # Mon..Sun of prev_start..prev_end
    avg_running_pace_s_per_km: float | None
    steps_by_day: list[DailyMetricPoint]  # Mon..Sun of prev_start..prev_end
    sleep_hours_by_day: list[DailyMetricPoint]  # Mon..Sun of prev_start..prev_end, in hours
    coming_workouts: list[PlannedWorkoutLine]
    coming_races: list[PlannedRaceLine]  # races in the coming week only
    future_races: list[PlannedRaceLine]  # every race after today, however far out


@dataclass(frozen=True)
class MonthlyReport:
    athlete_name: str
    month_label: str
    month_start: date
    month_end: date
    totals: PeriodTotals
    sports: list[SportTotals]


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


# --- queries --------------------------------------------------------------------------------


def _totals_from_rollup(
    conn: Connection, athlete_id: str, period_type: str, period_start: str
) -> PeriodTotals:
    row = conn.execute(
        select(period_rollup).where(
            and_(
                period_rollup.c.athlete_id == athlete_id,
                period_rollup.c.period_type == period_type,
                period_rollup.c.period_start == period_start,
            )
        )
    ).fetchone()
    if row is None:
        return PeriodTotals(0, 0, None, None, None, None, None)
    return PeriodTotals(
        activity_count=row.activity_count,
        active_days=row.activity_days_count,
        distance_m=row.activity_distance_m,
        moving_duration_s=row.activity_moving_duration_s,
        elevation_gain_m=row.activity_elevation_gain_m,
        calories=row.activity_calories,
        sleep_total_s=row.sleep_total_s,
    )


def _sport_breakdown(
    conn: Connection, athlete_id: str, start: str, end: str
) -> list[SportTotals]:
    # Grouped in Python by _display_sport, not a SQL GROUP BY on the raw activity.c.sport column
    # -- a recorded yoga/strength/breathwork session's real type lives in sub_sport, not sport
    # (sport is just Garmin's generic "training" container for all three). A week's/month's worth
    # of activities is small enough that this costs nothing over the SQL aggregate it replaces.
    rows = conn.execute(
        select(
            activity.c.sport,
            activity.c.sub_sport,
            activity.c.distance_m,
            func.coalesce(activity.c.moving_duration_s, activity.c.duration_s).label(
                "duration_s"
            ),
        ).where(
            and_(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.local_date >= start,
                activity.c.local_date <= end,
            )
        )
    ).fetchall()

    counts: dict[str, int] = {}
    distances: dict[str, float | None] = {}
    durations: dict[str, float | None] = {}
    for r in rows:
        key = _display_sport(r.sport, r.sub_sport)
        counts[key] = counts.get(key, 0) + 1
        if r.distance_m is not None:
            distances[key] = (distances.get(key) or 0.0) + r.distance_m
        if r.duration_s is not None:
            durations[key] = (durations.get(key) or 0.0) + r.duration_s

    return sorted(
        (
            SportTotals(
                sport=key, count=n, distance_m=distances.get(key), duration_s=durations.get(key)
            )
            for key, n in counts.items()
        ),
        key=lambda s: (-(s.distance_m or 0.0), -s.count),
    )


def _daily_points(start: str, end: str, values: dict[str, float]) -> list[DailyMetricPoint]:
    """One point per calendar day from `start` to `end` inclusive, in order -- `values` need not
    cover every day; a missing date becomes `None` (never a fabricated 0), same "nothing
    recorded" convention as `SportTotals`'s own None fields."""
    points: list[DailyMetricPoint] = []
    d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while d <= end_d:
        iso = d.isoformat()
        points.append(DailyMetricPoint(local_date=iso, value=values.get(iso)))
        d += timedelta(days=1)
    return points


def _running_distance_by_day(
    conn: Connection, athlete_id: str, start: str, end: str
) -> list[DailyMetricPoint]:
    # Exact sport == "running" match, not sport_family() -- this project's own established
    # precedent for running-specific stats (RunningStats.tsx, CLAUDE.md), deliberately excluding
    # trail_running/track_running.
    rows = conn.execute(
        select(activity.c.local_date, func.sum(activity.c.distance_m))
        .where(
            and_(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.sport == "running",
                activity.c.local_date >= start,
                activity.c.local_date <= end,
            )
        )
        .group_by(activity.c.local_date)
    ).fetchall()
    return _daily_points(start, end, {r[0]: r[1] for r in rows if r[1] is not None})


def _running_avg_pace_s_per_km(
    conn: Connection, athlete_id: str, start: str, end: str
) -> float | None:
    row = conn.execute(
        select(
            func.sum(activity.c.distance_m).label("distance_m"),
            func.sum(func.coalesce(activity.c.moving_duration_s, activity.c.duration_s)).label(
                "duration_s"
            ),
        ).where(
            and_(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.sport == "running",
                activity.c.local_date >= start,
                activity.c.local_date <= end,
            )
        )
    ).one()
    if not row.distance_m or not row.duration_s:
        return None
    return float(row.duration_s) / (float(row.distance_m) / 1000)


def _steps_by_day(
    conn: Connection, athlete_id: str, start: str, end: str
) -> list[DailyMetricPoint]:
    rows = conn.execute(
        select(
            health_metric_daily_rollup.c.local_date,
            health_metric_daily_rollup.c.metric_key,
            health_metric_daily_rollup.c.value_sum,
        ).where(
            and_(
                health_metric_daily_rollup.c.athlete_id == athlete_id,
                health_metric_daily_rollup.c.metric_key.in_(_STEPS_ALIASES),
                health_metric_daily_rollup.c.local_date >= start,
                health_metric_daily_rollup.c.local_date <= end,
            )
        )
    ).fetchall()
    # First alias (in priority order) with a row for that date wins -- same "namespace
    # switchover never produces two rows for the same day" merge api/routers/health.py::
    # _merge_logical_metric uses, simplified for this module's single-metric, bounded-window need.
    best_priority: dict[str, int] = {}
    values: dict[str, float] = {}
    for r in rows:
        if r.value_sum is None:
            continue
        priority = _STEPS_ALIASES.index(r.metric_key)
        if r.local_date not in best_priority or priority < best_priority[r.local_date]:
            best_priority[r.local_date] = priority
            values[r.local_date] = r.value_sum
    return _daily_points(start, end, values)


def _sleep_hours_by_day(
    conn: Connection, athlete_id: str, start: str, end: str
) -> list[DailyMetricPoint]:
    # sleep_session is unique on (athlete_id, local_date, source), so more than one source could
    # in principle report the same night -- func.max, not sum, picks one sane value per date
    # rather than double-counting (matches this app's own general "don't sum across sources"
    # instinct; a real overlap here has never actually been observed in practice, same as the
    # frontend's own sleepToDailyPoints, which doesn't merge sources at all). Values are hours,
    # not seconds -- DailyMetricPoint.value is meant to be display-ready for _bar_rows.
    rows = conn.execute(
        select(sleep_session.c.local_date, func.max(sleep_session.c.total_sleep_s))
        .where(
            and_(
                sleep_session.c.athlete_id == athlete_id,
                sleep_session.c.local_date >= start,
                sleep_session.c.local_date <= end,
            )
        )
        .group_by(sleep_session.c.local_date)
    ).fetchall()
    values = {r[0]: r[1] / 3600 for r in rows if r[1] is not None}
    return _daily_points(start, end, values)


def _running_thresholds(
    conn: Connection, athlete_id: str
) -> tuple[float | None, float | None, float | None, float | None]:
    """(threshold_pace_sec_per_km, threshold_hr_bpm, max_hr_bpm, resting_hr_bpm) -- mirrors
    api/routers/planned_workouts.py::_fetch_running_load_thresholds without the router
    dependency."""
    load = conn.execute(
        select(athlete_running_load_config.c.threshold_pace_sec_per_km).where(
            athlete_running_load_config.c.athlete_id == athlete_id
        )
    ).fetchone()
    hr = conn.execute(
        select(
            athlete_hr_zone_config.c.threshold_hr_bpm,
            athlete_hr_zone_config.c.max_hr_bpm,
            athlete_hr_zone_config.c.resting_hr_bpm,
        ).where(athlete_hr_zone_config.c.athlete_id == athlete_id)
    ).fetchone()
    return (
        load.threshold_pace_sec_per_km if load is not None else None,
        hr.threshold_hr_bpm if hr is not None else None,
        hr.max_hr_bpm if hr is not None else None,
        hr.resting_hr_bpm if hr is not None else None,
    )


def _coming_workouts(
    conn: Connection, athlete_id: str, start: str, end: str
) -> list[PlannedWorkoutLine]:
    rows = conn.execute(
        select(planned_workout)
        .where(
            and_(
                planned_workout.c.athlete_id == athlete_id,
                planned_workout.c.local_date >= start,
                planned_workout.c.local_date <= end,
            )
        )
        .order_by(planned_workout.c.local_date, planned_workout.c.scheduled_time.nulls_last())
    ).fetchall()
    if not rows:
        return []

    thresholds = _running_thresholds(conn, athlete_id)
    lines: list[PlannedWorkoutLine] = []
    for row in rows:
        estimate_line: str | None = None
        if row.sport == "running" and row.source_text:
            parsed = parse_workout_syntax(row.source_text)
            est = estimate_workout(
                parsed.steps,
                threshold_pace_sec_per_km=thresholds[0],
                threshold_hr_bpm=thresholds[1],
                max_hr_bpm=thresholds[2],
                resting_hr_bpm=thresholds[3],
            )
            parts = []
            if est.distance_m:
                parts.append(f"{est.distance_m / 1000:.1f} km")
            if est.duration_s:
                parts.append(f"{round(est.duration_s / 60)} min")
            if est.load is not None:
                parts.append(f"Load {round(est.load)}")
            estimate_line = " · ".join(parts) or None
        lines.append(
            PlannedWorkoutLine(
                local_date=row.local_date,
                sport=row.sport,
                name=row.name,
                scheduled_time=row.scheduled_time,
                estimate_line=estimate_line,
                comment=row.comment,
            )
        )
    return lines


def _coming_races(conn: Connection, athlete_id: str, start: str, end: str) -> list[PlannedRaceLine]:
    rows = conn.execute(
        select(planned_race)
        .where(
            and_(
                planned_race.c.athlete_id == athlete_id,
                planned_race.c.local_date >= start,
                planned_race.c.local_date <= end,
            )
        )
        .order_by(planned_race.c.local_date, planned_race.c.id)
    ).fetchall()
    return [
        PlannedRaceLine(
            local_date=r.local_date,
            name=r.name,
            distance_m=r.distance_m,
            target_duration_s=r.target_duration_s,
            predicted_duration_s=predicted_duration_s_for_distance(
                conn, athlete_id=athlete_id, distance_m=r.distance_m
            ),
        )
        for r in rows
    ]


def _future_races(conn: Connection, athlete_id: str, after: str) -> list[PlannedRaceLine]:
    """Every race strictly after `after` (today), however far out on the calendar -- unlike
    `_coming_races` above, which is bounded to just the coming Mon..Sun week. Deliberately
    unbounded: a "future races" overview is meant to cover the whole calendar, not just the next
    seven days, and a planned_race row is cheap enough (one athlete's own races, never a lot of
    them) that no LIMIT is worth adding."""
    rows = conn.execute(
        select(planned_race)
        .where(
            and_(
                planned_race.c.athlete_id == athlete_id,
                planned_race.c.local_date > after,
            )
        )
        .order_by(planned_race.c.local_date, planned_race.c.id)
    ).fetchall()
    return [
        PlannedRaceLine(
            local_date=r.local_date,
            name=r.name,
            distance_m=r.distance_m,
            target_duration_s=r.target_duration_s,
            predicted_duration_s=predicted_duration_s_for_distance(
                conn, athlete_id=athlete_id, distance_m=r.distance_m
            ),
        )
        for r in rows
    ]


def _athlete_name(conn: Connection, athlete_id: str) -> str:
    name = conn.execute(
        select(athlete.c.display_name).where(athlete.c.id == athlete_id)
    ).scalar_one_or_none()
    return name or "athlete"


# --- report builders -----------------------------------------------------------------------


def build_weekly_report(conn: Connection, *, athlete_id: str, today: date) -> WeeklyReport:
    """`today` is the Sunday the job fires on. Previous week = the Mon..today (Sun) that just
    ended; coming week = the following Mon..Sun."""
    prev_start = today - timedelta(days=today.weekday())  # Monday of the week containing `today`
    prev_end = prev_start + timedelta(days=6)
    coming_start = prev_start + timedelta(days=7)
    coming_end = coming_start + timedelta(days=6)
    return WeeklyReport(
        athlete_name=_athlete_name(conn, athlete_id),
        today=today,
        prev_start=prev_start,
        prev_end=prev_end,
        coming_start=coming_start,
        coming_end=coming_end,
        totals=_totals_from_rollup(conn, athlete_id, "week", prev_start.isoformat()),
        sports=_sport_breakdown(
            conn, athlete_id, prev_start.isoformat(), prev_end.isoformat()
        ),
        running_distance_by_day=_running_distance_by_day(
            conn, athlete_id, prev_start.isoformat(), prev_end.isoformat()
        ),
        avg_running_pace_s_per_km=_running_avg_pace_s_per_km(
            conn, athlete_id, prev_start.isoformat(), prev_end.isoformat()
        ),
        steps_by_day=_steps_by_day(
            conn, athlete_id, prev_start.isoformat(), prev_end.isoformat()
        ),
        sleep_hours_by_day=_sleep_hours_by_day(
            conn, athlete_id, prev_start.isoformat(), prev_end.isoformat()
        ),
        coming_workouts=_coming_workouts(
            conn, athlete_id, coming_start.isoformat(), coming_end.isoformat()
        ),
        coming_races=_coming_races(
            conn, athlete_id, coming_start.isoformat(), coming_end.isoformat()
        ),
        future_races=_future_races(conn, athlete_id, today.isoformat()),
    )


def build_monthly_report(conn: Connection, *, athlete_id: str, today: date) -> MonthlyReport:
    """`today` is the month's last day the job fires on."""
    month_start = today.replace(day=1)
    last_day = _calendar.monthrange(today.year, today.month)[1]
    month_end = today.replace(day=last_day)
    return MonthlyReport(
        athlete_name=_athlete_name(conn, athlete_id),
        month_label=f"{_MONTH_NAMES[today.month - 1]} {today.year}",
        month_start=month_start,
        month_end=month_end,
        totals=_totals_from_rollup(conn, athlete_id, "month", month_start.isoformat()),
        sports=_sport_breakdown(
            conn, athlete_id, month_start.isoformat(), month_end.isoformat()
        ),
    )


# --- formatting ----------------------------------------------------------------------------


def _km(m: float | None) -> str:
    return "—" if not m else f"{m / 1000:.1f} km"


def _hm(s: float | None) -> str:
    if not s:
        return "—"
    total_min = round(s / 60)
    h, mn = divmod(total_min, 60)
    return f"{h}h {mn:02d}m" if h else f"{mn}m"


def _meters(m: float | None) -> str:
    return "—" if not m else f"{round(m):,} m"


def _clock(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _clock_hm(seconds: float) -> str:
    """Same as _clock, but drops a trailing :00 seconds component when there's an hours part --
    a marathon goal like 14400s reads better as "4:00" than "4:00:00", while a 5K/10K goal (no
    hours component) keeps its own real seconds precision unchanged, e.g. "22:30" or "45:00"."""
    full = _clock(seconds)
    if full.count(":") == 2 and full.endswith(":00"):
        return full.rsplit(":", 1)[0]
    return full


def _count(n: int | None) -> str:
    return "0" if not n else str(n)


def _steps_fmt(n: float | None) -> str:
    return "—" if not n else f"{round(n):,}"


def _sleep_hours_fmt(hours: float | None) -> str:
    # DailyMetricPoint stores hours, but _hm's own "Xh YYm" shape (already used for the "Sleep"
    # stat cell's own weekly total) is the more legible unit for a single night, so convert back
    # to seconds just for formatting.
    return _hm(hours * 3600) if hours else "—"


def _pace(seconds_per_km: float | None) -> str:
    # Same "M:SS /km" shape as sharing.py::_format_pace -- duplicated rather than imported,
    # same small-pure-helper precedent as _display_sport above.
    if not seconds_per_km:
        return "—"
    m, s = divmod(round(seconds_per_km), 60)
    return f"{m}:{s:02d} /km"


def _date_range_label(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}-{end.day}, {end.year}"
    return f"{start.strftime('%b')} {start.day} - {end.strftime('%b')} {end.day}, {end.year}"


_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# --- HTML/text rendering ------------------------------------------------------------------

_BG = "#f4f6f9"
_CARD = "#ffffff"
_BORDER = "#dde2ea"
_TEXT = "#1a1d24"
_MUTED = "#5a6273"
_ACCENT = "#1a73e8"

_FOOTER = "Data as of the last sync — activities from the send day may not be counted yet."


def _stat_cells(totals: PeriodTotals) -> str:
    stats = [
        ("Distance", _km(totals.distance_m)),
        ("Moving time", _hm(totals.moving_duration_s)),
        ("Activities", _count(totals.activity_count)),
        ("Active days", _count(totals.active_days)),
        ("Elevation gain", _meters(totals.elevation_gain_m)),
        ("Sleep", _hm(totals.sleep_total_s)),
    ]
    cells = ""
    for i, (label, value) in enumerate(stats):
        if i % 2 == 0:
            cells += "<tr>"
        cells += (
            f'<td style="padding:10px 14px;border:1px solid {_BORDER};width:50%">'
            f'<div style="font-size:12px;color:{_MUTED};text-transform:uppercase;'
            f'letter-spacing:.04em">{html.escape(label)}</div>'
            f'<div style="font-size:20px;color:{_TEXT};font-weight:600">{html.escape(value)}</div>'
            f"</td>"
        )
        if i % 2 == 1:
            cells += "</tr>"
    return cells


_BAR_TRACK_PX = 380  # fits the 600px card (minus its 24px*2 padding, the day-label and value
# columns, and their own padding) with room to spare.


def _bar(pct: int) -> str:
    """A single horizontal bar as a nested table with literal PIXEL-width `<td>`s -- the
    bulletproof HTML-email "progress bar" technique. Two things were tried first and confirmed
    live (via an actual rendered preview, not just reading the markup) to collapse to 0 width:
    a percentage-width `<div>`, and a percentage-width nested `<table width="100%">`. Both fail
    for the same reason -- the containing `<td>` (in `_bar_rows` below) has no width of its own
    for the outer table's auto layout algorithm to resolve a percentage against, and a `&nbsp;`-
    only cell gives that algorithm no real content to size from either, so it collapses the
    whole column to 0 and every percentage inside it becomes 0px. Literal pixel widths have
    nothing to resolve -- `_BAR_TRACK_PX` fixes the total, and `_bar_rows` gives the containing
    `<td>` that exact same pixel width so the two stay in lockstep.
    """
    pct = max(0, min(100, pct))
    fill_px = round(_BAR_TRACK_PX * pct / 100)
    track_px = _BAR_TRACK_PX - fill_px
    segments = []
    if fill_px > 0:
        radius = "3px" if track_px == 0 else "3px 0 0 3px"
        segments.append(
            f'<td width="{fill_px}" style="background:{_ACCENT};border-radius:{radius};'
            f'height:12px;font-size:0;line-height:0">&nbsp;</td>'
        )
    if track_px > 0:
        radius = "3px" if fill_px == 0 else "0 3px 3px 0"
        segments.append(
            f'<td width="{track_px}" style="background:{_BORDER};border-radius:{radius};'
            f'height:12px;font-size:0;line-height:0">&nbsp;</td>'
        )
    return (
        f'<table role="presentation" width="{_BAR_TRACK_PX}" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse"><tr>{"".join(segments)}</tr></table>'
    )


def _bar_rows(points: list[DailyMetricPoint], value_fmt: Callable[[float | None], str]) -> str:
    """One row per day: a weekday label, a horizontal bar (width proportional to the week's own
    max value, via `_bar`), and the formatted value. A day with nothing recorded draws an
    all-track (no fill) bar and shows value_fmt(None), same "None, never a fabricated 0"
    convention as DailyMetricPoint itself.
    """
    max_value = max((p.value or 0.0) for p in points) or 1.0  # avoid a divide-by-zero week
    rows = ""
    for p in points:
        d = date.fromisoformat(p.local_date)
        pct = round((p.value or 0.0) / max_value * 100)
        rows += (
            "<tr>"
            f'<td style="padding:5px 8px 5px 0;font-size:12px;color:{_MUTED};width:30px">'
            f"{_WEEKDAYS[d.weekday()]}</td>"
            f'<td width="{_BAR_TRACK_PX}" style="padding:5px 0">{_bar(pct)}</td>'
            f'<td style="padding:5px 0 5px 10px;font-size:12px;color:{_TEXT};'
            f'text-align:right;white-space:nowrap">{html.escape(value_fmt(p.value))}</td>'
            "</tr>"
        )
    return rows


def _sport_rows(sports: list[SportTotals]) -> str:
    if not sports:
        return (
            f'<tr><td colspan="3" style="padding:10px 14px;border:1px solid {_BORDER};'
            f'color:{_MUTED}">No activities recorded.</td></tr>'
        )
    rows = ""
    for s in sports:
        rows += (
            f'<tr><td style="padding:8px 14px;border:1px solid {_BORDER};color:{_TEXT}">'
            f"{html.escape(_sport_label(s.sport))}</td>"
            f'<td style="padding:8px 14px;border:1px solid {_BORDER};color:{_TEXT};'
            f'text-align:right">{html.escape(_km(s.distance_m))}</td>'
            f'<td style="padding:8px 14px;border:1px solid {_BORDER};color:{_TEXT};'
            f'text-align:right">{html.escape(_hm(s.duration_s))} · '
            f"{s.count}x</td></tr>"
        )
    return rows


def _workout_rows(workouts: list[PlannedWorkoutLine]) -> str:
    if not workouts:
        return (
            f'<tr><td style="padding:10px 14px;border:1px solid {_BORDER};color:{_MUTED}">'
            f"Nothing scheduled yet.</td></tr>"
        )
    rows = ""
    for w in workouts:
        d = date.fromisoformat(w.local_date)
        day = f"{_WEEKDAYS[d.weekday()]} {d.day}"
        time = f" {html.escape(w.scheduled_time)}" if w.scheduled_time else ""
        title = html.escape(w.name or _sport_label(w.sport))
        comment = (
            f'<div style="font-size:13px;color:{_MUTED};font-style:italic">'
            f"{html.escape(w.comment)}</div>"
            if w.comment
            else ""
        )
        detail = (
            f'<div style="font-size:13px;color:{_MUTED}">{html.escape(w.estimate_line)}</div>'
            if w.estimate_line
            else ""
        )
        rows += (
            f'<tr><td style="padding:8px 14px;border:1px solid {_BORDER}">'
            f'<div style="color:{_TEXT}"><b>{html.escape(day)}</b>{time} — {title} '
            f'<span style="color:{_MUTED}">({html.escape(_sport_label(w.sport))})</span></div>'
            f"{comment}{detail}</td></tr>"
        )
    return rows


def _race_rows(races: list[PlannedRaceLine]) -> str:
    rows = ""
    for r in races:
        d = date.fromisoformat(r.local_date)
        day = f"{_WEEKDAYS[d.weekday()]} {d.day}"
        bits = [_km(r.distance_m)]
        if r.target_duration_s:
            bits.append(f"target {_clock(r.target_duration_s)}")
        detail = " · ".join(bits)
        compare = ""
        if r.predicted_duration_s is not None and r.target_duration_s is not None:
            diff = r.target_duration_s - r.predicted_duration_s
            note = (
                f"predicted {_clock(r.predicted_duration_s)} — on track"
                if diff >= 0
                else f"predicted {_clock(r.predicted_duration_s)} — {_clock(-diff)} over target"
            )
            compare = f'<div style="font-size:13px;color:{_MUTED}">{html.escape(note)}</div>'
        elif r.predicted_duration_s is not None:
            compare = (
                f'<div style="font-size:13px;color:{_MUTED}">'
                f"predicted {html.escape(_clock(r.predicted_duration_s))}</div>"
            )
        rows += (
            f'<tr><td style="padding:8px 14px;border:1px solid {_BORDER}">'
            f'<div style="color:{_TEXT}"><b>{html.escape(day)}</b> — 🏁 {html.escape(r.name)} '
            f'<span style="color:{_MUTED}">({html.escape(detail)})</span></div>'
            f"{compare}</td></tr>"
        )
    return rows


def _future_race_date_label(r: PlannedRaceLine, today: date) -> str:
    d = date.fromisoformat(r.local_date)
    days_until = (d - today).days
    return f"{_WEEKDAYS[d.weekday()]} {d.day:02d} {d.strftime('%b')} {d.year} ({days_until}d)"


def _future_race_goal(r: PlannedRaceLine) -> str | None:
    if not r.target_duration_s:
        return None
    pace = _pace(r.target_duration_s / (r.distance_m / 1000))
    return f"{_clock_hm(r.target_duration_s)} goal ({pace})"


def _future_race_rows(races: list[PlannedRaceLine], today: date) -> str:
    """Every race on the calendar after today, not just the coming week (_race_rows above) --
    a quick-glance list (date, days-until, name, goal pace) rather than the coming week's own
    full target-vs-predicted comparison, since most of these races are too far out for a current
    prediction to mean much."""
    rows = ""
    for r in races:
        goal = _future_race_goal(r)
        detail = (
            f'<div style="font-size:13px;color:{_MUTED}">{html.escape(goal)}</div>' if goal else ""
        )
        rows += (
            f'<tr><td style="padding:8px 14px;border:1px solid {_BORDER}">'
            f'<div style="color:{_TEXT}"><b>{html.escape(_future_race_date_label(r, today))}</b>: '
            f"🏁 {html.escape(r.name)}</div>"
            f"{detail}</td></tr>"
        )
    return rows


def _shell(title: str, subtitle: str, inner: str) -> str:
    return (
        f'<div style="background:{_BG};padding:24px 0;font-family:-apple-system,'
        f'BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        f'style="margin:0 auto;background:{_CARD};border:1px solid {_BORDER};border-radius:8px">'
        f'<tr><td style="padding:20px 24px;border-bottom:1px solid {_BORDER}">'
        f'<div style="font-size:13px;color:{_ACCENT};font-weight:700;letter-spacing:.06em;'
        f'text-transform:uppercase">Perseverer</div>'
        f'<div style="font-size:22px;color:{_TEXT};font-weight:700;margin-top:4px">'
        f"{html.escape(title)}</div>"
        f'<div style="font-size:14px;color:{_MUTED};margin-top:2px">{html.escape(subtitle)}</div>'
        f"</td></tr>"
        f'<tr><td style="padding:20px 24px">{inner}</td></tr>'
        f'<tr><td style="padding:14px 24px;border-top:1px solid {_BORDER};font-size:12px;'
        f'color:{_MUTED}">{html.escape(_FOOTER)}</td></tr>'
        f"</table></div>"
    )


def _section(title: str, table_inner: str, *, caption: str | None = None) -> str:
    caption_html = (
        f'<div style="font-size:13px;color:{_MUTED};margin:-2px 0 8px">{html.escape(caption)}</div>'
        if caption
        else ""
    )
    return (
        f'<div style="font-size:15px;color:{_TEXT};font-weight:700;margin:18px 0 8px">'
        f"{html.escape(title)}</div>"
        f"{caption_html}"
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse">{table_inner}</table>'
    )


def render_weekly_email(report: WeeklyReport) -> RenderedEmail:
    t = report.totals
    subject = (
        f"Perseverer — your week: {_km(t.distance_m)} across "
        f"{t.activity_count} activit{'y' if t.activity_count == 1 else 'ies'}"
    )
    races_section = (
        _section("Races this week", _race_rows(report.coming_races)) if report.coming_races else ""
    )
    # Omitted entirely (not just an empty chart) when there were no runs/no step data that week
    # -- an all-zero bar chart would just be noise, same "nothing to show" posture the races
    # section above already takes.
    running_section = (
        _section(
            "Running this week",
            _bar_rows(report.running_distance_by_day, _km),
            caption=f"Average pace: {_pace(report.avg_running_pace_s_per_km)}",
        )
        if report.avg_running_pace_s_per_km is not None
        else ""
    )
    steps_section = (
        _section("Steps this week", _bar_rows(report.steps_by_day, _steps_fmt))
        if any(p.value for p in report.steps_by_day)
        else ""
    )
    sleep_section = (
        _section("Sleep this week", _bar_rows(report.sleep_hours_by_day, _sleep_hours_fmt))
        if any(p.value for p in report.sleep_hours_by_day)
        else ""
    )
    future_races_section = (
        _section("Future races", _future_race_rows(report.future_races, report.today))
        if report.future_races
        else ""
    )
    inner = (
        _section("Last week", _stat_cells(t))
        + running_section
        + _section("By sport", _sport_rows(report.sports))
        + steps_section
        + sleep_section
        + races_section
        + future_races_section
        + _section(
            f"Coming week ({_date_range_label(report.coming_start, report.coming_end)})",
            _workout_rows(report.coming_workouts),
        )
    )
    html_body = _shell(
        "Your training week",
        f"{_date_range_label(report.prev_start, report.prev_end)} · "
        f"for {report.athlete_name}",
        inner,
    )
    return RenderedEmail(subject=subject, html=html_body, text=_weekly_text(report))


def render_monthly_email(report: MonthlyReport) -> RenderedEmail:
    t = report.totals
    subject = (
        f"Perseverer — {report.month_label}: {_km(t.distance_m)} across "
        f"{t.activity_count} activit{'y' if t.activity_count == 1 else 'ies'}"
    )
    inner = _section(report.month_label, _stat_cells(t)) + _section(
        "By sport", _sport_rows(report.sports)
    )
    html_body = _shell(
        f"Your training month — {report.month_label}",
        f"for {report.athlete_name}",
        inner,
    )
    return RenderedEmail(subject=subject, html=html_body, text=_monthly_text(report))


def _totals_text(t: PeriodTotals) -> str:
    return (
        f"  Distance:       {_km(t.distance_m)}\n"
        f"  Moving time:    {_hm(t.moving_duration_s)}\n"
        f"  Activities:     {_count(t.activity_count)}\n"
        f"  Active days:    {_count(t.active_days)}\n"
        f"  Elevation gain: {_meters(t.elevation_gain_m)}\n"
        f"  Sleep:          {_hm(t.sleep_total_s)}\n"
    )


def _sports_text(sports: list[SportTotals]) -> str:
    if not sports:
        return "  (no activities recorded)\n"
    return "".join(
        f"  {_sport_label(s.sport):<18} {_km(s.distance_m):>10}  "
        f"{_hm(s.duration_s):>8}  {s.count}x\n"
        for s in sports
    )


def _daily_points_text(
    points: list[DailyMetricPoint], value_fmt: Callable[[float | None], str]
) -> str:
    return "".join(
        f"  {_WEEKDAYS[date.fromisoformat(p.local_date).weekday()]:<4} {value_fmt(p.value):>10}\n"
        for p in points
    )


def _weekly_text(report: WeeklyReport) -> str:
    lines = [
        "PERSEVERER - your training week",
        f"{_date_range_label(report.prev_start, report.prev_end)} - for {report.athlete_name}",
        "",
        "Last week",
        _totals_text(report.totals),
    ]
    if report.avg_running_pace_s_per_km is not None:
        lines.append(f"Running this week (avg pace {_pace(report.avg_running_pace_s_per_km)})")
        lines.append(_daily_points_text(report.running_distance_by_day, _km))
    lines.append("By sport")
    lines.append(_sports_text(report.sports))
    if any(p.value for p in report.steps_by_day):
        lines.append("Steps this week")
        lines.append(_daily_points_text(report.steps_by_day, _steps_fmt))
    if any(p.value for p in report.sleep_hours_by_day):
        lines.append("Sleep this week")
        lines.append(_daily_points_text(report.sleep_hours_by_day, _sleep_hours_fmt))
    if report.coming_races:
        lines.append("Races this week")
        for r in report.coming_races:
            d = date.fromisoformat(r.local_date)
            bits = [_km(r.distance_m)]
            if r.target_duration_s:
                bits.append(f"target {_clock(r.target_duration_s)}")
            lines.append(f"  {_WEEKDAYS[d.weekday()]} {d.day} - {r.name} ({' - '.join(bits)})")
            if r.predicted_duration_s is not None:
                lines.append(f"      predicted {_clock(r.predicted_duration_s)}")
        lines.append("")
    if report.future_races:
        lines.append("Future races")
        for r in report.future_races:
            goal = _future_race_goal(r)
            suffix = f" - {goal}" if goal else ""
            lines.append(f"  {_future_race_date_label(r, report.today)}: {r.name}{suffix}")
        lines.append("")
    lines.append(f"Coming week ({_date_range_label(report.coming_start, report.coming_end)})")
    if report.coming_workouts:
        for w in report.coming_workouts:
            d = date.fromisoformat(w.local_date)
            time = f" {w.scheduled_time}" if w.scheduled_time else ""
            title = w.name or _sport_label(w.sport)
            lines.append(
                f"  {_WEEKDAYS[d.weekday()]} {d.day}{time} - {title} ({_sport_label(w.sport)})"
            )
            if w.comment:
                lines.append(f"      {w.comment}")
            if w.estimate_line:
                lines.append(f"      {w.estimate_line}")
    else:
        lines.append("  (nothing scheduled yet)")
    lines += ["", _FOOTER]
    return "\n".join(lines)


def _monthly_text(report: MonthlyReport) -> str:
    return "\n".join(
        [
            f"PERSEVERER - your training month - {report.month_label}",
            f"for {report.athlete_name}",
            "",
            report.month_label,
            _totals_text(report.totals),
            "By sport",
            _sports_text(report.sports),
            "",
            _FOOTER,
        ]
    )


# --- send ---------------------------------------------------------------------------------


def send_report_email(
    settings: Settings,
    conn: Connection,
    *,
    athlete_id: str,
    kind: ReportKind,
    today: date,
) -> None:
    """Build + render + send one report to the athlete's own `athlete.email`. Raises if the
    athlete has no email set, or on any SMTP failure (callers handle: the scheduled jobs
    log-and-continue, the test endpoint returns 502)."""
    recipient = conn.execute(
        select(athlete.c.email).where(athlete.c.id == athlete_id)
    ).scalar_one_or_none()
    if not recipient:
        raise ValueError(f"athlete {athlete_id} has no email address set")

    if kind == "weekly":
        rendered = render_weekly_email(
            build_weekly_report(conn, athlete_id=athlete_id, today=today)
        )
    else:
        rendered = render_monthly_email(
            build_monthly_report(conn, athlete_id=athlete_id, today=today)
        )
    send_email(
        settings,
        to=recipient,
        subject=rendered.subject,
        html_body=rendered.html,
        text_body=rendered.text,
    )


def athletes_opted_in(conn: Connection, *, kind: ReportKind) -> list[str]:
    """athlete_ids with the given report enabled -- one row, flag True."""
    col = (
        athlete_email_report_config.c.weekly_enabled
        if kind == "weekly"
        else athlete_email_report_config.c.monthly_enabled
    )
    return list(
        conn.execute(
            select(athlete_email_report_config.c.athlete_id).where(col.is_(True))
        ).scalars()
    )
