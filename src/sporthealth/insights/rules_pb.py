"""PB-proximity insights: for each sport family and each standard race distance, is the
window's own fastest whole activity in that distance band still the athlete's all-time best?
Ports the same band-matching logic frontend/src/runningStats.ts::personalRecords already uses
and has been verified against real data with (STANDARD_DISTANCES, a 0.9x-1.3x tolerance band,
fastest-effective-pace-in-band wins) -- ADR 0012 deliberately keeps the two implementations
separate rather than sharing code across the Python/TypeScript boundary.

An honest approximation, not a true "best effort" sliding-window extraction from the full
per-second stream (same caveat as the TS version) -- it picks the fastest *whole recorded
activity* within a tolerance band of the target distance.
"""

from __future__ import annotations

from datetime import date

from sporthealth.insights.rules_efforts import WINDOWS, window_start_date
from sporthealth.insights.types import Insight, InsightActivity

STANDARD_DISTANCES: tuple[tuple[str, float], ...] = (
    ("1 mile", 1609.34),
    ("3 km", 3000.0),
    ("5 km", 5000.0),
    ("4 mile", 6437.38),
    ("5 mile", 8046.72),
    ("10 km", 10000.0),
    ("15 km", 15000.0),
    ("10 mile", 16093.4),
    ("20 km", 20000.0),
    ("Half marathon", 21097.5),
    ("Marathon", 42195.0),
)

_BAND_LOW = 0.9
_BAND_HIGH = 1.3


def _effective_duration_s(a: InsightActivity) -> float | None:
    return a.moving_duration_s if a.moving_duration_s is not None else a.duration_s


def _band_best(activities: list[InsightActivity], meters: float) -> InsightActivity | None:
    low, high = meters * _BAND_LOW, meters * _BAND_HIGH
    eligible = [
        a
        for a in activities
        if a.distance_m is not None
        and low <= a.distance_m <= high
        and _effective_duration_s(a) is not None
    ]
    if not eligible:
        return None

    def pace(a: InsightActivity) -> float:
        duration = _effective_duration_s(a)
        assert duration is not None and a.distance_m is not None
        return duration / a.distance_m

    return min(eligible, key=pace)


def compute_pb_insights(activities: list[InsightActivity], as_of: date) -> list[Insight]:
    insights: list[Insight] = []
    families = sorted({a.sport_family for a in activities})
    for family in families:
        family_activities = [a for a in activities if a.sport_family == family]
        for label, meters in STANDARD_DISTANCES:
            all_time_best = _band_best(family_activities, meters)
            if all_time_best is None:
                continue
            for window, days in WINDOWS:
                start = window_start_date(window, days, as_of)
                windowed = [
                    a
                    for a in family_activities
                    if start.isoformat() <= a.local_date <= as_of.isoformat()
                ]
                window_best = _band_best(windowed, meters)
                if window_best is None or window_best.id != all_time_best.id:
                    continue
                duration = _effective_duration_s(window_best)
                insights.append(
                    Insight(
                        kind="pb",
                        window=window,
                        subject_key=f"pb:{family}:{label}",
                        title=f"All-time best {label} ({family})",
                        detail={
                            "distance_label": label,
                            "activity_id": window_best.id,
                            "distance_m": window_best.distance_m,
                            "duration_s": duration,
                        },
                        value_num=duration,
                        sport_family=family,
                        activity_id=window_best.id,
                        local_date=window_best.local_date,
                    )
                )
    return insights
