"""Athlete-issued tokens granting unauthenticated, read-only access to one activity or one
summary period -- see `api/routers/share.py` for the endpoints, `db/schema.py::share_link` for
the table. Only the token's sha256 is ever stored, matching `auth/api_keys.py`'s own convention
exactly, even though a share link isn't a login credential -- there's no reason to treat it more
loosely just because it's lower-stakes.

The two `render_*_share_html` functions build the actual public response by hand (escaped
f-strings, not a template engine -- the page is simple enough that adding a Jinja2 dependency
for it isn't worth it) -- both the OG meta tags social platforms read and the visible page body
come from the exact same query, so there's no risk of the preview card and the page disagreeing.
Deliberately excludes fields this project's own schema docs flag as not vetted for public
exposure, and everything health-related (weight/HRV/sleep) -- this app's own existing stance is
that health metrics aren't vetted for public exposure, and a share has no per-field opt-in the
way an athlete reviewing their own dashboard at least implicitly has.

Both pages mirror the *authenticated* frontend's own activity-detail and period-summary views as
closely as a public, unauthenticated page reasonably can. Two things are still intentionally NOT
mirrored, to avoid turning a public page into a second implementation of logic that belongs in
one place:
  - The context/comparison sections (fastest-for-this-distance, recent efforts percentile,
    similar-runs/similar-climbs tables) are themselves already a second, request-time aggregate
    query each -- reasonable for an athlete's own occasional page view, not something to also
    expose to anonymous traffic. Skipped; the Intervals/bouldering-routes tables below already
    cover this page's "tables" mandate with data that's already a plain row read.
  - Time-in-zone's *stream-computed* path (against the athlete's own configured HR zones) is
    skipped -- the *device-reported* path is not (see below).

Weather is shown, but only ever from cache -- this page never calls `weather.py`'s own
fetch-if-missing path (`get_or_fetch_activity_weather`), which would let a public URL trigger a
live paid-API call. `_cached_weather` below is a plain `SELECT` against `activity_metric`
mirroring `weather.py::_read_cached`'s own read-only query (duplicated rather than imported,
since that function is module-private) -- an activity whose weather was never fetched by an
*authenticated* view simply shows no Weather section, same "absent data is a normal state"
convention as everywhere else on this page.

Time-in-zone is shown from the *device-reported* fallback only (`fit.time_in_zone.*` metrics,
already present in the `metrics` dict this function already loads) -- never the athlete's own
configured-HR-zone stream computation (`TimeInZoneChart.tsx`'s other path), which would need the
athlete's `athlete_hr_zone_config` row joined in for no real benefit to an outside visitor.

Two visuals are real interactive JS, not hand-rolled static SVG, a deliberate exception to this
file's own general zero-JS-by-default posture:
  - The route map: CARTO's Positron vector basemap via MapLibre GL, the same one
    `frontend/src/mapBasemap.ts` uses, loaded from a CDN `<script>`/`<link>` and initialized by
    one small inline module script -- plus a Play/Pause route-playback marker (the same idea as
    `ActivityRouteMap.tsx`'s own scrubber, simplified to a fixed-speed replay rather than a
    draggable scrubber) driven by the same low-tier `lat`/`lon` stream the charts below already
    fetch. The CARTO key this embeds is the same `PERSEVERER_CARTO_API_KEY` the authenticated
    frontend already ships to every visitor's browser -- not actually secret (CARTO's basemap
    product is designed for client-side embedding, like a Mapbox public token), so a public share
    page carries no larger exposure than the app already has. The static route line itself comes
    straight from `route_geom.encoded_polyline` (precision-5 `polyline`, computed at ingest) --
    only the playback marker needs the per-sample stream.
  - The per-second charts (elevation/pace-or-speed/heart rate/cadence/power/respiration): still
    hand-rolled SVG (`stream_query.py::downsample`, the same bucket-averaging function
    `GET /activities/{id}/stream` uses, called at the "low" tier -- 200 points -- through a
    throwaway in-memory DuckDB connection against the archived Parquet file), but each chart now
    also embeds its own `values`/`elapsed_s` arrays as a sibling `<script type="application/json">`
    and gets a hover crosshair + value/unit/elapsed-time tooltip from one small shared script
    (`_CHART_HOVER_SCRIPT`) -- reading a value off a chart was the actual ask (a static image with
    no axis units was not enough), not a full charting library.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html import escape
from typing import Any
from urllib.parse import quote

import duckdb
import polyline as polyline_codec
from sqlalchemy import Connection, Row, func, select

from perseverer.api.routers.activities import (
    AVG_HR_METRIC_KEYS,
    CADENCE_METRIC_KEY,
    MAX_HR_METRIC_KEYS,
    TRAINING_LOAD_METRIC_KEYS,
)
from perseverer.config import Settings
from perseverer.db.schema import (
    activity,
    activity_metric,
    activity_stream,
    activity_workout_step,
    day_rollup,
    fitness_daily_rollup,
    lap,
    period_rollup,
    route_geom,
    share_link,
)
from perseverer.db.schema import split as split_table
from perseverer.gap import compute_lap_gap_speeds_mps
from perseverer.geocoding import read_cached_location
from perseverer.merge.engine import sport_family
from perseverer.performance import VDOT_METRIC_KEY
from perseverer.stream_query import downsample
from perseverer.weather_code import weather_code_info

_TOKEN_BYTES = 32

# Sports whose average is meaningfully a "pace" (min/km) rather than a "speed" (km/h) -- same
# list as frontend/src/runningStats.ts::PACE_SPORTS.
_PACE_SPORTS = frozenset({"running", "walking", "hiking", "snowshoeing"})


def _is_bouldering(sport: str, sub_sport: str | None) -> bool:
    return sport == "rock_climbing" and sub_sport == "bouldering"


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


# --- Formatting helpers -------------------------------------------------------------------


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


def _format_pace_from_speed_mps(mps: float | None) -> str:
    if not mps or mps <= 0:
        return "—"
    pace_s_per_km = 1000 / mps
    m, s = divmod(int(pace_s_per_km), 60)
    return f"{m}:{s:02d} /km"


def _format_speed_kmh(mps: float | None) -> str:
    return "—" if mps is None else f"{mps * 3.6:.1f} km/h"


def _format_pace_or_speed(seconds: float | None, meters: float | None, *, pace_sport: bool) -> str:
    if pace_sport:
        return _format_pace(seconds, meters)
    if not seconds or not meters:
        return "—"
    return _format_speed_kmh(meters / seconds)


def _local_time_label(start_time_utc: datetime, utc_offset_s: int) -> str:
    # Not `strftime("%-I:%M %p")` -- the `-` no-leading-zero flag is a glibc/Linux-only strftime
    # extension; this module runs on both Windows (dev/tests) and Linux (bercy), so it's built by
    # hand instead of relying on a platform-specific format string.
    local = start_time_utc + timedelta(seconds=utc_offset_s)
    hour12 = local.hour % 12 or 12
    period = "AM" if local.hour < 12 else "PM"
    return f"{hour12}:{local.minute:02d} {period}"


def _stat(label: str, value: str) -> str:
    return (
        f'<div class="stat"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}</div></div>'
    )


def _stats_grid(*pairs: tuple[str, str]) -> str:
    rows = "\n".join(_stat(label, value) for label, value in pairs)
    return f'<div class="stats">\n{rows}\n</div>'


# --- Icons + tones -- matching frontend/src/components/Icon.tsx / StatTile.tsx as closely as a
# static page reasonably can, so a stat here reads as the same metric as its authenticated-app
# counterpart, not a differently-styled clone. Only the hand-rolled stroke glyphs actually used
# by ActivityStatsGrid.tsx's own confirmed icon/tone assignments are reproduced here (path data
# copied verbatim from Icon.tsx's own inline SVG sprite) -- the Phosphor sport pictograms aren't
# needed on this page at all. `tone` values are the same names `metricStyle.ts::Tone` uses and
# resolve against the exact same `--color-*` tokens this file's own `<style>` already defines.
_ICON_PATHS: dict[str, str] = {
    "route": (
        '<circle cx="5.5" cy="18" r="2.3"/><circle cx="18.5" cy="6" r="2.3"/>'
        '<path d="M7.6 17.1c3.4-1 4.2-3 4.4-5 .2-2.2 1.4-4 4.4-4.8" stroke-dasharray="2.4 2.6"/>'
    ),
    "clock": '<circle cx="12" cy="12" r="8.4"/><path d="M12 7.1v5.2l3.4 2"/>',
    "mountain": '<path d="M2.6 19h18.8L14.1 6l-3.5 6.1-2.3-2.7L2.6 19Z"/>',
    "heart": (
        '<path d="M12 20.2s-7.3-4.5-7.3-9.4a4.25 4.25 0 0 1 7.3-2.6 4.25 4.25 0 0 1 '
        '7.3 2.6c0 4.9-7.3 9.4-7.3 9.4Z"/>'
    ),
    "flame": (
        '<path d="M12.5 3c.4 3.9-3.1 5-3.1 8.3 0 1 .5 1.7.5 2.4 0 .9-.7 1.5-1.5 1.5-.9 '
        "0-1.6-.7-1.6-1.9-1 1.3-1.4 2.6-1.4 3.9C5.4 19.9 8.3 22 12 22s6.6-2.1 "
        '6.6-4.8c0-5-3.4-6.6-6.1-14.2Z"/>'
    ),
    "bolt": '<path d="M13.4 2.4 5.9 13.6h5.3l-.6 8 7.5-11.2h-5.3l.6-8Z"/>',
    "pulse": '<path d="M2.6 12.4h4l2.1-5.6 3.6 11.2 2.4-7 1.6 3.2h5.1"/>',
    "gauge": (
        '<path d="M4 17.6a9 9 0 1 1 16 0"/><path d="M12 17.4 15.9 10"/>'
        '<circle cx="12" cy="17.8" r="1.5"/>'
    ),
    "steps": (
        '<path d="M8.2 4.4c1.5 0 2.4 1.3 2.4 3.2 0 2.5-.8 4.2-2.4 4.2s-2.4-1.7-2.4-4.2c0-1.9'
        ".9-3.2 2.4-3.2ZM8.2 14c1.3 0 2 .7 2 2 0 1.6-.8 2.7-2 2.7s-2-1.1-2-2.7c0-1.3.7-2 "
        "2-2ZM16.4 8c1.5 0 2.4 1.3 2.4 3.2 0 2.5-.8 4.2-2.4 4.2S14 13.7 14 11.2C14 9.3 14.9 "
        '8 16.4 8ZM16.4 17.6c1.3 0 2 .7 2 2 0 1.6-.8 2.7-2 2.7"/>'
    ),
    "trend": '<path d="M3.2 16.6 9 10.8l3.6 3.6 8.2-8.2"/><path d="M15.4 6.2h5.4v5.4"/>',
    "thermometer": (
        '<path d="M12 3.6a2.1 2.1 0 0 0-2.1 2.1v8.6a3.6 3.6 0 1 0 4.2 0V5.7A2.1 2.1 0 0 0 '
        '12 3.6Z"/><path d="M12 10.4v5.4"/>'
    ),
}


def _icon_svg(name: str) -> str:
    path = _ICON_PATHS.get(name)
    if path is None:
        return ""
    return f'<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">{path}</svg>'


@dataclass(frozen=True)
class _Stat:
    label: str
    value: str
    icon: str | None = None
    tone: str = "neutral"


def st(label: str, value: str, icon: str | None = None, tone: str = "neutral") -> _Stat:
    return _Stat(label, value, icon, tone)


def _stat_tile(stat: _Stat) -> str:
    icon_html = f'<span class="icon-chip">{_icon_svg(stat.icon)}</span>' if stat.icon else ""
    return (
        f'<div class="stat tone-{stat.tone}"><div class="label">{icon_html}'
        f"{escape(stat.label)}</div>"
        f'<div class="value">{escape(stat.value)}</div></div>'
    )


def _stats_grid_iconed(*stats: _Stat) -> str:
    rows = "\n".join(_stat_tile(s) for s in stats)
    return f'<div class="stats">\n{rows}\n</div>'


def _section(title: str, inner: str) -> str:
    return f"<h2>{escape(title)}</h2>\n{inner}\n"


# --- Inline SVG charts ----------------------------------------------------------------------


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
        f'<polyline points="{a_pts}" fill="none" stroke="var(--color-elevation)" stroke-width="2"/>'
        f'<polyline points="{b_pts}" fill="none" stroke="var(--color-load)" stroke-width="2"/>'
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
            f'fill="var(--color-pace)" rx="2"/>'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{label_y}" font-size="9" text-anchor="middle" '
            f'class="chart-axis-label">{escape(label)}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="Monthly distance" class="chart">{"".join(parts)}</svg>'
    )


def _format_axis_number(v: float, *, decimals: int) -> str:
    return f"{v:.{decimals}f}"


def _format_axis_pace(minutes: float) -> str:
    m = int(minutes)
    s = round((minutes - m) * 60)
    if s == 60:
        m, s = m + 1, 0
    return f"{m}:{s:02d}"


def _format_elapsed_hms(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _svg_series_chart(
    values: list[float | None],
    elapsed_s: list[float],
    *,
    width: int = 560,
    height: int = 110,
    color: str = "var(--color-pace)",
    area: bool = False,
    label: str = "",
    unit: str = "",
    decimals: int = 0,
    pace_format: bool = False,
    chart_id: str = "",
) -> str:
    """One line (optionally area-filled), from a possibly-gappy per-sample series -- gaps (None)
    are skipped when drawing but x-position is still based on the sample's own index, so a real
    gap in the recording shows as a break rather than compressing the timeline. Y-axis min/max are
    labeled with `unit`, and the whole thing is wrapped for the shared hover script
    (`_CHART_HOVER_SCRIPT`) to read a value + elapsed time off any point via mouse/touch --
    `values`/`elapsed_s` are embedded verbatim as a sibling JSON blob for that script to read,
    not recomputed from the SVG's own drawn geometry."""
    n = len(values)
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2 or n < 2:
        return ""
    vs = [v for _, v in pts]
    lo, hi = min(vs), max(vs)
    span = hi - lo or 1.0
    pad = 4

    def x_at(i: int) -> float:
        return pad + i / (n - 1) * (width - 2 * pad)

    def y_at(v: float) -> float:
        return height - pad - (v - lo) / span * (height - 2 * pad)

    def _fmt(v: float) -> str:
        return _format_axis_pace(v) if pace_format else _format_axis_number(v, decimals=decimals)

    fmt = _fmt

    poly = " ".join(f"{x_at(i):.1f},{y_at(v):.1f}" for i, v in pts)
    fill = ""
    if area:
        floor_x0, floor_x1 = x_at(pts[0][0]), x_at(pts[-1][0])
        fill = (
            f'<polygon points="{floor_x0:.1f},{height - pad} {poly} '
            f'{floor_x1:.1f},{height - pad}" fill="{color}" fill-opacity="0.15" stroke="none"/>'
        )
    aria = f' aria-label="{escape(label)}"' if label else ""
    axis_labels = (
        f'<text x="{width - pad}" y="{pad + 8}" font-size="9" text-anchor="end" '
        f'class="chart-axis-label">{escape(fmt(hi))}{escape(unit)}</text>'
        f'<text x="{width - pad}" y="{height - pad - 2}" font-size="9" text-anchor="end" '
        f'class="chart-axis-label">{escape(fmt(lo))}{escape(unit)}</text>'
    )
    svg = (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img"{aria} class="chart">{fill}'
        f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/>'
        f'{axis_labels}'
        f'<line class="ichart-cursor" x1="0" x2="0" y1="{pad}" y2="{height - pad}"/>'
        f"</svg>"
    )
    cfg = json.dumps(
        {
            "values": values,
            "elapsed_s": elapsed_s,
            "unit": unit,
            "decimals": decimals,
            "pace": pace_format,
            "width": width,
            "pad": pad,
        }
    )
    return (
        f'<div class="ichart" id="{chart_id}">{svg}'
        f'<div class="ichart-tooltip" hidden></div>'
        f'<script type="application/json" class="ichart-data">{cfg}</script>'
        f"</div>"
    )


# Reads each `.ichart`'s own sibling `<script class="ichart-data">` (values/elapsed_s/unit/
# decimals/pace/width/pad) and wires up a hover (mouse) + touch crosshair that shows the nearest
# real sample's value and elapsed time -- added because a static chart image with no way to read
# an exact value off it wasn't enough; this is the one shared script emitted once per activity
# page, not duplicated per chart.
_CHART_HOVER_SCRIPT = """
<script>
(function() {
  function formatElapsed(s) {
    var total = Math.round(s), h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60),
        sec = total % 60;
    var mm = (h ? String(m).padStart(2, "0") : String(m)) + ":" + String(sec).padStart(2, "0");
    return h ? h + ":" + mm : mm;
  }
  function formatPace(minutes) {
    var m = Math.floor(minutes), s = Math.round((minutes - m) * 60);
    if (s === 60) { m += 1; s = 0; }
    return m + ":" + String(s).padStart(2, "0");
  }
  document.querySelectorAll(".ichart").forEach(function (container) {
    var dataEl = container.querySelector("script.ichart-data");
    var svg = container.querySelector("svg");
    var cursor = container.querySelector(".ichart-cursor");
    var tooltip = container.querySelector(".ichart-tooltip");
    if (!dataEl || !svg || !tooltip) return;
    var cfg = JSON.parse(dataEl.textContent);
    var n = cfg.values.length;

    function update(clientX) {
      var rect = svg.getBoundingClientRect();
      if (rect.width === 0) return;
      var frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      var i = Math.round(frac * (n - 1));
      var v = cfg.values[i];
      if (v == null) { hide(); return; }
      var xSvg = cfg.pad + (i / (n - 1)) * (cfg.width - 2 * cfg.pad);
      cursor.setAttribute("x1", xSvg); cursor.setAttribute("x2", xSvg);
      cursor.style.visibility = "visible";
      var valueText = cfg.pace ? formatPace(v) : v.toFixed(cfg.decimals);
      tooltip.textContent = formatElapsed(cfg.elapsed_s[i]) + " \\u2014 " + valueText + cfg.unit;
      tooltip.hidden = false;
      tooltip.style.left = (frac * 100) + "%";
    }
    function hide() { tooltip.hidden = true; cursor.style.visibility = "hidden"; }

    svg.addEventListener("mousemove", function (e) { update(e.clientX); });
    svg.addEventListener("mouseleave", hide);
    svg.addEventListener(
      "touchmove",
      function (e) { if (e.touches[0]) update(e.touches[0].clientX); },
      { passive: true },
    );
    svg.addEventListener("touchend", hide);
  });
})();
</script>
"""


def _svg_grade_chart(
    breakdown: list[tuple[int, int, int]], *, width: int = 560, height: int = 150
) -> str:
    """Stacked bar per grade -- completed (green) atop attempted (red), matching
    ClimbGradeChart.tsx's own reading order. `breakdown` is (grade, attempted, completed)."""
    if not breakdown:
        return ""
    max_v = max(a + c for _, a, c in breakdown) or 1.0
    pad = 4
    plot_w = width - 2 * pad
    slot_w = plot_w / len(breakdown)
    bar_w = slot_w * 0.6
    label_y = height - 6
    bar_floor = height - 22
    parts = []
    for i, (grade, attempted, completed) in enumerate(breakdown):
        x = pad + i * slot_w + (slot_w - bar_w) / 2
        attempted_h = (attempted / max_v) * (bar_floor - 10)
        completed_h = (completed / max_v) * (bar_floor - 10)
        y_attempted = bar_floor - attempted_h
        y_completed = y_attempted - completed_h
        if attempted_h > 0:
            parts.append(
                f'<rect x="{x:.1f}" y="{y_attempted:.1f}" width="{bar_w:.1f}" '
                f'height="{attempted_h:.1f}" fill="var(--color-danger)" rx="1"/>'
            )
        if completed_h > 0:
            parts.append(
                f'<rect x="{x:.1f}" y="{y_completed:.1f}" width="{bar_w:.1f}" '
                f'height="{completed_h:.1f}" fill="var(--color-success)" rx="1"/>'
            )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{label_y}" font-size="9" text-anchor="middle" '
            f'class="chart-axis-label">V{grade}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="Routes by grade" class="chart">{"".join(parts)}</svg>'
    )


# --- Route map (the one deliberate exception to this file's zero-JS default) ----------------

_MAPLIBRE_VERSION = "6.6.0"
_CARTO_POSITRON_STYLE = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"


def _carto_style_url(settings: Settings) -> str:
    if settings.carto_api_key:
        return f"{_CARTO_POSITRON_STYLE}?key={quote(settings.carto_api_key)}"
    return _CARTO_POSITRON_STYLE


# Total wall-clock length of a playback replay, regardless of the activity's own real duration --
# a fixed, watchable length rather than literally replaying a 55-minute run in real time.
_PLAYBACK_DURATION_MS = 20_000


def _route_map_html(
    route_row: Row[Any],
    settings: Settings,
    *,
    playback_coords: list[list[float]] | None,
    playback_elapsed_s: list[float] | None,
) -> str:
    if not route_row.encoded_polyline:
        return ""
    points = polyline_codec.decode(route_row.encoded_polyline)  # [(lat, lng), ...]
    if len(points) < 2:
        return ""
    coords = [[lng, lat] for lat, lng in points]
    bounds = [
        [route_row.min_lng, route_row.min_lat],
        [route_row.max_lng, route_row.max_lat],
    ]
    style_url = _carto_style_url(settings)
    map_id = "route-map"
    base = f"https://cdn.jsdelivr.net/npm/maplibre-gl@{_MAPLIBRE_VERSION}/dist"

    has_playback = (
        playback_coords is not None
        and playback_elapsed_s is not None
        and len(playback_coords) >= 2
    )
    playback_ui = ""
    playback_js = ""
    if has_playback:
        assert playback_coords is not None and playback_elapsed_s is not None
        total_s = playback_elapsed_s[-1] or 1.0
        playback_ui = """
<div class="route-playback">
  <button type="button" class="route-playback__btn" id="route-playback-toggle"
          aria-label="Play route">&#9654;</button>
  <span class="route-playback__track">
    <span class="route-playback__fill" id="route-playback-fill"></span>
  </span>
  <span class="route-playback__time" id="route-playback-time">0:00</span>
</div>
"""
        playback_js = f"""
    var playbackCoords = {json.dumps(playback_coords)};
    var playbackElapsed = {json.dumps(playback_elapsed_s)};
    var totalS = {total_s};
    var runner = new Marker({{color: "var(--color-accent)"}})
      .setLngLat(playbackCoords[0])
      .addTo(map);
    var toggleBtn = document.getElementById("route-playback-toggle");
    var fillEl = document.getElementById("route-playback-fill");
    var timeEl = document.getElementById("route-playback-time");
    var playing = false, startTs = null, pausedAtMs = 0, rafId = null;

    function formatElapsed(s) {{
      var total = Math.round(s), h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60),
          sec = total % 60;
      var mm = (h ? String(m).padStart(2, "0") : String(m)) + ":" + String(sec).padStart(2, "0");
      return h ? h + ":" + mm : mm;
    }}
    function renderAt(fracOfTotal) {{
      var targetS = fracOfTotal * totalS;
      var i = 0;
      while (i < playbackElapsed.length - 1 && playbackElapsed[i + 1] <= targetS) i++;
      runner.setLngLat(playbackCoords[Math.min(i, playbackCoords.length - 1)]);
      fillEl.style.width = (fracOfTotal * 100) + "%";
      timeEl.textContent = formatElapsed(targetS);
    }}
    function tick(now) {{
      if (!playing) return;
      var elapsedMs = now - startTs + pausedAtMs;
      var frac = Math.min(1, elapsedMs / {_PLAYBACK_DURATION_MS});
      renderAt(frac);
      if (frac >= 1) {{
        playing = false;
        pausedAtMs = 0;
        toggleBtn.innerHTML = "&#9654;";
        toggleBtn.setAttribute("aria-label", "Replay route");
        return;
      }}
      rafId = requestAnimationFrame(tick);
    }}
    toggleBtn.addEventListener("click", function () {{
      if (playing) {{
        playing = false;
        pausedAtMs += performance.now() - startTs;
        toggleBtn.innerHTML = "&#9654;";
        toggleBtn.setAttribute("aria-label", "Play route");
        if (rafId) cancelAnimationFrame(rafId);
        return;
      }}
      if (pausedAtMs >= {_PLAYBACK_DURATION_MS}) pausedAtMs = 0;
      playing = true;
      startTs = performance.now();
      toggleBtn.innerHTML = "&#10074;&#10074;";
      toggleBtn.setAttribute("aria-label", "Pause route");
      rafId = requestAnimationFrame(tick);
    }});
"""

    # v6 dropped its UMD build entirely (confirmed against the real published package -- its
    # dist/ only ships .mjs files now, no plain maplibre-gl.js global) -- same fact
    # CartoBasemapLayer.tsx's own docstring already documents for the bundler build, just
    # re-confirmed here for a plain CDN `<script>` rather than a Vite import. Loaded as a real
    # `type="module"` script with named imports, and `setWorkerUrl` pointed at the CDN's own
    # worker file is mandatory (same reason: the library no longer resolves its background-
    # thread worker via `import.meta.url` on its own).
    return f"""
<div id="{map_id}" class="route-map"></div>
{playback_ui}
<link href="{base}/maplibre-gl.css" rel="stylesheet">
<script type="module">
  import {{ Map, Marker, NavigationControl, setWorkerUrl }} from "{base}/maplibre-gl.mjs";
  setWorkerUrl("{base}/maplibre-gl-worker.mjs");
  var map = new Map({{
    container: "{map_id}",
    style: {json.dumps(style_url)},
    attributionControl: true,
  }});
  map.addControl(new NavigationControl({{showCompass: false}}), "top-right");
  map.on("load", function() {{
    map.addSource("route", {{
      type: "geojson",
      data: {{
        type: "Feature",
        geometry: {{type: "LineString", coordinates: {json.dumps(coords)}}},
      }},
    }});
    map.addLayer({{
      id: "route-line",
      type: "line",
      source: "route",
      layout: {{"line-cap": "round", "line-join": "round"}},
      paint: {{"line-color": "#2563eb", "line-width": 4}},
    }});
    new Marker({{color: "#16a34a"}})
      .setLngLat([{route_row.start_lng}, {route_row.start_lat}])
      .addTo(map);
    new Marker({{color: "#dc2626"}})
      .setLngLat([{route_row.end_lng}, {route_row.end_lat}])
      .addTo(map);
    map.fitBounds({json.dumps(bounds)}, {{padding: 32, animate: false}});
{playback_js}
  }});
</script>
"""


# --- Page shell -------------------------------------------------------------------------


def _page(*, title: str, description: str, body: str, extra_head: str = "") -> str:
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
  /* Same token values as frontend/src/styles/theme.css -- dark is the default there too, with
     a `prefers-color-scheme: light` override; this page has no JS-based theme toggle (no
     localStorage choice to persist), so it only ever follows the visitor's OS preference,
     which is exactly theme.css's own no-explicit-choice-stored behavior. */
  :root {{
    --color-bg: #12151c; --color-surface: #1b1f29; --color-surface-raised: #232836;
    --color-border: #2e3441; --color-text: #eef1f6; --color-text-muted: #9aa3b5;
    --color-text-faint: #6b7386; --color-accent: #4da3ff;
    --color-heart-rate: #ef5a6f; --color-pace: #4da3ff; --color-elevation: #4caf7d;
    --color-power: #b57bee; --color-cadence: #3fb6c9; --color-load: #e8a33d;
    --color-success: #4caf7d; --color-success-bg: rgba(76, 175, 125, 0.18);
    --color-danger: #ef5a6f; --color-danger-bg: rgba(239, 90, 111, 0.18);
    --shadow-card: 0 1px 3px rgba(0, 0, 0, 0.4);
  }}
  @media (prefers-color-scheme: light) {{
    :root {{
      --color-bg: #f4f6f9; --color-surface: #ffffff; --color-surface-raised: #f4f6f9;
      --color-border: #dde2ea; --color-text: #1a1d24; --color-text-muted: #5a6273;
      --color-text-faint: #8a92a3; --color-accent: #1a73e8;
      --color-heart-rate: #d43a52; --color-pace: #1a73e8; --color-elevation: #2f9160;
      --color-power: #8c4fd6; --color-cadence: #1f92a3; --color-load: #c47f1a;
      --color-success: #2f9160; --color-success-bg: rgba(47, 145, 96, 0.12);
      --color-danger: #d43a52; --color-danger-bg: rgba(212, 58, 82, 0.12);
      --shadow-card: 0 1px 3px rgba(20, 25, 35, 0.08);
    }}
  }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 42rem; margin: 3rem auto; padding: 0 1.5rem;
          background: var(--color-bg); color: var(--color-text); }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0.25rem; }}
  .meta {{ color: var(--color-text-muted); font-size: 0.9rem; margin-bottom: 1.5rem; }}
  h2 {{ font-size: 1rem; margin: 1.75rem 0 0.5rem; }}
  h3 {{ font-size: 0.85rem; margin: 1.1rem 0 0.4rem; color: var(--color-text-muted); }}
  .stats {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; }}
  .stat {{ background: var(--color-surface); border: 1px solid var(--color-border);
           box-shadow: var(--shadow-card); border-radius: 8px; padding: 0.75rem 1rem; }}
  .stat .label {{ display: flex; align-items: center; gap: 0.4rem; font-size: 0.75rem;
                  color: var(--color-text-muted); text-transform: uppercase; }}
  .stat .value {{ font-size: 1.3rem; font-weight: 600; }}
  /* Tone + icon-chip -- same names/tokens as frontend/src/metricStyle.ts::Tone and
     components/StatTile.tsx's own icon-chip, so a metric here reads as the same colour as its
     authenticated-app counterpart. `--tone` inherits onto the icon-chip below it. */
  .tone-neutral {{ --tone: var(--color-text-muted); }}
  .tone-pace {{ --tone: var(--color-pace); }}
  .tone-hr {{ --tone: var(--color-heart-rate); }}
  .tone-elevation {{ --tone: var(--color-elevation); }}
  .tone-power {{ --tone: var(--color-power); }}
  .tone-cadence {{ --tone: var(--color-cadence); }}
  .tone-load {{ --tone: var(--color-load); }}
  .icon-chip {{ width: 22px; height: 22px; border-radius: 4px; display: grid; place-items: center;
                color: var(--tone); background: color-mix(in srgb, var(--tone) 15%, transparent);
                flex: none; }}
  .icon {{ width: 13px; height: 13px; display: block; fill: none; stroke: currentColor;
           stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }}
  .chart {{ display: block; margin: 0.5rem 0; }}
  .chart-axis-label {{ fill: var(--color-text-faint); }}
  .chart-legend {{ font-size: 0.75rem; color: var(--color-text-muted); margin-bottom: 0.25rem; }}
  .legend-dot {{ display: inline-block; width: 0.6rem; height: 0.6rem; border-radius: 50%;
                 margin-right: 0.25rem; }}
  .ichart {{ position: relative; }}
  .ichart svg {{ cursor: crosshair; }}
  .ichart-cursor {{ stroke: var(--color-text-faint); stroke-width: 1; visibility: hidden; }}
  .ichart-tooltip {{ position: absolute; top: 0; transform: translateX(-50%);
                      background: var(--color-surface-raised);
                      border: 1px solid var(--color-border);
                      border-radius: 4px; padding: 0.15rem 0.4rem; font-size: 0.75rem;
                      white-space: nowrap; pointer-events: none; }}
  .route-map {{ width: 100%; height: 22rem; border-radius: 8px; margin: 0.5rem 0; }}
  .route-playback {{ display: flex; align-items: center; gap: 0.75rem; margin: 0.5rem 0;
                      font-size: 0.85rem; color: var(--color-text-muted); }}
  .route-playback__btn {{ background: var(--color-accent); color: var(--color-bg); border: none;
                           border-radius: 999px; width: 2rem; height: 2rem; font-size: 0.9rem;
                           cursor: pointer; flex-shrink: 0; }}
  .route-playback__track {{ flex: 1; height: 4px; border-radius: 999px;
                             background: var(--color-border); overflow: hidden; }}
  .route-playback__fill {{ height: 100%; width: 0%; background: var(--color-accent); }}
  .route-playback__time {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .table-scroll {{ overflow-x: auto; margin: 0.5rem 0; }}
  table.tbl {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
  table.tbl th, table.tbl td {{ text-align: right; padding: 0.35rem 0.6rem;
                                 border-bottom: 1px solid var(--color-border);
                                 white-space: nowrap; }}
  table.tbl th:first-child, table.tbl td:first-child {{ text-align: left; }}
  table.tbl thead th {{ color: var(--color-text-muted); font-weight: 500; font-size: 0.75rem;
                         text-transform: uppercase; }}
  .badge {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px;
            font-size: 0.75rem; font-weight: 600; }}
  .badge--completed {{ background: var(--color-success-bg); color: var(--color-success); }}
  .badge--attempt {{ background: var(--color-danger-bg); color: var(--color-danger); }}
  .weather-row {{ display: flex; align-items: center; gap: 0.75rem; }}
  .weather-emoji {{ font-size: 2rem; line-height: 1; }}
  .weather-readout {{ display: flex; flex-direction: column; gap: 0.15rem; font-size: 0.85rem;
                       color: var(--color-text-muted); }}
  .weather-readout strong {{ color: var(--color-text); font-size: 0.95rem; }}
  .time-in-zone__row {{ display: grid; grid-template-columns: 5.5rem 1fr 7rem; align-items: center;
                         gap: 0.6rem; margin: 0.3rem 0; font-size: 0.8rem; }}
  .time-in-zone__track {{ height: 10px; border-radius: 999px; background: var(--color-border);
                           overflow: hidden; }}
  .time-in-zone__fill {{ display: block; height: 100%; }}
  .time-in-zone__value {{ text-align: right; color: var(--color-text-muted); }}
  footer {{ margin-top: 2rem; font-size: 0.8rem; color: var(--color-text-faint); }}
</style>
{extra_head}
</head>
<body>
{body}
<footer>Shared from Perseverer.</footer>
</body>
</html>"""


# --- Activity share -------------------------------------------------------------------------


def _first_metric(metrics: dict[str, float], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        if k in metrics:
            return metrics[k]
    return None


def _climb_summary_from_splits(
    splits: Sequence[Row[Any]],
) -> tuple[int | None, int | None, float | None]:
    """Same logic as api/routers/activities.py::_climb_summary_from_splits -- duplicated (not
    imported) because that one lives on a Row shape private to that router's own query; this
    file runs its own SELECT * FROM split. Returns (route_count, max_completed_grade,
    climb_time_s), all None when this activity has no climb_active splits at all."""
    climbs = [s for s in splits if s.climb_grade is not None]
    if not climbs:
        return None, None, None
    completed_grades = [c.climb_grade for c in climbs if c.climb_result == "completed"]
    climb_time_s = sum(c.duration_s for c in climbs if c.duration_s is not None) or None
    return len(climbs), (max(completed_grades) if completed_grades else None), climb_time_s


# Mirrors weather.py's own module-private metric_key constants -- duplicated, not imported,
# since `_read_cached` there is module-private (leading underscore); this is the exact same
# read-only query, never the fetch-if-missing path (`get_or_fetch_activity_weather`), which a
# public URL must never be able to trigger. See this file's own module docstring.
_WEATHER_SOURCE = "open-meteo"
_WEATHER_REQUIRED_KEYS = (
    "weather.open_meteo.temperature_min_c",
    "weather.open_meteo.temperature_max_c",
    "weather.open_meteo.humidity_min_pct",
    "weather.open_meteo.humidity_max_pct",
    "weather.open_meteo.weather_code",
)
_WEATHER_OPTIONAL_KEYS = (
    "weather.open_meteo.feels_like_c",
    "weather.open_meteo.wind_speed_mps",
    "weather.open_meteo.wind_direction_deg",
)

_COMPASS_POINTS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def _compass_direction(degrees: float) -> str:
    return _COMPASS_POINTS[round(degrees / 22.5) % 16]


def _cached_weather(conn: Connection, athlete_id: str, activity_id: str) -> dict[str, float] | None:
    rows = conn.execute(
        select(activity_metric.c.metric_key, activity_metric.c.value_num).where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.activity_id == activity_id,
            activity_metric.c.source == _WEATHER_SOURCE,
            activity_metric.c.metric_key.in_((*_WEATHER_REQUIRED_KEYS, *_WEATHER_OPTIONAL_KEYS)),
        )
    ).fetchall()
    values = {r.metric_key: r.value_num for r in rows if r.value_num is not None}
    if not all(k in values for k in _WEATHER_REQUIRED_KEYS):
        return None
    return values


def _weather_html(values: dict[str, float]) -> str:
    t_min = values["weather.open_meteo.temperature_min_c"]
    t_max = values["weather.open_meteo.temperature_max_c"]
    h_min = values["weather.open_meteo.humidity_min_pct"]
    h_max = values["weather.open_meteo.humidity_max_pct"]
    code = int(values["weather.open_meteo.weather_code"])
    feels_like = values.get("weather.open_meteo.feels_like_c")
    wind_speed = values.get("weather.open_meteo.wind_speed_mps")
    wind_dir = values.get("weather.open_meteo.wind_direction_deg")

    info = weather_code_info(code)
    temp = (
        f"{t_min:.0f}°C" if round(t_min) == round(t_max) else f"{t_min:.0f}-{t_max:.0f}°C"
    )
    humidity = (
        f"{h_min:.0f}% RH" if round(h_min) == round(h_max) else f"{h_min:.0f}-{h_max:.0f}% RH"
    )

    lines = [escape(info.label), escape(temp)]
    if feels_like is not None:
        lines.append(f"Feels like {feels_like:.0f}°C")
    lines.append(escape(humidity))
    if wind_speed is not None:
        wind = f"Wind {wind_speed:.0f} m/s"
        if wind_dir is not None:
            wind += f" from {_compass_direction(wind_dir)}"
        lines.append(escape(wind))

    readout = "".join(f"<span>{line}</span>" for line in lines)
    return (
        '<div class="weather-row">'
        f'<span class="weather-emoji" title="{escape(info.label)}">{info.emoji}</span>'
        f'<span class="weather-readout">{readout}</span>'
        "</div>"
    )


# Device-reported time-in-zone only (see this file's own module docstring) -- port of
# frontend/src/activityMetrics.ts::extractHrZones. `fit.time_in_zone.time_in_hr_zone_{N}` and
# `..hr_zone_high_boundary_{N}` are already ordinary `activity_metric` rows, already loaded into
# this function's own `metrics` dict -- no stream/zone-config join needed.
_TIME_IN_ZONE_PREFIX = "fit.time_in_zone.time_in_hr_zone_"
_ZONE_BOUNDARY_PREFIX = "fit.time_in_zone.hr_zone_high_boundary_"
# Same cool-to-hot tone cycle as TimeInZoneChart.tsx's own ZONE_TONES, as CSS var references.
_ZONE_TONE_VARS = (
    "var(--color-cadence)", "var(--color-elevation)", "var(--color-pace)",
    "var(--color-load)", "var(--color-power)", "var(--color-heart-rate)", "var(--color-heart-rate)",
)


@dataclass(frozen=True)
class _HrZone:
    index: int
    seconds: float
    low: float | None
    high: float | None


def _extract_hr_zones(metrics: dict[str, float]) -> list[_HrZone]:
    time_by_index: dict[int, float] = {}
    boundary_by_index: dict[int, float] = {}
    for key, value in metrics.items():
        if key.startswith(_TIME_IN_ZONE_PREFIX):
            time_by_index[int(key[len(_TIME_IN_ZONE_PREFIX) :])] = value
        elif key.startswith(_ZONE_BOUNDARY_PREFIX):
            boundary_by_index[int(key[len(_ZONE_BOUNDARY_PREFIX) :])] = value
    if not time_by_index:
        return []
    max_index = max(time_by_index)
    zones = []
    for i in range(max_index + 1):
        if i not in time_by_index:
            continue
        zones.append(
            _HrZone(
                index=i,
                seconds=time_by_index[i],
                low=None if i == 0 else boundary_by_index.get(i - 1),
                high=boundary_by_index.get(i),
            )
        )
    return zones


def _zone_range_label(zone: _HrZone) -> str:
    if zone.low is None and zone.high is not None:
        return f"< {zone.high:.0f}"
    if zone.low is not None and zone.high is None:
        return f"{zone.low:.0f}+"
    if zone.low is not None and zone.high is not None:
        return f"{zone.low:.0f} - {zone.high:.0f}"
    return "—"


def _time_in_zone_html(zones: list[_HrZone]) -> str:
    total = sum(z.seconds for z in zones) or 1.0
    rows = []
    for zone in zones:
        pct = zone.seconds / total * 100
        color = _ZONE_TONE_VARS[zone.index % len(_ZONE_TONE_VARS)]
        value = f"{_format_duration(zone.seconds)} · {pct:.0f}%" if zone.seconds > 0 else "—"
        rows.append(
            '<div class="time-in-zone__row">'
            f"<span>Z{zone.index} <span>{escape(_zone_range_label(zone))}</span></span>"
            f'<span class="time-in-zone__track"><span class="time-in-zone__fill" '
            f'style="width:{pct:.1f}%;background:{color}"></span></span>'
            f'<span class="time-in-zone__value">{escape(value)}</span>'
            "</div>"
        )
    note = f'<p class="chart-note">Total {_format_duration(total)} across {len(zones)} zones.</p>'
    return "".join(rows) + note


@dataclass(frozen=True)
class _ExpandedStep:
    duration_type: str | None
    duration_time_s: float | None
    duration_distance_m: float | None
    target_type: str | None
    target_low_mps: float | None
    target_high_mps: float | None
    intensity: str | None


def _to_expanded_step(s: Row[Any]) -> _ExpandedStep:
    return _ExpandedStep(
        duration_type=s.duration_type,
        duration_time_s=s.duration_time_s,
        duration_distance_m=s.duration_distance_m,
        target_type=s.target_type,
        target_low_mps=s.target_low_mps,
        target_high_mps=s.target_high_mps,
        intensity=s.intensity,
    )


def _expand_workout_steps(steps: Sequence[Row[Any]]) -> list[_ExpandedStep]:
    """Port of frontend/src/workoutSteps.ts::expandWorkoutSteps -- unrolls each
    `repeat_until_steps_cmplt` step into `repeat_count` copies of its own child steps
    (`step_index` in `[repeat_from_step, step_index)`, file order), so a lap recorded by the
    device for each *executed* rep lines up positionally with `laps[i] <-> expanded[i]` --
    confirmed against a real structured-workout FIT file (see ActivityDetailPage.tsx's own
    comment). A plain step not consumed by any repeat group passes through unchanged."""
    ordered = sorted(steps, key=lambda s: s.step_index)
    consumed: set[int] = set()
    for s in ordered:
        if s.duration_type == "repeat_until_steps_cmplt" and s.repeat_from_step is not None:
            consumed.update(range(s.repeat_from_step, s.step_index))
    by_index = {s.step_index: s for s in ordered}

    out: list[_ExpandedStep] = []
    for s in ordered:
        if s.duration_type == "repeat_until_steps_cmplt":
            if s.repeat_from_step is None or s.repeat_count is None:
                continue
            children = [
                by_index[i] for i in range(s.repeat_from_step, s.step_index) if i in by_index
            ]
            for _ in range(s.repeat_count):
                out.extend(_to_expanded_step(c) for c in children)
        elif s.step_index not in consumed:
            out.append(_to_expanded_step(s))
    return out


def _interval_label(intensity: str | None) -> str:
    return intensity.capitalize() if intensity else "Step"


def _expected_duration_label(step: _ExpandedStep) -> str:
    """Port of frontend/src/workoutSteps.ts::formatStepDurationLabel."""
    if step.duration_type == "distance" and step.duration_distance_m is not None:
        km = step.duration_distance_m / 1000
        return f"{km:.0f}km" if km == int(km) else f"{km:.2f}km"
    if step.duration_type == "time" and step.duration_time_s is not None:
        if step.duration_time_s % 60 == 0:
            return f"{int(step.duration_time_s / 60)}m"
        return f"{round(step.duration_time_s)}s"
    return "—"


def _minutes_per_km(mps: float | None) -> float | None:
    return None if not mps or mps <= 0 else 1000 / mps / 60


def _expected_pace_label(step: _ExpandedStep, *, pace_sport: bool) -> str:
    """Port of frontend/src/workoutSteps.ts::targetPaceRangeLabel -- pace sports with a "speed"
    target range only; the unit itself is the table header's job, same as the real app."""
    if not pace_sport or step.target_type != "speed":
        return "—"
    fast = _minutes_per_km(step.target_high_mps)
    slow = _minutes_per_km(step.target_low_mps)
    if fast is None or slow is None:
        return "—"
    return f"{_format_axis_pace(fast)}-{_format_axis_pace(slow)}"


_CHART_CHANNELS: tuple[tuple[str, str, str, bool, str, int], ...] = (
    # (parquet channel, chart title, color, area-fill, unit, decimals) -- colors/tones match
    # ActivityCharts.tsx's own per-channel tone exactly (elevation/pace/hr/cadence/cadence/power
    # -- respiration and cadence deliberately share one tone in the real app too).
    ("altitude_m", "Elevation", "var(--color-elevation)", True, " m", 0),
    # title/unit resolved at render time (Pace vs Speed); decimals overridden there too.
    ("speed_mps", "", "var(--color-pace)", False, "", 0),
    ("heart_rate", "Heart rate", "var(--color-heart-rate)", False, " bpm", 0),
    ("respiration_rate", "Respiration", "var(--color-cadence)", False, " brpm", 0),
    ("cadence", "Cadence", "var(--color-cadence)", False, " spm", 0),
    ("power", "Power", "var(--color-power)", False, " W", 0),
)


def render_activity_share_html(conn: Connection, settings: Settings, activity_id: str) -> str:
    row = conn.execute(
        select(activity).where(activity.c.id == activity_id, activity.c.deleted_at.is_(None))
    ).fetchone()
    if row is None:
        return render_unavailable_html()

    metrics = {
        m.metric_key: m.value_num
        for m in conn.execute(
            select(activity_metric.c.metric_key, activity_metric.c.value_num).where(
                activity_metric.c.activity_id == activity_id
            )
        ).fetchall()
        if m.value_num is not None
    }
    avg_hr = _first_metric(metrics, AVG_HR_METRIC_KEYS)
    max_hr = _first_metric(metrics, MAX_HR_METRIC_KEYS)
    training_load = _first_metric(metrics, TRAINING_LOAD_METRIC_KEYS)
    vdot = metrics.get(VDOT_METRIC_KEY)
    workout_rpe_raw = metrics.get("fit.session.workout_rpe")
    workout_rpe = workout_rpe_raw / 10 if workout_rpe_raw is not None else None
    total_descent = _first_metric(
        metrics, ("fit.session.total_descent", "strava.session.total_descent")
    )
    cadence_raw = metrics.get(CADENCE_METRIC_KEY)
    max_cadence_raw = metrics.get("fit.session.max_running_cadence")

    is_bouldering = _is_bouldering(row.sport, row.sub_sport)
    pace_sport = row.sport in _PACE_SPORTS
    duration_s = row.moving_duration_s or row.duration_s

    location = read_cached_location(conn, row.athlete_id, activity_id)

    name = row.name or row.sport.replace("_", " ").title()
    sport_label = (row.sub_sport or row.sport).replace("_", " ").title()
    race_badge = " 🏁" if row.is_race else ""
    description = (
        f"{sport_label} on {row.local_date or ''} — "
        f"{_format_km(row.distance_m)}, {_format_duration(row.moving_duration_s)}"
    )

    meta_parts = [sport_label, row.local_date or ""]
    if row.start_time_utc is not None:
        meta_parts.append(_local_time_label(row.start_time_utc, row.utc_offset_s or 0))
    if location:
        meta_parts.append(location)
    meta_line = " · ".join(p for p in meta_parts if p)

    splits = conn.execute(
        select(split_table)
        .where(split_table.c.activity_id == activity_id)
        .order_by(split_table.c.split_index)
    ).fetchall()
    climb_route_count, climb_max_grade, climb_time_s = _climb_summary_from_splits(splits)

    # --- Primary stats ---
    body_parts: list[str] = [
        f"<h1>{escape(name)}{race_badge}</h1>",
        f'<div class="meta">{escape(meta_line)}</div>',
    ]

    weather_values = _cached_weather(conn, row.athlete_id, activity_id)
    if weather_values is not None:
        body_parts.append(_weather_html(weather_values))

    if is_bouldering:
        primary = _stats_grid_iconed(
            st(
                "Max completed grade",
                f"V{climb_max_grade}" if climb_max_grade is not None else "—",
                "mountain",
                "elevation",
            ),
            st("Routes", str(climb_route_count or 0), "route", "pace"),
            st("Climb time", _format_duration(climb_time_s), "clock", "cadence"),
            st(
                "Calories",
                f"{row.calories:.0f} kcal" if row.calories is not None else "—",
                "flame",
                "load",
            ),
        )
        body_parts.append(_section("Time & calories", primary))
    else:
        primary_stats = []
        if row.distance_m is not None:
            primary_stats.append(st("Distance", _format_km(row.distance_m), "route", "pace"))
        if duration_s is not None:
            primary_stats.append(
                st("Moving time", _format_duration(duration_s), "clock", "cadence")
            )
        if row.distance_m and duration_s:
            primary_stats.append(
                st(
                    "Avg pace" if pace_sport else "Avg speed",
                    _format_pace_or_speed(duration_s, row.distance_m, pace_sport=pace_sport),
                    "gauge",
                    "pace",
                )
            )
        if row.calories is not None:
            primary_stats.append(st("Calories", f"{row.calories:.0f} kcal", "flame", "load"))
        if primary_stats:
            body_parts.append(_section("Distance & time", _stats_grid_iconed(*primary_stats)))

    if row.carbohydrates_g is not None or row.sodium_mg is not None:
        fueling_pairs = []
        if row.carbohydrates_g is not None:
            fueling_pairs.append(("Carbohydrates", f"{row.carbohydrates_g:.0f} g"))
        if row.sodium_mg is not None:
            fueling_pairs.append(("Sodium", f"{row.sodium_mg:.0f} mg"))
        body_parts.append(_section("Fueling", _stats_grid(*fueling_pairs)))

    if avg_hr is not None or max_hr is not None:
        hr_stats = []
        if avg_hr is not None:
            hr_stats.append(st("Avg heart rate", f"{avg_hr:.0f} bpm", "heart", "hr"))
        if max_hr is not None:
            hr_stats.append(st("Max heart rate", f"{max_hr:.0f} bpm", "heart", "hr"))
        body_parts.append(_section("Heart rate", _stats_grid_iconed(*hr_stats)))

    elevation_stats = []
    if row.elevation_gain_m is not None:
        elevation_stats.append(
            st("Elevation gain", _format_meters(row.elevation_gain_m), "mountain", "elevation")
        )
    if total_descent is not None:
        elevation_stats.append(
            st("Elevation loss", _format_meters(total_descent), "mountain", "elevation")
        )
    if row.max_altitude_m is not None:
        elevation_stats.append(
            st("Max elevation", _format_meters(row.max_altitude_m), "mountain", "elevation")
        )
    if elevation_stats:
        body_parts.append(_section("Elevation", _stats_grid_iconed(*elevation_stats)))

    # --- Route map ---
    route_row = conn.execute(
        select(route_geom).where(route_geom.c.activity_id == activity_id)
    ).fetchone()
    # --- Charts + per-lap GAP + route-playback coordinates, all from the Parquet stream ---
    stream_row = conn.execute(
        select(activity_stream).where(activity_stream.c.activity_id == activity_id)
    ).fetchone()
    laps = conn.execute(
        select(lap).where(lap.c.activity_id == activity_id).order_by(lap.c.lap_index)
    ).fetchall()
    lap_gaps: list[float | None] = [None] * len(laps)
    chart_hover_needed = False
    charts_section = ""
    playback_coords: list[list[float]] | None = None
    playback_elapsed_s: list[float] | None = None

    if stream_row is not None:
        available_channels = frozenset(json.loads(stream_row.channels))
        parquet_path = settings.parquet_dir / stream_row.parquet_path
        con = duckdb.connect()
        try:
            needs_gap = laps and row.sport == "running"
            has_gap_channels = {"altitude_m", "distance_m"} <= available_channels
            if needs_gap and has_gap_channels:
                stream_rows = con.execute(
                    "SELECT epoch(timestamp_utc) AS ts, distance_m, altitude_m FROM read_parquet(?)"
                    " ORDER BY timestamp_utc",
                    [str(parquet_path)],
                ).fetchall()
                if stream_rows:
                    lap_gaps = compute_lap_gap_speeds_mps(
                        [lp.start_time_utc.replace(tzinfo=UTC).timestamp() for lp in laps],
                        [r[0] for r in stream_rows],
                        [r[1] for r in stream_rows],
                        [r[2] for r in stream_rows],
                    )

            chart_channels = [c for c, *_ in _CHART_CHANNELS if c in available_channels]
            has_position = {"lat", "lon"} <= available_channels
            requested_channels = [*chart_channels, *(["lat", "lon"] if has_position else [])]
            if requested_channels and row.duration_s:
                # "low" tier (200 pts) is plenty for both a readable chart and a smooth-looking
                # route-playback marker at this page's fixed replay speed -- no reason to pay for
                # a heavier tier just because the authenticated app's own scrubber uses one.
                ds = downsample(
                    con,
                    parquet_path,
                    tier="low",
                    channels=requested_channels,
                    available_channels=available_channels,
                    duration_s=row.duration_s,
                    n_samples=stream_row.n_samples,
                )
                elapsed_s = [
                    (ts - row.start_time_utc.replace(tzinfo=ts.tzinfo)).total_seconds()
                    for ts in ds.timestamps
                ]

                if has_position:
                    lats, lons = ds.series.get("lat"), ds.series.get("lon")
                    if lats and lons:
                        playback_coords = [
                            [lon, lat]
                            for lat, lon in zip(lats, lons, strict=True)
                            if lat is not None and lon is not None
                        ]
                        playback_elapsed_s = [
                            elapsed_s[i]
                            for i, (lat, lon) in enumerate(zip(lats, lons, strict=True))
                            if lat is not None and lon is not None
                        ]
                        if len(playback_coords) < 2:
                            playback_coords = playback_elapsed_s = None

                chart_svgs = []
                for channel, title, color, area, unit, decimals in _CHART_CHANNELS:
                    values = ds.series.get(channel)
                    if not values:
                        continue
                    pace_format = False
                    if channel == "speed_mps":
                        title = "Pace" if pace_sport else "Speed"
                        unit = "" if pace_sport else " km/h"
                        pace_format = pace_sport
                        decimals = 1
                        values = [
                            (1000 / v / 60 if pace_sport else v * 3.6) if v else None
                            for v in values
                        ]
                    svg = _svg_series_chart(
                        values,
                        elapsed_s,
                        color=color,
                        area=area,
                        label=title,
                        unit=unit,
                        decimals=decimals,
                        pace_format=pace_format,
                        chart_id=f"chart-{channel}",
                    )
                    if svg:
                        chart_hover_needed = True
                        chart_svgs.append(f'<h3>{escape(title)}</h3>\n{svg}')
                if chart_svgs:
                    charts_section = _section("Charts", "\n".join(chart_svgs))
        finally:
            con.close()

    if route_row is not None:
        map_html = _route_map_html(
            route_row,
            settings,
            playback_coords=playback_coords,
            playback_elapsed_s=playback_elapsed_s,
        )
        if map_html:
            body_parts.append(_section("Route", map_html))
    if charts_section:
        body_parts.append(charts_section)

    # --- Intervals (laps) table ---
    if laps and not is_bouldering and row.sport != "hiking":
        workout_steps = conn.execute(
            select(activity_workout_step)
            .where(activity_workout_step.c.activity_id == activity_id)
            .order_by(activity_workout_step.c.step_index)
        ).fetchall()
        expanded_steps = _expand_workout_steps(workout_steps)
        show_expected_columns = len(expanded_steps) > 0

        header = ["#"]
        if show_expected_columns:
            header.append("Interval")
        header.append("Duration")
        if show_expected_columns:
            header.append("Exp. pace or dist.")
        header.append("Distance")
        header.append("Pace" if pace_sport else "Speed")
        if row.sport == "running":
            header.append("GAP")
        if show_expected_columns:
            header.append("Expected pace")
        header += ["Avg HR", "Max HR"]

        rows_html = []
        for i, lp in enumerate(laps):
            lap_duration = lp.moving_duration_s or lp.duration_s
            expected = expanded_steps[i] if i < len(expanded_steps) else None
            cells = [str(i + 1)]
            if show_expected_columns:
                cells.append(_interval_label(expected.intensity) if expected else "—")
            cells.append(_format_duration(lap_duration))
            if show_expected_columns:
                cells.append(_expected_duration_label(expected) if expected else "—")
            cells.append(_format_km(lp.distance_m))
            cells.append(_format_pace_or_speed(lap_duration, lp.distance_m, pace_sport=pace_sport))
            if row.sport == "running":
                gap = lap_gaps[i] if i < len(lap_gaps) else None
                cells.append(_format_pace_from_speed_mps(gap) if gap else "—")
            if show_expected_columns:
                cells.append(
                    _expected_pace_label(expected, pace_sport=pace_sport) if expected else "—"
                )
            cells.append(f"{lp.avg_hr:.0f}" if lp.avg_hr is not None else "—")
            cells.append(f"{lp.max_hr:.0f}" if lp.max_hr is not None else "—")
            rows_html.append("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in cells) + "</tr>")
        table = (
            '<div class="table-scroll"><table class="tbl"><thead><tr>'
            + "".join(f"<th>{escape(h)}</th>" for h in header)
            + "</tr></thead><tbody>"
            + "".join(rows_html)
            + "</tbody></table></div>"
        )
        body_parts.append(_section("Intervals", table))

    # --- Bouldering: grade chart + routes table ---
    if is_bouldering:
        climb_splits = [
            s for s in splits if s.split_type == "climb_active" and s.climb_grade is not None
        ]
        if climb_splits:
            breakdown: dict[int, list[int]] = {}
            for s in climb_splits:
                bucket = breakdown.setdefault(s.climb_grade, [0, 0])
                if s.climb_result == "completed":
                    bucket[1] += 1
                else:
                    bucket[0] += 1
            grade_rows = [(g, a, c) for g, (a, c) in sorted(breakdown.items())]
            chart = _svg_grade_chart(grade_rows)
            if chart:
                body_parts.append(_section("Routes by grade", chart))

            table_rows = []
            for i, s in enumerate(climb_splits):
                is_completed = s.climb_result == "completed"
                badge_class = "badge--completed" if is_completed else "badge--attempt"
                # Same display mapping as frontend/src/boulderingRoutes.ts::formatResult --
                # "completed"/"attempt" are the only two confirmed raw values; anything else
                # (an unconfirmed "unknown_<n>") is shown as-is rather than guessed at.
                if is_completed:
                    badge_text = "Completed"
                elif s.climb_result == "attempt":
                    badge_text = "Attempt"
                else:
                    badge_text = escape(s.climb_result or "unknown")
                table_rows.append(
                    "<tr>"
                    f"<td>{i + 1}</td>"
                    f"<td>V{s.climb_grade}</td>"
                    f'<td><span class="badge {badge_class}">{badge_text}</span></td>'
                    f"<td>{_format_duration(s.duration_s)}</td>"
                    f"<td>{f'{s.climb_avg_hr:.0f}' if s.climb_avg_hr is not None else '—'}</td>"
                    "</tr>"
                )
            table = (
                '<div class="table-scroll"><table class="tbl"><thead><tr>'
                "<th>Route</th><th>Grade</th><th>Status</th><th>Duration</th><th>Avg HR</th>"
                "</tr></thead><tbody>" + "".join(table_rows) + "</tbody></table></div>"
            )
            body_parts.append(_section("Routes", table))

    # --- Secondary stats: training effect / power / running dynamics / temperature / respiration
    aerobic = metrics.get("fit.session.total_training_effect")
    anaerobic = metrics.get("fit.session.total_anaerobic_training_effect")
    has_training_effect = (
        aerobic is not None
        or anaerobic is not None
        or training_load is not None
        or workout_rpe is not None
        or vdot is not None
    )
    if has_training_effect:
        stats = []
        if aerobic is not None:
            stats.append(st("Aerobic effect", f"{aerobic:.1f}", "trend", "power"))
        if anaerobic is not None:
            stats.append(st("Anaerobic effect", f"{anaerobic:.1f}", "bolt", "power"))
        if training_load is not None:
            stats.append(st("Training load", f"{training_load:.0f}", "bolt", "load"))
        if workout_rpe is not None:
            stats.append(st("Perceived effort", f"{workout_rpe:.1f} RPE", "flame", "load"))
        if vdot is not None:
            stats.append(st("VDOT", f"{vdot:.1f}", "trend", "pace"))
        body_parts.append(_section("Training effect", _stats_grid_iconed(*stats)))

    avg_power = metrics.get("fit.session.avg_power")
    max_power = metrics.get("fit.session.max_power")
    normalized_power = metrics.get("fit.session.normalized_power")
    if avg_power is not None or max_power is not None or normalized_power is not None:
        stats = []
        if avg_power is not None:
            stats.append(st("Avg power", f"{avg_power:.0f} W", "bolt", "power"))
        if max_power is not None:
            stats.append(st("Max power", f"{max_power:.0f} W", "bolt", "power"))
        if normalized_power is not None:
            stats.append(st("Normalized power", f"{normalized_power:.0f} W", "bolt", "power"))
        body_parts.append(_section("Power", _stats_grid_iconed(*stats)))

    avg_vert_osc = metrics.get("fit.session.avg_vertical_oscillation")
    avg_stance = metrics.get("fit.session.avg_stance_time")
    avg_step_len = metrics.get("fit.session.avg_step_length")
    avg_vert_ratio = metrics.get("fit.session.avg_vertical_ratio")
    dynamics_fields = (cadence_raw, max_cadence_raw, avg_vert_osc, avg_stance, avg_step_len)
    if any(v is not None for v in dynamics_fields):
        stats = []
        if cadence_raw is not None:
            stats.append(st("Avg cadence", f"{cadence_raw * 2:.0f} spm", "steps", "cadence"))
        if max_cadence_raw is not None:
            stats.append(
                st("Max cadence", f"{max_cadence_raw * 2:.0f} spm", "steps", "cadence")
            )
        if avg_step_len is not None:
            stats.append(st("Step length", f"{avg_step_len / 10:.0f} cm", "route", "cadence"))
        if avg_stance is not None:
            stats.append(
                st("Ground contact time", f"{avg_stance:.0f} ms", "clock", "cadence")
            )
        if avg_vert_osc is not None:
            stats.append(
                st("Vertical oscillation", f"{avg_vert_osc:.1f} mm", "trend", "cadence")
            )
        if avg_vert_ratio is not None:
            stats.append(st("Vertical ratio", f"{avg_vert_ratio:.1f} %", "trend", "cadence"))
        body_parts.append(_section("Running dynamics", _stats_grid_iconed(*stats)))

    avg_temp = metrics.get("fit.session.avg_temperature")
    min_temp = metrics.get("fit.session.min_temperature")
    max_temp = metrics.get("fit.session.max_temperature")
    if avg_temp is not None:
        stats = [st("Avg temperature", f"{avg_temp:.0f} °C", "thermometer", "load")]
        if min_temp is not None and max_temp is not None:
            stats.append(
                st(
                    "Temperature range",
                    f"{min_temp:.0f}-{max_temp:.0f} °C",
                    "thermometer",
                    "load",
                )
            )
        body_parts.append(_section("Temperature", _stats_grid_iconed(*stats)))

    avg_resp = metrics.get("fit.session.enhanced_avg_respiration_rate")
    max_resp = metrics.get("fit.session.enhanced_max_respiration_rate")
    min_resp = metrics.get("fit.session.enhanced_min_respiration_rate")
    if avg_resp is not None or max_resp is not None:
        stats = []
        if avg_resp is not None:
            stats.append(st("Avg respiration", f"{avg_resp:.0f} brpm", "pulse", "cadence"))
        if max_resp is not None:
            stats.append(st("Max respiration", f"{max_resp:.0f} brpm", "pulse", "cadence"))
        if min_resp is not None:
            stats.append(st("Min respiration", f"{min_resp:.0f} brpm", "pulse", "cadence"))
        body_parts.append(_section("Respiration", _stats_grid_iconed(*stats)))

    # --- Time in zone: device-reported only -- see this file's own module docstring ---
    if row.sport in ("running", "cycling"):
        zones = _extract_hr_zones(metrics)
        if zones and any(z.seconds > 0 for z in zones):
            body_parts.append(_section("Time in zone", _time_in_zone_html(zones)))

    if chart_hover_needed:
        body_parts.append(_CHART_HOVER_SCRIPT)

    return _page(title=name, description=description, body="\n".join(body_parts))


# --- Period share -------------------------------------------------------------------------


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

    # Every non-deleted activity's own fields in range, fetched once -- used below for the
    # longest activity plus every per-sport-family breakdown (sport_family/is-bouldering have no
    # SQL equivalent, so that filtering runs in Python here, same cross-language-duplication
    # precedent as insights/rules_pb.py's own personalRecords logic).
    activity_query = select(
        activity.c.sport,
        activity.c.sub_sport,
        activity.c.distance_m,
        activity.c.moving_duration_s,
        activity.c.duration_s,
        activity.c.elevation_gain_m,
        activity.c.local_date,
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

    hiking_body = ""
    hikes = [a for a in activities if sport_family(a.sport) == "hike"]
    if hikes:
        hikes_with_distance = [a for a in hikes if a.distance_m]
        total_hike_m = sum(a.distance_m or 0 for a in hikes_with_distance)
        total_hike_s = sum((a.moving_duration_s or a.duration_s or 0) for a in hikes)
        gains = [a.elevation_gain_m for a in hikes if a.elevation_gain_m is not None]
        max_gain = max(gains, default=None)
        max_gain_hike = (
            next((a for a in hikes if a.elevation_gain_m == max_gain), None)
            if max_gain is not None
            else None
        )
        hiking_pairs = [
            ("Hikes", str(len(hikes))),
            ("Total distance", f"{total_hike_m / 1000:.1f} km" if hikes_with_distance else "—"),
            ("Total time", _format_duration(total_hike_s)),
            ("Average elevation gain", f"{sum(gains) / len(gains):.0f} m" if gains else "—"),
        ]
        if max_gain_hike is not None:
            hiking_pairs.append(
                ("Max elevation gain", f"{max_gain:.0f} m on {max_gain_hike.local_date}")
            )
        hiking_body = f"<h2>Hiking</h2>\n{_stats_grid(*hiking_pairs)}\n"

    climbing_body = ""
    climb_where = [
        activity.c.athlete_id == athlete_id,
        activity.c.deleted_at.is_(None),
        activity.c.sub_sport == "bouldering",
    ]
    if start is not None and end is not None:
        climb_where.append(activity.c.local_date.between(start, end))
    session_count_query = select(func.count()).select_from(activity).where(*climb_where)
    session_count = conn.execute(session_count_query).scalar_one()
    if session_count:
        climb_splits = conn.execute(
            select(split_table.c.climb_grade, split_table.c.climb_result, split_table.c.duration_s)
            .select_from(split_table.join(activity, activity.c.id == split_table.c.activity_id))
            .where(*climb_where, split_table.c.climb_grade.is_not(None))
        ).fetchall()
        total_climb_time_s = sum(s.duration_s for s in climb_splits if s.duration_s is not None)
        completed_grades = [s.climb_grade for s in climb_splits if s.climb_result == "completed"]
        breakdown: dict[int, list[int]] = {}
        for s in climb_splits:
            bucket = breakdown.setdefault(s.climb_grade, [0, 0])
            if s.climb_result == "completed":
                bucket[1] += 1
            else:
                bucket[0] += 1
        climbing_pairs = [
            ("Sessions", str(session_count)),
            ("Climb time", _format_duration(total_climb_time_s)),
            ("Routes", str(len(climb_splits))),
            ("Max grade completed", f"V{max(completed_grades)}" if completed_grades else "—"),
        ]
        grade_chart = _svg_grade_chart([(g, a, c) for g, (a, c) in sorted(breakdown.items())])
        climbing_body = f"<h2>Climbing</h2>\n{_stats_grid(*climbing_pairs)}\n{grade_chart}\n"

    fitness_training_body = ""
    fitness_sessions = [
        a for a in activities if sport_family(a.sport) == "strength" or a.sport == "hiit"
    ]
    if fitness_sessions:
        total_s = sum((a.moving_duration_s or a.duration_s or 0) for a in fitness_sessions)
        fitness_pairs = [
            ("Sessions", str(len(fitness_sessions))),
            ("Total time", _format_duration(total_s)),
            ("Average session length", _format_duration(total_s / len(fitness_sessions))),
        ]
        fitness_training_body = f"<h2>Fitness</h2>\n{_stats_grid(*fitness_pairs)}\n"

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
    fitness_form_body = ""
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
            fitness_form_body = (
                "<h2>Fitness &amp; Form</h2>\n"
                '<div class="chart-legend"><span class="legend-dot" '
                'style="background:var(--color-elevation)"></span>Fitness (CTL) &nbsp; '
                '<span class="legend-dot" style="background:var(--color-load)"></span>'
                "Fatigue (ATL)</div>\n"
                f"{chart}\n"
            )

    body = f"""
<h1>{escape(label)}</h1>
<div class="meta">Activity summary</div>
{stats}
{running_body}{hiking_body}{climbing_body}{fitness_training_body}{fitness_form_body}{months_body}"""
    return _page(title=f"{label} summary", description=description, body=body)
