"""Backfills `lap.wkt_step_index` and `lap.lap_trigger` for already-ingested activities from their
already-archived raw FIT bytes -- without a full `sync rebuild`. Same shape as
`backfill_lap_moving_duration.py`: both are purely additive columns added after these laps were
inserted, so re-parsing the archived FIT bytes and UPDATE-ing the existing lap rows is enough.

Why they matter: the watch tags every lap with the workout step it belongs to, and with why it
ended. A structured workout run with the device's auto-lap on (every 1 km) has many more laps than
steps, and only that tag says which lap belongs to which step.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Connection, select

from perseverer.archive import read_raw_bytes
from perseverer.db.schema import activity_source_link, lap
from perseverer.db.schema import raw_object as raw_object_table
from perseverer.fit.parser import parse_fit


def backfill_lap_workout_steps(conn: Connection, archive_root: Path, *, athlete_id: str) -> int:
    """Returns the number of lap rows updated. An activity whose laps already carry a trigger is
    skipped, so running it twice is a no-op."""
    already_done = {
        row.activity_id
        for row in conn.execute(
            select(lap.c.activity_id)
            .where(lap.c.athlete_id == athlete_id, lap.c.lap_trigger.is_not(None))
            .distinct()
        )
    }
    activity_id_by_raw_object_id = {
        row.raw_object_id: row.activity_id
        for row in conn.execute(
            select(activity_source_link.c.raw_object_id, activity_source_link.c.activity_id).where(
                activity_source_link.c.athlete_id == athlete_id
            )
        )
    }
    raw_rows = conn.execute(
        select(raw_object_table.c.id, raw_object_table.c.storage_path).where(
            raw_object_table.c.athlete_id == athlete_id, raw_object_table.c.kind.like("fit%")
        )
    ).fetchall()

    updated = 0
    for row in raw_rows:
        activity_id = activity_id_by_raw_object_id.get(row.id)
        if activity_id is None or activity_id in already_done:
            continue

        batch = parse_fit(read_raw_bytes(archive_root, row.storage_path))
        if batch.activity is None or not batch.activity.laps:
            continue

        for lap_row in batch.activity.laps:
            if lap_row.wkt_step_index is None and lap_row.lap_trigger is None:
                continue
            result = conn.execute(
                lap.update()
                .where(
                    lap.c.athlete_id == athlete_id,
                    lap.c.activity_id == activity_id,
                    lap.c.lap_index == lap_row.lap_index,
                )
                .values(wkt_step_index=lap_row.wkt_step_index, lap_trigger=lap_row.lap_trigger)
            )
            updated += result.rowcount
        already_done.add(activity_id)
        # One short write transaction per activity (AGENTS.md: never hold a write across many
        # stream reads): the API writes to the same database.
        conn.commit()

    return updated
