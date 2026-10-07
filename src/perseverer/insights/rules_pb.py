"""PB-proximity insights: for each sport family and each whole-km distance an athlete has
actually run, is the window's own fastest activity at that distance still the athlete's
all-time best? Bands are whole kilometres by floor, not a percentage-tolerance match against a
fixed list of race distances -- a 5.01km run and a 5.99km run both count as "5 km", but a
4.99km run does not (see `_floor_km_band`). Bands are derived dynamically from whatever
distances are actually present in the activity pool, not a fixed table, so any distance the
athlete has genuinely run (5K, 10K, a 17km training run, a 50km ultra) gets its own real band.

Deliberately not shared with frontend/src/runningStats.ts::personalRecords, which still uses
its own percentage-tolerance match against a fixed list of standard race distances
(docs/ARCHITECTURE.md
already keeps these two implementations separate) -- personalRecords answers "how does this
activity compare to my best near this distance", a different question from "what's my best at
exactly this many km".

An honest approximation, not a true "best effort" sliding-window extraction from the full
per-second stream -- it picks the fastest *whole recorded activity* in the band.

`compute_window_best_insights` is the deliberately weaker sibling of `compute_pb_insights`:
"fastest in this window" even when it isn't the athlete's all-time best in that band -- e.g. an
ordinary 6K that happens to be the only (or fastest of a handful of) ~6K runs in the last 30
days, while a genuinely faster 6K sits further back in history. `compute_pb_insights` correctly
stays silent for a claim that weak, but the athlete still reasonably expects *some* insight for
"fastest in the last month," so this is a separate, honestly-labelled kind rather than loosening
what "personal best" means.
"""

from __future__ import annotations

from datetime import date

from perseverer.insights.rules_efforts import WINDOWS, window_start_date
from perseverer.insights.types import Insight, InsightActivity


def _effective_duration_s(a: InsightActivity) -> float | None:
    return a.moving_duration_s if a.moving_duration_s is not None else a.duration_s


def _floor_km_band(distance_m: float) -> int | None:
    """Whole-km bucket a distance falls into by floor -- 5010m and 5990m both bucket to 5,
    4990m does not. None below 1km: too short for a meaningful "fastest N km" claim."""
    band = int(distance_m // 1000)
    return band if band >= 1 else None


def _band_best(activities: list[InsightActivity], band_km: int) -> InsightActivity | None:
    eligible = [
        a
        for a in activities
        if a.distance_m is not None
        and _floor_km_band(a.distance_m) == band_km
        and _effective_duration_s(a) is not None
    ]
    if not eligible:
        return None

    def pace(a: InsightActivity) -> float:
        duration = _effective_duration_s(a)
        assert duration is not None and a.distance_m is not None
        return duration / a.distance_m

    return min(eligible, key=pace)


def _bands_present(activities: list[InsightActivity]) -> list[int]:
    bands = {
        b
        for a in activities
        if a.distance_m is not None and (b := _floor_km_band(a.distance_m)) is not None
    }
    return sorted(bands)


def compute_pb_insights(activities: list[InsightActivity], as_of: date) -> list[Insight]:
    """All-time best pace per sport family and whole-km distance band."""
    insights: list[Insight] = []
    families = sorted({a.sport_family for a in activities})
    for family in families:
        family_activities = [a for a in activities if a.sport_family == family]
        for band_km in _bands_present(family_activities):
            label = f"{band_km} km"
            all_time_best = _band_best(family_activities, band_km)
            if all_time_best is None:
                continue
            for window, days in WINDOWS:
                start = window_start_date(window, days, as_of)
                windowed = [
                    a
                    for a in family_activities
                    if start.isoformat() <= a.local_date <= as_of.isoformat()
                ]
                window_best = _band_best(windowed, band_km)
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


def compute_window_best_insights(activities: list[InsightActivity], as_of: date) -> list[Insight]:
    """Same distance-band matching as `compute_pb_insights`, but for the window's own fastest
    effort in that band even when it ISN'T the athlete's all-time best there -- see this module's
    own docstring for why that distinction is worth a separate, honestly-labelled insight kind
    ("window_best") rather than folding it into "pb". Skips a window entirely when its own
    band-best *is* the all-time best -- that case is already `compute_pb_insights`'s job, and
    "fastest in the last 30 days" would be a redundant, weaker echo of "all-time best" for the
    same activity. rules_activity.py's own relabeling adds the period-explicit phrasing
    ("... in the last 30 days") for the per-activity panel; this module's default title is used
    as-is by the athlete-wide persisted table.
    """
    insights: list[Insight] = []
    families = sorted({a.sport_family for a in activities})
    for family in families:
        family_activities = [a for a in activities if a.sport_family == family]
        for band_km in _bands_present(family_activities):
            label = f"{band_km} km"
            all_time_best = _band_best(family_activities, band_km)
            if all_time_best is None:
                continue
            for window, days in WINDOWS:
                start = window_start_date(window, days, as_of)
                windowed = [
                    a
                    for a in family_activities
                    if start.isoformat() <= a.local_date <= as_of.isoformat()
                ]
                window_best = _band_best(windowed, band_km)
                if window_best is None or window_best.id == all_time_best.id:
                    continue
                duration = _effective_duration_s(window_best)
                insights.append(
                    Insight(
                        kind="window_best",
                        window=window,
                        subject_key=f"window_best:{family}:{label}",
                        title=f"Fastest {label} ({family})",
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
