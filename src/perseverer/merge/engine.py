"""Pure activity-matching logic: is a newly-parsed activity the same real-world activity as
one already in the database?

This is the merge engine *core* — used from Phase 1 onward (even with a single source,
duplicate files happen: re-exports, corrected re-syncs) and becomes the full cross-source
reconciliation engine from Phase 2+ once garmin_connect/strava_export exist. The rule below
is the project spec's own definition, verbatim: two records are the same activity when
|Δstart_time| <= 180s AND sport families match AND |Δduration| <= max(60s, 5%).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# Maps a vendor-specific sport string to a broad family so "running" (Garmin FIT) and "run"
# (a future Strava import) are recognized as the same kind of activity. A sport missing from
# this table falls back to itself (lowercased) — it simply won't match anything until added
# here, rather than raising or silently matching everything.
_SPORT_FAMILIES: dict[str, str] = {
    "running": "run",
    "trail_running": "run",
    "treadmill_running": "run",
    "track_running": "run",
    "street_running": "run",
    "cycling": "ride",
    "road_biking": "ride",
    "mountain_biking": "ride",
    "gravel_cycling": "ride",
    "indoor_cycling": "ride",
    "virtual_ride": "ride",
    "e_bike_fitness": "ride",
    "swimming": "swim",
    "lap_swimming": "swim",
    "open_water_swimming": "swim",
    "walking": "walk",
    "casual_walking": "walk",
    "hiking": "hike",
    "strength_training": "strength",
    "cardio_training": "cardio",
    "elliptical": "cardio",
    "rowing": "row",
    "indoor_rowing": "row",
}


def sport_family(sport: str) -> str:
    return _SPORT_FAMILIES.get(sport.lower(), sport.lower())


# Family pairs that count as the same activity for *merge-matching* purposes only, without
# collapsing them into one family for anything else (insights/engine.py's own use of
# sport_family() groups streaks/PBs by family, and a genuine walk shouldn't start counting
# toward a hiking streak just because merge-matching got more lenient). Real-data-confirmed
# gap: several of this athlete's own casual/easy hikes are classified "Hike" by Garmin/FIT but
# "Walk" by Strava's own auto-detection for the exact same recording, so the strict family-
# equality check below silently left cross-source duplicates unmerged even with byte-identical
# start time and duration.
_MERGE_COMPATIBLE_FAMILIES: frozenset[frozenset[str]] = frozenset({frozenset({"hike", "walk"})})


def _sport_families_compatible(family_a: str, family_b: str) -> bool:
    return family_a == family_b or frozenset({family_a, family_b}) in _MERGE_COMPATIBLE_FAMILIES


@dataclass(frozen=True)
class MergeThresholds:
    max_start_time_delta_s: float = 180.0
    max_duration_delta_s: float = 60.0
    max_duration_delta_pct: float = 0.05


DEFAULT_MERGE_THRESHOLDS = MergeThresholds()


@dataclass(frozen=True)
class ActivityCandidate:
    start_time_utc: datetime
    duration_s: float | None
    sport: str


@dataclass(frozen=True)
class MergeDecision:
    is_match: bool
    reasons: list[str] = field(default_factory=list)
    inputs: dict[str, object] = field(default_factory=dict)


def _as_naive_utc(value: datetime) -> datetime:
    """Normalizes to a naive-but-implicitly-UTC datetime.

    Callers may pass a value freshly produced by a parser (timezone-aware, per FIT/garmin_fit_sdk)
    or one read back from SQLite (naive — see db/schema.py's naive-UTC convention; SQLAlchemy's
    SQLite dialect does not round-trip tzinfo). Comparing the two directly raises `TypeError:
    can't subtract offset-naive and offset-aware datetimes`, which is exactly what happened on
    real data during Phase 1 development once merge-matching had an existing activity to compare
    against. Normalizing both sides here, once, closes that off regardless of which shape either
    side arrives in.
    """
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def is_same_activity(
    a: ActivityCandidate,
    b: ActivityCandidate,
    thresholds: MergeThresholds = DEFAULT_MERGE_THRESHOLDS,
) -> MergeDecision:
    reasons = []

    a_start = _as_naive_utc(a.start_time_utc)
    b_start = _as_naive_utc(b.start_time_utc)
    start_delta_s = abs((a_start - b_start).total_seconds())
    start_ok = start_delta_s <= thresholds.max_start_time_delta_s
    reasons.append(
        f"start_time delta {start_delta_s:.0f}s "
        f"{'<=' if start_ok else '>'} {thresholds.max_start_time_delta_s:.0f}s"
    )

    family_a = sport_family(a.sport)
    family_b = sport_family(b.sport)
    sport_ok = _sport_families_compatible(family_a, family_b)
    reasons.append(
        f"sport family {family_a!r} vs {family_b!r}: {'match' if sport_ok else 'mismatch'}"
    )

    if a.duration_s is None or b.duration_s is None:
        duration_ok = True  # can't compare — missing data shouldn't block an otherwise-good match
        reasons.append("duration comparison skipped (missing data on one side)")
    else:
        duration_delta_s = abs(a.duration_s - b.duration_s)
        allowed = max(
            thresholds.max_duration_delta_s,
            thresholds.max_duration_delta_pct * max(a.duration_s, b.duration_s),
        )
        duration_ok = duration_delta_s <= allowed
        reasons.append(
            f"duration delta {duration_delta_s:.0f}s "
            f"{'<=' if duration_ok else '>'} allowed {allowed:.0f}s"
        )

    return MergeDecision(
        is_match=start_ok and sport_ok and duration_ok,
        reasons=reasons,
        inputs={
            "a_start": a.start_time_utc.isoformat(),
            "b_start": b.start_time_utc.isoformat(),
            "a_duration_s": a.duration_s,
            "b_duration_s": b.duration_s,
            "a_sport": a.sport,
            "b_sport": b.sport,
        },
    )
