"""Notable-effort extremes: for each of five time windows, find the activity that's the
max/min for each of a fixed set of real, already-stored dimensions -- distance, duration, pace,
avg/max heart rate (both directions), avg/max cadence, elevation gained/lost, highest point
reached, calories burned, start-time-of-day
(earliest/latest), and outside temperature (hottest/coldest). One generic `find_extreme`
function driven by a declarative `_DIMENSIONS` table, not one hand-written function per
combination -- see ADR 0012.

Distance/duration/pace/HR/cadence/elevation dimensions are scoped within the same sport family
(comparing a hike's elevation gain to a run's is not a meaningful "record") -- one insight per
sport family present in the window. Temperature is not sport-scoped --
one insight across all of that window's activities regardless of sport.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from perseverer.insights.types import Insight, InsightActivity

WINDOWS: tuple[tuple[str, int | None], ...] = (
    ("30d", 30),
    ("90d", 90),
    ("180d", 180),
    ("year", None),  # calendar-year-to-date, handled specially below
    ("365d", 365),
)


def _effective_duration_s(a: InsightActivity) -> float | None:
    return a.moving_duration_s if a.moving_duration_s is not None else a.duration_s


def _pace_s_per_m(a: InsightActivity) -> float | None:
    duration = _effective_duration_s(a)
    if duration is None or a.distance_m is None or a.distance_m <= 0:
        return None
    return duration / a.distance_m


def _start_hour(a: InsightActivity) -> float | None:
    # Local time-of-day, not raw UTC -- comparing bare UTC hours across activities logged in
    # different local timezones (or a source's bogus midnight-UTC placeholder timestamp for an
    # incomplete manual entry) produces a nonsense "earliest start" winner. Confirmed against
    # real data: a genuine early-morning marathon start lost to a "training" entry timestamped
    # 00:01 UTC purely because the comparison never converted to local time at all.
    local = a.start_time_utc + timedelta(seconds=a.utc_offset_s)
    return local.hour + local.minute / 60


@dataclass(frozen=True)
class _Dimension:
    key: str
    title: str
    direction: str  # "high" | "low"
    value_fn: Callable[[InsightActivity], float | None]
    sport_scoped: bool
    unit: str | None = None


_DIMENSIONS: tuple[_Dimension, ...] = (
    _Dimension("distance", "Longest distance", "high", lambda a: a.distance_m, True, "m"),
    _Dimension("duration", "Longest duration", "high", _effective_duration_s, True, "s"),
    _Dimension("pace", "Fastest pace", "low", _pace_s_per_m, True, "s_per_m"),
    _Dimension(
        "avg_hr_high", "Highest average heart rate", "high", lambda a: a.avg_hr, True, "bpm"
    ),
    _Dimension("avg_hr_low", "Lowest average heart rate", "low", lambda a: a.avg_hr, True, "bpm"),
    _Dimension("max_hr_high", "Highest heart rate", "high", lambda a: a.max_hr, True, "bpm"),
    _Dimension("max_hr_low", "Lowest peak heart rate", "low", lambda a: a.max_hr, True, "bpm"),
    _Dimension("cadence", "Highest average cadence", "high", lambda a: a.cadence, True, "spm"),
    _Dimension("max_cadence", "Highest max cadence", "high", lambda a: a.max_cadence, True, "spm"),
    _Dimension(
        "elevation_gain", "Most elevation gained", "high", lambda a: a.elevation_gain_m, True, "m"
    ),
    _Dimension(
        "elevation_loss", "Most elevation lost", "high", lambda a: a.elevation_loss_m, True, "m"
    ),
    _Dimension(
        "max_altitude", "Highest point reached", "high", lambda a: a.max_altitude_m, True, "m"
    ),
    _Dimension("calories", "Most calories burned", "high", lambda a: a.calories, True, "kcal"),
    # Sport-scoped like the other per-activity dimensions: a late bike ride must not hide the
    # latest *run* (the run page compares a run only against other runs).
    _Dimension("start_earliest", "Earliest start", "low", _start_hour, True, "hour"),
    _Dimension("start_latest", "Latest start", "high", _start_hour, True, "hour"),
    _Dimension(
        "temperature_high", "Hottest conditions", "high", lambda a: a.temperature_max_c, False, "c"
    ),
    _Dimension(
        "temperature_low", "Coldest conditions", "low", lambda a: a.temperature_min_c, False, "c"
    ),
)

# Public alias -- rules_activity.py reuses these dimension definitions (with find_extreme,
# already public) to check whether an activity is the true all-time extreme, not just the
# winner within one of the five fixed windows above.
DIMENSIONS = _DIMENSIONS


def window_start_date(window: str, days: int | None, as_of: date) -> date:
    if days is not None:
        return as_of - timedelta(days=days)
    return date(as_of.year, 1, 1)  # "year": calendar-year-to-date


def _activities_in_window(
    activities: list[InsightActivity], window: str, days: int | None, as_of: date
) -> list[InsightActivity]:
    start = window_start_date(window, days, as_of)
    return [a for a in activities if start.isoformat() <= a.local_date <= as_of.isoformat()]


def _extreme(
    candidates: list[InsightActivity], dim: _Dimension
) -> tuple[InsightActivity, float] | None:
    scored: list[tuple[InsightActivity, float]] = [
        (a, v) for a in candidates if (v := dim.value_fn(a)) is not None
    ]
    if not scored:
        return None
    if dim.direction == "low":
        return min(scored, key=lambda pair: pair[1])
    return max(scored, key=lambda pair: pair[1])


def find_extreme(activities: list[InsightActivity], dim: _Dimension, window: str) -> list[Insight]:
    """One Insight per sport family present, for a sport-scoped dimension; at most one Insight
    overall for a non-sport-scoped dimension. Never fabricates an extreme when nothing in the
    window has a value for this dimension (AGENTS.md's never-invent-a-number rule) -- returns an
    empty list, not a zero/null placeholder row.
    """
    results: list[Insight] = []
    if dim.sport_scoped:
        families = sorted({a.sport_family for a in activities})
        for family in families:
            group = [a for a in activities if a.sport_family == family]
            found = _extreme(group, dim)
            if found is None:
                continue
            activity, value = found
            results.append(
                Insight(
                    kind="effort",
                    window=window,
                    subject_key=f"{dim.key}:{family}",
                    title=f"{dim.title} ({family})",
                    detail={"activity_id": activity.id, "unit": dim.unit, "sport": activity.sport},
                    value_num=value,
                    sport_family=family,
                    activity_id=activity.id,
                    local_date=activity.local_date,
                )
            )
    else:
        found = _extreme(activities, dim)
        if found is not None:
            activity, value = found
            results.append(
                Insight(
                    kind="effort",
                    window=window,
                    subject_key=dim.key,
                    title=dim.title,
                    detail={"activity_id": activity.id, "unit": dim.unit, "sport": activity.sport},
                    value_num=value,
                    activity_id=activity.id,
                    local_date=activity.local_date,
                )
            )
    return results


def compute_effort_insights(activities: list[InsightActivity], as_of: date) -> list[Insight]:
    insights: list[Insight] = []
    for window, days in WINDOWS:
        windowed = _activities_in_window(activities, window, days, as_of)
        for dim in _DIMENSIONS:
            insights.extend(find_extreme(windowed, dim, window))
    return insights
