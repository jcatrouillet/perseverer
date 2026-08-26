"""Athlete-issued tokens granting unauthenticated, read-only access to one activity or one
summary period -- see `api/routers/share.py` for the endpoints, `db/schema.py::share_link` for
the table. Only the token's sha256 is ever stored, matching `auth/api_keys.py`'s own convention
exactly, even though a share link isn't a login credential -- there's no reason to treat it more
loosely just because it's lower-stakes.

The two `render_*_share_html` functions build the actual public response by hand (escaped
f-strings, not a template engine -- the page is simple enough that adding a Jinja2 dependency
for it isn't worth it) -- both the OG meta tags social platforms read and the visible page body
come from the exact same query, so there's no risk of the preview card and the page disagreeing.
Deliberately excludes fields `ActivitySummary` includes but this project's own schema docs flag
as not vetted for public exposure: `weight_kg`, `avg_hr_bpm`/`max_hr_bpm` -- and, by the same
reasoning, the period share below deliberately excludes health data entirely (weight/HRV/sleep),
even though the real YearView page it mirrors shows those: this app's own existing stance is
that health metrics aren't vetted for public exposure, and a period share has no per-activity
opt-in the way an activity share at least implicitly does.

`render_period_share_html` mirrors YearView/AllTimeView's own stats -- activity totals, a
running-specific breakdown (query `activity` directly, not a rollup, since day_rollup/
period_rollup don't break down by sport), a month-by-month table (year periods only; "all" can
span a decade of months, not worth a table), and a Fitness & Form (CTL/ATL) chart -- rendered as
inline hand-built SVG (`_svg_line_chart`/`_svg_bar_chart`) rather than a JS charting library,
consistent with this whole page being static, crawler-readable HTML with no JS at all.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html import escape

from sqlalchemy import Connection, func, select

from perseverer.db.schema import (
    activity,
    day_rollup,
    fitness_daily_rollup,
    period_rollup,
    share_link,
)
from perseverer.merge.engine import sport_family

_TOKEN_BYTES = 32


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_share_link(
    conn: Connection, *, athlete_id: str, target_type: str, target_id: str
) -> tuple[int, str]:
    """Returns (share_link.id, raw_token) -- the raw token is the only time it's ever available
    in plaintext; only its hash is persisted. The id is returned so the caller can offer a
    revoke action later without needing to know the token itself."""
    raw_token = secrets.token_urlsafe(_TOKEN_BYTES)
    result = conn.execute(
        share_link.insert().values(
            athlete_id=athlete_id,
            token_hash=_hash_token(raw_token),
            target_type=target_type,
            target_id=target_id,
            created_at=datetime.now(UTC),
        )
    )
    assert result.inserted_primary_key is not None
    new_id = result.inserted_primary_key[0]
    assert isinstance(new_id, int)
    return new_id, raw_token


def revoke_share_link(conn: Connection, *, athlete_id: str, id: int) -> bool:
    """True if a matching, not-already-revoked row was found and revoked."""
    result = conn.execute(
        share_link.update()
        .where(
            share_link.c.id == id,
            share_link.c.athlete_id == athlete_id,
            share_link.c.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    return result.rowcount > 0


@dataclass(frozen=True)
class ShareTarget:
    athlete_id: str
    target_type: str
    target_id: str


def resolve_share_token(conn: Connection, raw_token: str) -> ShareTarget | None:
    """None for both "no such token" and "revoked" -- deliberately not distinguished in the
    response, no reason to tell an outside visitor which one it was."""
    row = conn.execute(
        select(share_link.c.athlete_id, share_link.c.target_type, share_link.c.target_id).where(
            share_link.c.token_hash == _hash_token(raw_token),
            share_link.c.revoked_at.is_(None),
        )
    ).fetchone()
    if row is None:
        return None
    return ShareTarget(
        athlete_id=row.athlete_id, target_type=row.target_type, target_id=row.target_id
    )


_UNAVAILABLE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Link unavailable</title></head>
<body style="font-family: sans-serif; max-width: 32rem; margin: 4rem auto; text-align: center;">
<p>This link is no longer available.</p>
</body></html>"""


def render_unavailable_html() -> str:
    return _UNAVAILABLE_HTML


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _format_km(meters: float | None) -> str:
    return "—" if meters is None else f"{meters / 1000:.2f} km"


def _format_meters(meters: float | None) -> str:
    return "—" if meters is None else f"{meters:.0f} m"


def _format_pace(seconds: float | None, meters: float | None) -> str:
    if not seconds or not meters:
        return "—"
    pace_s_per_km = seconds / (meters / 1000)
    m, s = divmod(int(pace_s_per_km), 60)
    return f"{m}:{s:02d} /km"


def _stat(label: str, value: str) -> str:
    return (
        f'<div class="stat"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}</div></div>'
    )


def _stats_grid(*pairs: tuple[str, str]) -> str:
    rows = "\n".join(_stat(label, value) for label, value in pairs)
    return f'<div class="stats">\n{rows}\n</div>'


def _svg_line_chart(
    series: list[tuple[float, float]], *, width: int = 560, height: int = 140
) -> str:
    """Two polylines (e.g. CTL/ATL) sharing one y-scale -- `series` is a list of (a, b) value
    pairs, one per x position, already in display order. Empty string for fewer than 2 points
    (nothing to draw a line between)."""
    if len(series) < 2:
        return ""
    values = [v for pair in series for v in pair]
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    pad = 6

    def x_at(i: int) -> float:
        return pad + i / (len(series) - 1) * (width - 2 * pad)

    def y_at(v: float) -> float:
        return height - pad - (v - lo) / span * (height - 2 * pad)

    a_pts = " ".join(f"{x_at(i):.1f},{y_at(a):.1f}" for i, (a, _) in enumerate(series))
    b_pts = " ".join(f"{x_at(i):.1f},{y_at(b):.1f}" for i, (_, b) in enumerate(series))
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="Fitness (CTL) and Fatigue (ATL) over time" class="chart">'
        f'<polyline points="{a_pts}" fill="none" stroke="#2563eb" stroke-width="2"/>'
        f'<polyline points="{b_pts}" fill="none" stroke="#f59e0b" stroke-width="2"/>'
        f"</svg>"
    )


def _svg_bar_chart(bars: list[tuple[str, float]], *, width: int = 560, height: int = 150) -> str:
    """One bar per (label, value) pair, e.g. monthly distance -- `bars` in display order."""
    if not bars:
        return ""
    max_v = max(v for _, v in bars) or 1.0
    pad = 4
    plot_w = width - 2 * pad
    slot_w = plot_w / len(bars)
    bar_w = slot_w * 0.6
    label_y = height - 6
    bar_floor = height - 22
    parts = []
    for i, (label, v) in enumerate(bars):
        x = pad + i * slot_w + (slot_w - bar_w) / 2
        bar_h = (v / max_v) * (bar_floor - 10)
        y = bar_floor - bar_h
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
            f'fill="#2563eb" rx="2"/>'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{label_y}" font-size="9" text-anchor="middle" '
            f'fill="#666">{escape(label)}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="Monthly distance" class="chart">{"".join(parts)}</svg>'
    )


def _page(*, title: str, description: str, body: str) -> str:
    title_esc, desc_esc = escape(title), escape(description)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_esc}</title>
<meta property="og:title" content="{title_esc}">
<meta property="og:description" content="{desc_esc}">
<meta property="og:type" content="website">
<meta name="description" content="{desc_esc}">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 32rem; margin: 3rem auto; padding: 0 1.5rem; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0.25rem; }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }}
  h2 {{ font-size: 1rem; margin: 1.75rem 0 0.5rem; }}
  .stats {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; }}
  .stat {{ background: #f5f5f5; border-radius: 8px; padding: 0.75rem 1rem; }}
  .stat .label {{ font-size: 0.75rem; color: #666; text-transform: uppercase; }}
  .stat .value {{ font-size: 1.3rem; font-weight: 600; }}
  .chart {{ display: block; margin: 0.5rem 0; }}
  .chart-legend {{ font-size: 0.75rem; color: #666; margin-bottom: 0.25rem; }}
  .legend-dot {{ display: inline-block; width: 0.6rem; height: 0.6rem; border-radius: 50%;
                 margin-right: 0.25rem; }}
  footer {{ margin-top: 2rem; font-size: 0.8rem; color: #999; }}
</style>
</head>
<body>
{body}
<footer>Shared from Perseverer.</footer>
</body>
</html>"""


def render_activity_share_html(conn: Connection, activity_id: str) -> str:
    row = conn.execute(
        select(
            activity.c.name,
            activity.c.sport,
            activity.c.sub_sport,
            activity.c.local_date,
            activity.c.is_race,
            activity.c.duration_s,
            activity.c.moving_duration_s,
            activity.c.distance_m,
            activity.c.elevation_gain_m,
        ).where(activity.c.id == activity_id, activity.c.deleted_at.is_(None))
    ).fetchone()
    if row is None:
        return render_unavailable_html()

    name = row.name or row.sport.replace("_", " ").title()
    sport_label = (row.sub_sport or row.sport).replace("_", " ").title()
    race_badge = " 🏁" if row.is_race else ""
    description = (
        f"{sport_label} on {row.local_date or ''} — "
        f"{_format_km(row.distance_m)}, {_format_duration(row.moving_duration_s)}"
    )
    stats = _stats_grid(
        ("Distance", _format_km(row.distance_m)),
        ("Duration", _format_duration(row.moving_duration_s)),
        ("Pace", _format_pace(row.moving_duration_s, row.distance_m)),
        ("Elevation gain", _format_meters(row.elevation_gain_m)),
    )
    body = f"""
<h1>{escape(name)}{race_badge}</h1>
<div class="meta">{escape(sport_label)} · {escape(row.local_date or "")}</div>
{stats}
"""
    return _page(title=name, description=description, body=body)


VALID_PERIOD_TYPES = frozenset({"week", "month", "year", "all"})


def _period_date_range(period_type: str, period_start: str | None) -> tuple[str | None, str | None]:
    """(start, end) local_date bounds, inclusive, both None for "all" (no filter)."""
    if period_type == "all":
        return None, None
    if period_type == "year":
        assert period_start is not None
        return f"{period_start}-01-01", f"{period_start}-12-31"
    if period_type == "month":
        assert period_start is not None
        year, month = (int(p) for p in period_start.split("-"))
        end_day = 31 if month == 12 else (date(year, month + 1, 1) - date(year, month, 1)).days
        return f"{period_start}-01", f"{period_start}-{end_day:02d}"
    # "week": period_start is the Monday local_date already.
    assert period_start is not None
    start = date.fromisoformat(period_start)
    return period_start, (start + timedelta(days=6)).isoformat()


_MONTH_ABBR = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def render_period_share_html(
    conn: Connection, athlete_id: str, period_type: str, period_start: str | None
) -> str:
    if period_type not in VALID_PERIOD_TYPES:
        return render_unavailable_html()
    start, end = _period_date_range(period_type, period_start)

    rollup_query = select(
        func.coalesce(func.sum(day_rollup.c.activity_count), 0).label("activity_count"),
        func.sum(day_rollup.c.activity_distance_m).label("distance_m"),
        func.sum(day_rollup.c.activity_elevation_gain_m).label("elevation_gain_m"),
        func.sum(day_rollup.c.activity_moving_duration_s).label("moving_duration_s"),
        func.count().filter(day_rollup.c.activity_count > 0).label("active_days"),
    ).where(day_rollup.c.athlete_id == athlete_id)
    if start is not None and end is not None:
        rollup_query = rollup_query.where(day_rollup.c.local_date.between(start, end))
    totals = conn.execute(rollup_query).one()

    # Every non-deleted activity's sport/distance/duration in range, fetched once -- used below
    # both for the single longest activity and the running breakdown (sport_family has no SQL
    # equivalent, so that filter runs in Python here, same cross-language-duplication precedent
    # as insights/rules_pb.py's own personalRecords logic).
    activity_query = select(
        activity.c.sport, activity.c.distance_m, activity.c.moving_duration_s
    ).where(activity.c.athlete_id == athlete_id, activity.c.deleted_at.is_(None))
    if start is not None and end is not None:
        activity_query = activity_query.where(activity.c.local_date.between(start, end))
    activities = conn.execute(activity_query).fetchall()
    with_distance = [a for a in activities if a.distance_m]
    longest_m = max((a.distance_m for a in with_distance), default=None)

    label = "All time" if period_type == "all" else (period_start or "")
    description = (
        f"{totals.activity_count} activities, {_format_km(totals.distance_m)}, "
        f"{totals.active_days} active day(s)"
    )
    stats = _stats_grid(
        ("Activities", str(totals.activity_count)),
        ("Distance", _format_km(totals.distance_m)),
        ("Moving time", _format_duration(totals.moving_duration_s)),
        ("Elevation gain", _format_meters(totals.elevation_gain_m)),
        ("Active days", str(totals.active_days)),
        ("Longest activity", _format_km(longest_m)),
    )

    running_body = ""
    running = [a for a in with_distance if sport_family(a.sport) == "run"]
    if running:
        total_run_m = sum(a.distance_m or 0 for a in running)
        total_run_s = sum(a.moving_duration_s or 0 for a in running)
        longest_run_m = max(a.distance_m or 0 for a in running)
        running_stats = _stats_grid(
            ("Kilometers run", f"{total_run_m / 1000:.0f} km"),
            ("Number of runs", str(len(running))),
            ("Avg pace", _format_pace(total_run_s, total_run_m)),
            ("Longest run", _format_km(longest_run_m)),
        )
        running_body = f"<h2>Running</h2>\n{running_stats}\n"

    # Month-by-month distance, year periods only -- "all" can span a decade of months (not worth
    # a bar per month) and week/month periods are already narrower than a month themselves.
    months_body = ""
    if period_type == "year" and period_start is not None:
        month_rows = conn.execute(
            select(period_rollup.c.period_start, period_rollup.c.activity_distance_m)
            .where(
                period_rollup.c.athlete_id == athlete_id,
                period_rollup.c.period_type == "month",
                period_rollup.c.period_start.between(f"{period_start}-01", f"{period_start}-12"),
            )
            .order_by(period_rollup.c.period_start)
        ).fetchall()
        bars = [
            (
                _MONTH_ABBR[int(r.period_start.split("-")[1]) - 1],
                (r.activity_distance_m or 0) / 1000,
            )
            for r in month_rows
        ]
        if any(v > 0 for _, v in bars):
            months_body = f"<h2>Distance by month (km)</h2>\n{_svg_bar_chart(bars)}\n"

    # Fitness & Form (CTL/ATL) -- meaningful only over month+ windows; a week's worth of points
    # is too short a trend to chart.
    fitness_body = ""
    if period_type in ("month", "year", "all"):
        fitness_query = (
            select(fitness_daily_rollup.c.ctl, fitness_daily_rollup.c.atl)
            .where(fitness_daily_rollup.c.athlete_id == athlete_id)
            .order_by(fitness_daily_rollup.c.local_date)
        )
        if start is not None and end is not None:
            fitness_query = fitness_query.where(
                fitness_daily_rollup.c.local_date.between(start, end)
            )
        fitness_rows = conn.execute(fitness_query).fetchall()
        chart = _svg_line_chart([(r.ctl, r.atl) for r in fitness_rows])
        if chart:
            fitness_body = (
                "<h2>Fitness &amp; Form</h2>\n"
                '<div class="chart-legend"><span class="legend-dot" '
                'style="background:#2563eb"></span>Fitness (CTL) &nbsp; '
                '<span class="legend-dot" style="background:#f59e0b"></span>Fatigue (ATL)</div>\n'
                f"{chart}\n"
            )

    body = f"""
<h1>{escape(label)}</h1>
<div class="meta">Activity summary</div>
{stats}
{running_body}{fitness_body}{months_body}"""
    return _page(title=f"{label} summary", description=description, body=body)
