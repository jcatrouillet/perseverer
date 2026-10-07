"""Backfills `activity_workout`/`activity_workout_step` for already-ingested activities from
their already-archived raw FIT bytes -- without a full `sync rebuild`.

A full rebuild wipes and replays every derived table (`activity`, `lap`, `split`,
`activity_stream`, every rollup, fitness, insights), re-runs merge-matching, and mints a fresh
ULID for every activity -- correct when the raw-to-canonical *mapping itself* changed (a bug fix
that changes what `sport`/`distance_m`/etc. get set to, or a merge-matching change), but real
overkill for a purely additive parser change like this one: `activity_workout`/
`activity_workout_step` didn't exist before, nothing else in the schema references them, and the
raw FIT bytes for a given `activity_id` haven't changed -- re-parsing just the new message types
(`workout_mesgs`/`workout_step_mesgs`, see fit/parser.py::_parse_workout) and inserting rows for
them is enough. On a real archive of thousands of files this is a difference of tens of minutes
(full rebuild: re-parse every message type, re-insert every table, recompute every rollup) versus
well under a minute (this: re-parse one small message type, insert a handful of rows).

Idempotent and re-runnable: an activity that already has an `activity_workout` row is skipped,
so running this again after a fresh `import garmin-export`/`fit-folder` run is a safe no-op for
already-backfilled activities.

Not a general "backfill any new field" mechanism -- scoped to this one case. A future purely
additive field with the same shape (new message type, new table, no dependency on or mutation of
any existing row) can follow the same pattern rather than reaching for a full rebuild by default.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Connection, select

from perseverer.archive import read_raw_bytes
from perseverer.db.schema import activity_source_link, activity_workout, activity_workout_step
from perseverer.db.schema import raw_object as raw_object_table
from perseverer.fit.parser import parse_fit


def backfill_workouts(conn: Connection, archive_root: Path, *, athlete_id: str) -> int:
    """Returns the number of activities newly backfilled with a workout plan."""
    already_has_workout = {
        row.activity_id
        for row in conn.execute(
            select(activity_workout.c.activity_id).where(
                activity_workout.c.athlete_id == athlete_id
            )
        )
    }

    # activity_source_link.raw_object_id -> activity_id, so a raw FIT object can be mapped
    # straight to the (possibly merged-into) activity it belongs to without re-running any
    # merge-matching.
    activity_id_by_raw_object_id = {
        row.raw_object_id: row.activity_id
        for row in conn.execute(
            select(activity_source_link.c.raw_object_id, activity_source_link.c.activity_id).where(
                activity_source_link.c.athlete_id == athlete_id
            )
        )
    }

    # "fit" covers the unified activity-or-health dispatch kind; "fit_activity" is the older
    # The older activity-only kind kept for backward compatibility -- same convention as
    # rebuild.py's own replay
    # loop. Health FIT files decode with no session_mesgs (parse_fit returns
    # kind="unrecognized"), so they're naturally skipped below without a separate kind filter.
    raw_rows = conn.execute(
        select(raw_object_table.c.id, raw_object_table.c.storage_path).where(
            raw_object_table.c.athlete_id == athlete_id, raw_object_table.c.kind.like("fit%")
        )
    ).fetchall()

    backfilled = 0
    for row in raw_rows:
        activity_id = activity_id_by_raw_object_id.get(row.id)
        if activity_id is None or activity_id in already_has_workout:
            continue

        content = read_raw_bytes(archive_root, row.storage_path)
        batch = parse_fit(content)
        if batch.activity is None or batch.activity.workout is None:
            continue

        workout = batch.activity.workout
        conn.execute(
            activity_workout.insert().values(
                activity_id=activity_id,
                athlete_id=athlete_id,
                name=workout.name,
                description=workout.description,
            )
        )
        for step in workout.steps:
            conn.execute(
                activity_workout_step.insert().values(
                    athlete_id=athlete_id,
                    activity_id=activity_id,
                    step_index=step.step_index,
                    duration_type=step.duration_type,
                    duration_time_s=step.duration_time_s,
                    duration_distance_m=step.duration_distance_m,
                    target_type=step.target_type,
                    target_low_mps=step.target_low_mps,
                    target_high_mps=step.target_high_mps,
                    intensity=step.intensity,
                    repeat_from_step=step.repeat_from_step,
                    repeat_count=step.repeat_count,
                )
            )
        already_has_workout.add(activity_id)
        backfilled += 1

    return backfilled
