"""Weekly / monthly training-report emails -- opt-in per athlete
(`athlete_email_report_config`), delivered via the deployment's shared SMTP relay
(`email_delivery.send_email`, `PERSEVERER_SMTP_*`), scheduled from `worker/main.py`.

- **Weekly** (Sunday 18:00 local): the Mon--Sun week that just ended (activity totals + a
  per-sport breakdown) plus the coming Mon--Sun week's planned workouts.
- **Monthly** (month's last day, 18:00): the calendar month that just ended -- totals only, no
  planned-workout section.

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
    period_rollup,
    planned_workout,
)
from perseverer.email_delivery import send_email
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
class PlannedWorkoutLine:
    local_date: str
    sport: str
    name: str | None
    scheduled_time: str | None
    estimate_line: str | None  # running only, e.g. "5.8 km · 34 min · Load 36"


@dataclass(frozen=True)
class WeeklyReport:
    athlete_name: str
    prev_start: date
    prev_end: date
    coming_start: date
    coming_end: date
    totals: PeriodTotals
    sports: list[SportTotals]
    coming_workouts: list[PlannedWorkoutLine]


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
    rows = conn.execute(
        select(
            activity.c.sport,
            func.count().label("n"),
            func.sum(activity.c.distance_m).label("distance_m"),
            func.sum(func.coalesce(activity.c.moving_duration_s, activity.c.duration_s)).label(
                "duration_s"
            ),
        )
        .where(
            and_(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.local_date >= start,
                activity.c.local_date <= end,
            )
        )
        .group_by(activity.c.sport)
        .order_by(func.sum(activity.c.distance_m).desc().nulls_last(), func.count().desc())
    ).fetchall()
    return [
        SportTotals(
            sport=r.sport, count=r.n, distance_m=r.distance_m, duration_s=r.duration_s
        )
        for r in rows
    ]


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
            )
        )
    return lines


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
        prev_start=prev_start,
        prev_end=prev_end,
        coming_start=coming_start,
        coming_end=coming_end,
        totals=_totals_from_rollup(conn, athlete_id, "week", prev_start.isoformat()),
        sports=_sport_breakdown(
            conn, athlete_id, prev_start.isoformat(), prev_end.isoformat()
        ),
        coming_workouts=_coming_workouts(
            conn, athlete_id, coming_start.isoformat(), coming_end.isoformat()
        ),
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


def _count(n: int | None) -> str:
    return "0" if not n else str(n)


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
        detail = (
            f'<div style="font-size:13px;color:{_MUTED}">{html.escape(w.estimate_line)}</div>'
            if w.estimate_line
            else ""
        )
        rows += (
            f'<tr><td style="padding:8px 14px;border:1px solid {_BORDER}">'
            f'<div style="color:{_TEXT}"><b>{html.escape(day)}</b>{time} — {title} '
            f'<span style="color:{_MUTED}">({html.escape(_sport_label(w.sport))})</span></div>'
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


def _section(title: str, table_inner: str) -> str:
    return (
        f'<div style="font-size:15px;color:{_TEXT};font-weight:700;margin:18px 0 8px">'
        f"{html.escape(title)}</div>"
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse">{table_inner}</table>'
    )


def render_weekly_email(report: WeeklyReport) -> RenderedEmail:
    t = report.totals
    subject = (
        f"Perseverer — your week: {_km(t.distance_m)} across "
        f"{t.activity_count} activit{'y' if t.activity_count == 1 else 'ies'}"
    )
    inner = (
        _section("Last week", _stat_cells(t))
        + _section("By sport", _sport_rows(report.sports))
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


def _weekly_text(report: WeeklyReport) -> str:
    lines = [
        "PERSEVERER - your training week",
        f"{_date_range_label(report.prev_start, report.prev_end)} - for {report.athlete_name}",
        "",
        "Last week",
        _totals_text(report.totals),
        "By sport",
        _sports_text(report.sports),
        f"Coming week ({_date_range_label(report.coming_start, report.coming_end)})",
    ]
    if report.coming_workouts:
        for w in report.coming_workouts:
            d = date.fromisoformat(w.local_date)
            time = f" {w.scheduled_time}" if w.scheduled_time else ""
            title = w.name or _sport_label(w.sport)
            lines.append(
                f"  {_WEEKDAYS[d.weekday()]} {d.day}{time} - {title} ({_sport_label(w.sport)})"
            )
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
