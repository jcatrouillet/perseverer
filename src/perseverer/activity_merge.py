"""The athlete's own "these two activities are the same, use this side's data for each field"
correction -- for a cross-source duplicate `_find_merge_match` (`adapters/fit_folder.py`) failed
to catch at ingest time. Real, confirmed need: this athlete's own archive has activities recorded
by both a Garmin device and synced to Strava, each independently imported (`fit_folder`/
`garmin_export` and `strava_export`), that never merged into one record -- sometimes because a
sport correction landed *after* the other source had already been imported and failed to match
(merge-matching only runs once, at ingest time, for the incoming candidate; an existing
activity's own later correction never retroactively re-triggers it), sometimes because the two
platforms genuinely computed a different value for the same activity (a duration disagreement
big enough that only the athlete can say which one is right).

Unlike `_find_merge_match`'s own automatic merge (whichever source is ingested first silently
wins every field -- see `ingest_canonical_batch`'s matched branch, which only ever adds a new
`activity_source_link`, never touches the existing activity's own values), this lets the athlete
pick per field which side's value survives, mixing sources freely.

Same durable-override shape as `bouldering_overrides.py`/`activity_trim.py`, for the same reason:
`sync rebuild` wipes and re-derives `activity`/`activity_source_link` from raw bytes on every
run, re-splitting the two activities right back apart unless this correction is reapplied. Keyed
by `(source, external_id)` pairs -- not `activity_id` (a fresh ULID every rebuild) and not
`start_time_utc` (two activities being merged share the same start_time_utc *by definition*, so
it alone can't tell "keep" from "absorbed" apart once a rebuild has re-split them into two
identically-timestamped rows again).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import Connection, delete, select

from perseverer.db.schema import (
    activity,
    activity_merge_override,
    activity_metric,
    activity_source_link,
    activity_stream,
    lap,
    route_geom,
)
from perseverer.db.schema import split as split_table
from perseverer.merge.engine import ActivityCandidate, is_same_activity

# Mirrors adapters/fit_folder.py's own _find_merge_match window -- duplicated rather than
# imported (that name is private to that module) so duplicate detection considers exactly the
# same candidates ingest-time matching would have.
_MERGE_WINDOW = timedelta(days=1)

# Mirrors api/routers/activities.py's own AVG_HR_METRIC_KEYS/MAX_HR_METRIC_KEYS -- duplicated
# rather than imported to avoid a circular import (the router imports this module, not the
# reverse), same reasoning as activity_trim.py's own copy of these.
_AVG_HR_METRIC_KEYS = ("fit.session.avg_heart_rate", "strava.session.avg_heart_rate")
_MAX_HR_METRIC_KEYS = ("fit.session.max_heart_rate", "strava.session.max_heart_rate")
_TRAINING_LOAD_METRIC_KEY = "fit.session.training_load_peak"

MERGEABLE_SCALAR_FIELDS = (
    "distance_m",
    "duration_s",
    "moving_duration_s",
    "elevation_gain_m",
    "calories",
)
# Copying the *whole* activity_metric row (value + its own `source`) when a metric field is
# chosen from the other side preserves provenance per field (CLAUDE.md principle 4), not just
# the number.
MERGEABLE_METRIC_FIELDS: dict[str, tuple[str, ...]] = {
    "avg_hr_bpm": _AVG_HR_METRIC_KEYS,
    "max_hr_bpm": _MAX_HR_METRIC_KEYS,
    "training_load": (_TRAINING_LOAD_METRIC_KEY,),
}
# Whole-collection swaps only -- picking "the other side's route" means all of it, not per-point/
# per-lap, which would need a much bigger UI control for no real benefit here.
MERGEABLE_COLLECTION_FIELDS = ("route", "laps", "splits", "stream")


@dataclass(frozen=True)
class DuplicateCandidate:
    id: str
    name: str | None
    primary_source: str
    start_time_utc: datetime
    distance_m: float | None
    duration_s: float | None


@dataclass(frozen=True)
class FieldComparison:
    field: str
    # float|None for a scalar/metric field's actual value; the literal "self"/"other" for a
    # collection field (route/laps/splits/stream), which has no single scalar to show.
    self_value: float | str | None
    other_value: float | str | None


def find_duplicate_candidates(
    conn: Connection, *, athlete_id: str, activity_id: str
) -> list[DuplicateCandidate]:
    """Every *other* activity within the same +/-1 day window `_find_merge_match` itself uses
    that `is_same_activity` (with the hike/walk merge-family leniency -- see merge/engine.py)
    would consider a match. Universal across sports, unlike transport_mix.py's hiking/walking
    scope -- a cross-source duplicate can happen for any activity type. Cheap: one indexed
    window query plus an in-Python comparison over however many activities fall in it (typically
    0-2), the same bounded cost profile as `_find_merge_match` itself."""
    self_row = conn.execute(
        select(activity.c.start_time_utc, activity.c.duration_s, activity.c.sport).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if self_row is None:
        return []
    self_candidate = ActivityCandidate(
        self_row.start_time_utc, self_row.duration_s, self_row.sport
    )

    window_start = self_row.start_time_utc - _MERGE_WINDOW
    window_end = self_row.start_time_utc + _MERGE_WINDOW
    rows = conn.execute(
        select(activity).where(
            activity.c.athlete_id == athlete_id,
            activity.c.id != activity_id,
            activity.c.start_time_utc.between(window_start, window_end),
            activity.c.deleted_at.is_(None),
        )
    ).fetchall()

    candidates = []
    for row in rows:
        other_candidate = ActivityCandidate(row.start_time_utc, row.duration_s, row.sport)
        if is_same_activity(self_candidate, other_candidate).is_match:
            candidates.append(
                DuplicateCandidate(
                    id=row.id,
                    name=row.name,
                    primary_source=row.primary_source,
                    start_time_utc=row.start_time_utc,
                    distance_m=row.distance_m,
                    duration_s=row.duration_s,
                )
            )
    return candidates


def find_all_duplicate_pairs(
    conn: Connection, *, athlete_id: str
) -> list[tuple[DuplicateCandidate, DuplicateCandidate]]:
    """The Settings page's list-wide duplicate scan -- a deliberate, occasional-visit-only
    exception to this app's usual never-scan-list-wide rule (every other list/calendar view
    stays rollup-backed, see CLAUDE.md). Reuses `find_duplicate_candidates` per activity rather
    than reimplementing the matching query, so the two entry points (single-activity-detail-page
    banner, Settings list) can never disagree about what counts as a duplicate. Benchmarked at
    ~0.5s over this athlete's full ~1800-activity archive -- each call is the same indexed
    +/-1 day window query `find_duplicate_candidates` already pays for the detail-page case.

    `is_same_activity` is symmetric, so naively scanning every activity would report each pair
    twice (A matching B, then B matching A); each relationship is returned exactly once."""
    rows = conn.execute(
        select(activity).where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchall()
    self_by_id = {
        row.id: DuplicateCandidate(
            id=row.id,
            name=row.name,
            primary_source=row.primary_source,
            start_time_utc=row.start_time_utc,
            distance_m=row.distance_m,
            duration_s=row.duration_s,
        )
        for row in rows
    }
    seen: set[frozenset[str]] = set()
    pairs: list[tuple[DuplicateCandidate, DuplicateCandidate]] = []
    for row in rows:
        for other in find_duplicate_candidates(conn, athlete_id=athlete_id, activity_id=row.id):
            key = frozenset((row.id, other.id))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((self_by_id[row.id], other))
    return pairs


def _metric_value(
    conn: Connection, *, activity_id: str, metric_keys: tuple[str, ...]
) -> float | None:
    row = conn.execute(
        select(activity_metric.c.value_num).where(
            activity_metric.c.activity_id == activity_id,
            activity_metric.c.metric_key.in_(metric_keys),
        )
    ).fetchone()
    return row.value_num if row is not None else None


def build_merge_preview(
    conn: Connection, *, athlete_id: str, self_id: str, other_id: str
) -> list[FieldComparison]:
    """One row per mergeable field -- what the athlete's per-field choice UI is built from."""

    def _load(activity_id: str) -> object:
        row = conn.execute(
            select(activity).where(
                activity.c.id == activity_id, activity.c.athlete_id == athlete_id
            )
        ).fetchone()
        if row is None:
            raise ValueError(f"no activity {activity_id!r} for athlete {athlete_id!r}")
        return row

    self_row = _load(self_id)
    other_row = _load(other_id)

    comparisons = [
        FieldComparison(
            field=field,
            self_value=getattr(self_row, field),
            other_value=getattr(other_row, field),
        )
        for field in MERGEABLE_SCALAR_FIELDS
    ]
    for field, keys in MERGEABLE_METRIC_FIELDS.items():
        comparisons.append(
            FieldComparison(
                field=field,
                self_value=_metric_value(conn, activity_id=self_id, metric_keys=keys),
                other_value=_metric_value(conn, activity_id=other_id, metric_keys=keys),
            )
        )
    for field in MERGEABLE_COLLECTION_FIELDS:
        comparisons.append(FieldComparison(field=field, self_value="self", other_value="other"))
    return comparisons


def _apply_field_choices(
    conn: Connection,
    *,
    athlete_id: str,
    self_id: str,
    other_id: str,
    field_choices: dict[str, str],
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    scalar_updates = {
        field: field_choices[field]
        for field in MERGEABLE_SCALAR_FIELDS
        if field_choices.get(field) == "other"
    }
    if scalar_updates:
        other_row = conn.execute(
            select(activity).where(activity.c.id == other_id)
        ).fetchone()
        assert other_row is not None
        conn.execute(
            activity.update()
            .where(activity.c.id == self_id)
            .values(
                **{field: getattr(other_row, field) for field in scalar_updates},
                updated_at=now,
            )
        )

    for field, keys in MERGEABLE_METRIC_FIELDS.items():
        if field_choices.get(field) != "other":
            continue
        other_metric = conn.execute(
            select(activity_metric).where(
                activity_metric.c.activity_id == other_id,
                activity_metric.c.metric_key.in_(keys),
            )
        ).fetchone()
        if other_metric is None:
            continue
        existing = conn.execute(
            select(activity_metric.c.id).where(
                activity_metric.c.activity_id == self_id,
                activity_metric.c.metric_key == other_metric.metric_key,
            )
        ).fetchone()
        if existing is not None:
            conn.execute(
                activity_metric.update()
                .where(activity_metric.c.id == existing.id)
                .values(value_num=other_metric.value_num, source=other_metric.source)
            )
        else:
            conn.execute(
                activity_metric.insert().values(
                    athlete_id=athlete_id,
                    activity_id=self_id,
                    metric_key=other_metric.metric_key,
                    value_num=other_metric.value_num,
                    value_text=other_metric.value_text,
                    unit=other_metric.unit,
                    source=other_metric.source,
                    created_at=now,
                )
            )

    if field_choices.get("route") == "other":
        other_route = conn.execute(
            select(route_geom).where(route_geom.c.activity_id == other_id)
        ).fetchone()
        if other_route is not None:
            conn.execute(
                route_geom.update()
                .where(route_geom.c.activity_id == self_id)
                .values(
                    encoded_polyline=other_route.encoded_polyline,
                    simplified_polyline=other_route.simplified_polyline,
                    min_lat=other_route.min_lat,
                    min_lng=other_route.min_lng,
                    max_lat=other_route.max_lat,
                    max_lng=other_route.max_lng,
                    start_lat=other_route.start_lat,
                    start_lng=other_route.start_lng,
                    end_lat=other_route.end_lat,
                    end_lng=other_route.end_lng,
                )
            )

    if field_choices.get("laps") == "other":
        other_laps = conn.execute(
            select(lap).where(lap.c.activity_id == other_id).order_by(lap.c.lap_index)
        ).fetchall()
        conn.execute(delete(lap).where(lap.c.activity_id == self_id))
        for r in other_laps:
            conn.execute(
                lap.insert().values(
                    athlete_id=athlete_id,
                    activity_id=self_id,
                    lap_index=r.lap_index,
                    start_time_utc=r.start_time_utc,
                    duration_s=r.duration_s,
                    moving_duration_s=r.moving_duration_s,
                    distance_m=r.distance_m,
                    avg_hr=r.avg_hr,
                    max_hr=r.max_hr,
                    avg_speed_mps=r.avg_speed_mps,
                )
            )

    if field_choices.get("splits") == "other":
        other_splits = conn.execute(
            select(split_table)
            .where(split_table.c.activity_id == other_id)
            .order_by(split_table.c.split_index)
        ).fetchall()
        conn.execute(delete(split_table).where(split_table.c.activity_id == self_id))
        for r in other_splits:
            conn.execute(
                split_table.insert().values(
                    athlete_id=athlete_id,
                    activity_id=self_id,
                    split_index=r.split_index,
                    split_type=r.split_type,
                    start_time_utc=r.start_time_utc,
                    end_time_utc=r.end_time_utc,
                    duration_s=r.duration_s,
                    distance_m=r.distance_m,
                    climb_grade=r.climb_grade,
                    climb_result=r.climb_result,
                    climb_avg_hr=r.climb_avg_hr,
                    climb_max_hr=r.climb_max_hr,
                    is_manual=r.is_manual,
                )
            )

    if field_choices.get("stream") == "other":
        other_stream = conn.execute(
            select(activity_stream).where(activity_stream.c.activity_id == other_id)
        ).fetchone()
        if other_stream is not None:
            # Repoints self's row at other's already-written Parquet file -- self's own old
            # file (if any) is left on disk, orphaned but harmless (never destructive).
            existing = conn.execute(
                select(activity_stream.c.activity_id).where(
                    activity_stream.c.activity_id == self_id
                )
            ).fetchone()
            values = {
                "parquet_path": other_stream.parquet_path,
                "n_samples": other_stream.n_samples,
                "channels": other_stream.channels,
                "sample_rate_hint": other_stream.sample_rate_hint,
            }
            if existing is not None:
                conn.execute(
                    activity_stream.update()
                    .where(activity_stream.c.activity_id == self_id)
                    .values(**values)
                )
            else:
                conn.execute(
                    activity_stream.insert().values(
                        activity_id=self_id, athlete_id=athlete_id, **values
                    )
                )


def merge_activities(
    conn: Connection,
    *,
    athlete_id: str,
    self_id: str,
    other_id: str,
    field_choices: dict[str, str],
) -> None:
    """The shared merge core -- called by both the live endpoint and
    `apply_activity_merge_overrides` after a rebuild. `self_id` is always the survivor: applies
    `field_choices` (only entries valued `"other"` change anything -- everything else keeps
    `self`'s own current value), moves every one of `other`'s `activity_source_link` rows onto
    `self`, then soft-deletes `other`. Raises `ValueError` if either activity doesn't exist for
    this athlete, or if they're already the same activity."""
    if self_id == other_id:
        raise ValueError("cannot merge an activity with itself")
    for aid in (self_id, other_id):
        exists = conn.execute(
            select(activity.c.id).where(
                activity.c.id == aid,
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if exists is None:
            raise ValueError(f"no activity {aid!r} for athlete {athlete_id!r}")

    _apply_field_choices(
        conn,
        athlete_id=athlete_id,
        self_id=self_id,
        other_id=other_id,
        field_choices=field_choices,
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    conn.execute(
        activity_source_link.update()
        .where(
            activity_source_link.c.athlete_id == athlete_id,
            activity_source_link.c.activity_id == other_id,
        )
        .values(activity_id=self_id)
    )
    conn.execute(
        activity.update().where(activity.c.id == other_id).values(deleted_at=now, updated_at=now)
    )


def merge_activities_and_record(
    conn: Connection,
    *,
    athlete_id: str,
    self_id: str,
    other_id: str,
    field_choices: dict[str, str],
) -> None:
    """`merge_activities` plus the durable override row(s) that make it survive a rebuild --
    what the live API endpoint calls. One override row per source link `other` currently has
    *before* the merge (each independently reapplicable), anchored to any one of `self`'s own
    current source links (rebuild re-derives the same (source, external_id) pairs deterministically
    every time, so any single existing link works as the anchor)."""
    self_link = conn.execute(
        select(activity_source_link.c.source, activity_source_link.c.external_id)
        .where(
            activity_source_link.c.athlete_id == athlete_id,
            activity_source_link.c.activity_id == self_id,
        )
        .limit(1)
    ).fetchone()
    if self_link is None:
        raise ValueError(f"activity {self_id!r} has no source links to anchor a merge on")
    other_links = conn.execute(
        select(activity_source_link.c.source, activity_source_link.c.external_id).where(
            activity_source_link.c.athlete_id == athlete_id,
            activity_source_link.c.activity_id == other_id,
        )
    ).fetchall()

    merge_activities(
        conn,
        athlete_id=athlete_id,
        self_id=self_id,
        other_id=other_id,
        field_choices=field_choices,
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    choices_json = json.dumps({k: v for k, v in field_choices.items() if v == "other"})
    for link in other_links:
        conn.execute(
            activity_merge_override.insert().values(
                athlete_id=athlete_id,
                keep_source=self_link.source,
                keep_external_id=self_link.external_id,
                absorbed_source=link.source,
                absorbed_external_id=link.external_id,
                field_choices=choices_json,
                created_at=now,
            )
        )


def apply_activity_merge_overrides(conn: Connection, *, athlete_id: str) -> int:
    """Re-applies every durable merge after `sync rebuild` -- called alongside
    `apply_activity_trim_overrides`. Resolves each side's *current* activity_id via its
    (source, external_id) anchor rather than by start_time_utc (see the schema's own docstring
    for why). Skips a row whose data no longer exists, or that's already merged (both anchors
    already resolve to the same activity_id -- e.g. natural merge-matching, now with the hike/
    walk leniency, caught it on this rebuild without needing the override at all). Returns the
    number of merges (re-)applied."""

    def _resolve(source: str, external_id: str) -> str | None:
        row = conn.execute(
            select(activity_source_link.c.activity_id).where(
                activity_source_link.c.athlete_id == athlete_id,
                activity_source_link.c.source == source,
                activity_source_link.c.external_id == external_id,
            )
        ).fetchone()
        return cast(str, row.activity_id) if row is not None else None

    overrides = conn.execute(
        select(activity_merge_override).where(activity_merge_override.c.athlete_id == athlete_id)
    ).fetchall()
    merged = 0
    for o in overrides:
        keep_id = _resolve(o.keep_source, o.keep_external_id)
        absorbed_id = _resolve(o.absorbed_source, o.absorbed_external_id)
        if keep_id is None or absorbed_id is None or keep_id == absorbed_id:
            continue
        field_choices = json.loads(o.field_choices)
        try:
            merge_activities(
                conn,
                athlete_id=athlete_id,
                self_id=keep_id,
                other_id=absorbed_id,
                field_choices=field_choices,
            )
        except ValueError:
            continue
        merged += 1
    return merged
