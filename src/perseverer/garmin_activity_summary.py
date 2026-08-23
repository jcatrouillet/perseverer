"""Corrects `activity.sport`/`activity.name` using Garmin's own corrected classification,
sourced from the account export's `summarizedActivitiesExport` JSON
(`DI_CONNECT/DI-Connect-Fitness/*_summarizedActivities.json`) -- archived raw at ingest time
(`garmin_export.py`) but never parsed until now.

Real, confirmed need (not hypothetical): a FIT file's own `sport`/`sub_sport` fields can simply
be wrong -- e.g. a hike recorded with the watch's "Run" profile still selected. Garmin Connect's
own UI lets the athlete fix this after the fact (reclassify the sport, give the activity a real
title), but that correction lives only in Garmin's cloud-side activity record, never written
back into the original on-device FIT bytes. Confirmed against this athlete's real export: 4
activities the FIT parser stored as `sport=running` are, in this same export's own
`summarizedActivitiesExport`, real named hikes ("Sands Cave Hike", "Bryce Canyon - Mossy Cave",
etc.) with `activityType=hiking` -- matched by timestamp to within 43 seconds of the FIT file's
own recorded start.

There's no direct foreign key between our `activity.id` (a ULID we mint) and Garmin's own numeric
`activityId` for garmin_export-sourced activities (unlike `garmin_connect`, which stores it as
`activity_source_link.external_id` -- see that adapter's own `garmin_connect_json` archival).
Entries are matched to activity rows by closest `start_time_utc` within a tight per-athlete
tolerance instead, both timestamps ultimately coming from the same on-device clock.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, select

from perseverer.archive import read_raw_bytes
from perseverer.db.schema import activity, raw_object

# A timestamp mismatch this large means "probably not the same activity" -- real observed
# deltas (FIT-recorded start vs. Garmin's own beginTimestamp for the same activity) were 0-43s;
# 120s leaves comfortable margin without risking a false match between two genuinely different
# activities that happen to start close together (rare for one athlete, but not impossible on a
# multi-activity day).
_MATCH_TOLERANCE_S = 120.0

# Every real `activityType` value seen in this athlete's own export (confirmed via direct
# inspection, not assumed), mapped to this project's own (sport, sub_sport) taxonomy. Several
# entries deliberately fold into an (sport, sub_sport) pair this archive's FIT parser already
# produces elsewhere (e.g. "treadmill_running" -> the same "treadmill" sub_sport 8 real
# FIT-sourced activities already use, rather than a near-duplicate "treadmill_running" value;
# "breathwork" -> this archive's existing "breathing" sub_sport, not Garmin's own spelling) so a
# corrected activity reads consistently with ones that were already classified correctly.
# "other" (1 real occurrence) is deliberately omitted -- too ambiguous to correct confidently.
GARMIN_ACTIVITY_TYPE_MAP: dict[str, tuple[str, str]] = {
    "running": ("running", "generic"),
    "street_running": ("running", "generic"),
    "trail_running": ("running", "trail_running"),
    "treadmill_running": ("running", "treadmill"),
    "walking": ("walking", "generic"),
    "casual_walking": ("walking", "generic"),
    "yoga": ("training", "yoga"),
    "breathwork": ("training", "breathing"),
    "strength_training": ("training", "strength_training"),
    "hiking": ("hiking", "generic"),
    "bouldering": ("rock_climbing", "bouldering"),
    "pilates": ("fitness_equipment", "pilates"),
    "elliptical": ("fitness_equipment", "elliptical"),
    "cycling": ("cycling", "generic"),
    "road_biking": ("cycling", "generic"),
    "gravel_cycling": ("cycling", "generic"),
    "virtual_ride": ("cycling", "virtual_activity"),
    "indoor_cycling": ("cycling", "indoor_cycling"),
    "hiit": ("hiit", "generic"),
    "indoor_rowing": ("rowing", "indoor_rowing"),
    "resort_skiing": ("alpine_skiing", "generic"),
    "snow_shoe_ws": ("snowshoeing", "generic"),
    "squash": ("racket", "squash"),
}


# Garmin Connect's own event-type classification -- confirmed against this athlete's real
# export (not assumed from any documentation): eventTypeId 1 matches exclusively real named
# races ("Bay to Breakers", "Golden Gate Half Marathon", "JP Morgan Corporate Challenge 2023",
# etc., 12 of 13 occurrences); 9 is the overwhelming default ("uncategorized", 1709
# occurrences); 4 occurred once, on a plain "Long run". Nothing here is FIT-derivable -- a FIT
# file has no concept of "this was a race", only Garmin Connect's own activity record does.
GARMIN_RACE_EVENT_TYPE_ID = 1


@dataclass(frozen=True)
class GarminActivitySummaryEntry:
    garmin_activity_id: int | None
    name: str | None
    activity_type: str | None
    event_type_id: int | None
    # Naive, implicitly UTC -- matches every other DateTime in this codebase (CLAUDE.md decision
    # 10), so it compares directly against `activity.start_time_utc` with no tzinfo juggling.
    begin_timestamp_utc: datetime


@dataclass
class CorrectionResult:
    corrected: int = 0
    matched_no_change: int = 0
    unmatched_entries: int = 0
    corrections: list[tuple[str, str | None, str, str | None, str]] = field(default_factory=list)
    """(activity_id, old_sport, new_sport, old_name, new_name) for each row actually changed --
    small enough to log/print, useful for a human to spot-check a backfill run."""


def parse_summarized_activities_json(content: bytes) -> list[GarminActivitySummaryEntry]:
    """Pure parse of one `*_summarizedActivities.json` blob's real shape (confirmed against a
    real export): a top-level list of blocks, each with a `summarizedActivitiesExport` list of
    per-activity dicts. `beginTimestamp` is epoch milliseconds, UTC (cross-checked against the
    FIT-recorded start_time_utc for several real activities during development)."""
    data = json.loads(content)
    entries: list[GarminActivitySummaryEntry] = []
    for block in data:
        for raw_entry in block.get("summarizedActivitiesExport", []):
            begin_ts = raw_entry.get("beginTimestamp")
            if begin_ts is None:
                continue
            entries.append(
                GarminActivitySummaryEntry(
                    garmin_activity_id=raw_entry.get("activityId"),
                    name=raw_entry.get("name"),
                    activity_type=raw_entry.get("activityType"),
                    event_type_id=raw_entry.get("eventTypeId"),
                    begin_timestamp_utc=datetime.fromtimestamp(
                        begin_ts / 1000, tz=UTC
                    ).replace(tzinfo=None),
                )
            )
    return entries


def correct_activities_from_summary(
    conn: Connection, *, athlete_id: str, entries: list[GarminActivitySummaryEntry]
) -> CorrectionResult:
    """Matches each summary entry to this athlete's closest activity by start time (within
    `_MATCH_TOLERANCE_S`) and, when matched, corrects `sport`/`sub_sport` (only when
    `activity_type` maps to something different from what's stored) and `is_race` (only when
    `eventTypeId` is known and differs from what's stored -- see GARMIN_RACE_EVENT_TYPE_ID).
    `name` is always overwritten with Garmin's own name when it differs from what's stored --
    an explicit athlete directive, not an inferred heuristic: Garmin Connect's own `name` is
    *not* reliably a real athlete-given title (confirmed against this athlete's real archive --
    it can be just as generic a location+activity-type auto-template as the FIT-derived default,
    e.g. "Santa Clara Other" for hundreds of dissimilar, unrelated activities across years, which
    replaces an already-more-specific stored name like "Morning Run" with a less informative
    one), but the athlete has chosen to always prefer whatever Garmin Connect itself shows
    regardless. Never touches an activity with no matching entry, and never invents a mapping
    for an unrecognized `activity_type`.

    When more than one entry in `entries` matches the same activity (e.g. two archived export
    snapshots both cover it, possibly disagreeing -- see `backfill_activity_corrections`'s own
    docstring for a real example), only the *last* one in `entries`' own order governs that
    activity, resolved in a first pass before any update is applied. This is what makes a
    matched activity's outcome depend only on that single winning entry compared against its own
    original stored row -- a real, confirmed bug otherwise: applying each matching entry's
    update immediately, one at a time, made a later entry's own comparison run against whatever
    an *earlier* entry in the same call had just written, so a later entry whose name happened to
    already equal that intermediate value looked like a no-op and got silently skipped, instead
    of being correctly recognized as the entry that should win.
    """
    rows = conn.execute(
        select(
            activity.c.id,
            activity.c.start_time_utc,
            activity.c.sport,
            activity.c.sub_sport,
            activity.c.name,
            activity.c.is_race,
        )
        .where(activity.c.athlete_id == athlete_id, activity.c.deleted_at.is_(None))
        .order_by(activity.c.start_time_utc)
    ).fetchall()
    result = CorrectionResult()
    if not rows:
        return result

    starts = [r.start_time_utc for r in rows]

    winning_entry_by_idx: dict[int, GarminActivitySummaryEntry] = {}
    for entry in entries:
        target = entry.begin_timestamp_utc
        idx = bisect.bisect_left(starts, target)
        candidates = [i for i in (idx - 1, idx) if 0 <= i < len(starts)]
        if not candidates:
            result.unmatched_entries += 1
            continue
        best_idx = min(candidates, key=lambda i: abs((starts[i] - target).total_seconds()))
        if abs((starts[best_idx] - target).total_seconds()) > _MATCH_TOLERANCE_S:
            result.unmatched_entries += 1
            continue
        # Plain reassignment -- iterating `entries` in order means the last entry to match this
        # index is the one left standing once the loop finishes.
        winning_entry_by_idx[best_idx] = entry

    now = datetime.now(UTC).replace(tzinfo=None)
    for best_idx, entry in winning_entry_by_idx.items():
        row = rows[best_idx]
        mapped = (
            GARMIN_ACTIVITY_TYPE_MAP.get(entry.activity_type) if entry.activity_type else None
        )
        updates: dict[str, object] = {}
        if mapped is not None and (row.sport, row.sub_sport) != mapped:
            updates["sport"], updates["sub_sport"] = mapped

        is_race = (
            entry.event_type_id == GARMIN_RACE_EVENT_TYPE_ID
            if entry.event_type_id is not None
            else None
        )
        if is_race is not None and is_race != row.is_race:
            updates["is_race"] = is_race

        if entry.name and entry.name != row.name:
            updates["name"] = entry.name

        if not updates:
            result.matched_no_change += 1
            continue

        conn.execute(
            activity.update().where(activity.c.id == row.id).values(**updates, updated_at=now)
        )
        result.corrected += 1
        result.corrections.append(
            (
                row.id,
                row.sport,
                str(updates.get("sport", row.sport)),
                row.name,
                str(updates.get("name", row.name)) if updates.get("name") else row.name,
            )
        )

    return result


def backfill_activity_corrections(
    conn: Connection, archive_root: Path, *, athlete_id: str
) -> CorrectionResult:
    """Re-derives corrections directly from whatever `summarizedActivitiesExport` blobs are
    already in this athlete's raw archive -- works standalone as a one-off backfill for
    already-ingested data (no need to re-run a full garmin-export import), and is what
    `import_garmin_export` itself calls after each run so future imports self-correct too.

    An athlete can have more than one `*_summarizedActivities.json` archived (e.g. two separate
    GDPR export requests taken months apart), and they can genuinely disagree about one
    activity's own auto-generated name (confirmed live: one real activity had "Monterey County
    Hiking" in an older snapshot and "Monterey County Other" in a newer one -- both Garmin's own
    generic location+type auto-template, not a real athlete-given title, so neither is "more
    correct" on its own terms). `correct_activities_from_summary` below applies every matching
    entry it's given in order and lets the last one win, so which snapshot's name survives used
    to depend on SQLite's unspecified row order for an unordered query -- a real, confirmed bug:
    repeated runs of `sync correct-garmin-activities` against the same archive flip-flopped that
    one activity's name between the two values. Ordering by `fetched_at` (oldest first, with
    `id` as a tiebreak for two rows archived in the same instant) makes the newest snapshot's
    entries always get parsed last, so its name deterministically wins ties -- the newest export
    is the best available signal for "Garmin's name for this activity as of now".
    """
    rows = conn.execute(
        select(raw_object.c.storage_path)
        .where(
            raw_object.c.athlete_id == athlete_id,
            raw_object.c.source == "garmin_export",
            raw_object.c.source_locator.ilike("%summarizedActivities.json"),
        )
        .order_by(raw_object.c.fetched_at.asc(), raw_object.c.id.asc())
    ).fetchall()

    entries: list[GarminActivitySummaryEntry] = []
    for row in rows:
        content = read_raw_bytes(archive_root, row.storage_path)
        entries.extend(parse_summarized_activities_json(content))

    return correct_activities_from_summary(conn, athlete_id=athlete_id, entries=entries)
