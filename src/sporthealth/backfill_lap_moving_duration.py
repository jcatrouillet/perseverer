"""Backfills `lap.moving_duration_s` for already-ingested activities from their already-archived
raw FIT bytes -- without a full `sync rebuild`. Same shape as `backfill_workouts.py` (see its own
docstring for the general rationale): `moving_duration_s` is a purely additive column added after
these laps were already inserted, so re-parsing the archived FIT bytes and UPDATE-ing the existing
lap rows is enough -- no merge-matching, no new activity_id, nothing else touched.

Confirmed as a real, not hypothetical, gap against a real archived FIT file: `lap.duration_s` is
FIT's `total_elapsed_time` (real wall-clock, including any device pause within that lap), while
`total_timer_time` (now captured as `moving_duration_s`, see fit/parser.py) excludes it -- a real
recovery-interval lap showed elapsed=1008.2s vs timer=90.0s across a genuine ~15-minute pause,
which is exactly the kind of lap the Intervals table's DURATION/PACE columns were showing wrong
before this column existed.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Connection, select

from sporthealth.archive import read_raw_bytes
from sporthealth.db.schema import activity_source_link, lap
from sporthealth.db.schema import raw_object as raw_object_table
from sporthealth.fit.parser import parse_fit


def backfill_lap_moving_duration(conn: Connection, archive_root: Path, *, athlete_id: str) -> int:
    """Returns the number of lap rows updated with a moving_duration_s value."""
    already_done = {
        row.activity_id
        for row in conn.execute(
            select(lap.c.activity_id)
            .where(lap.c.athlete_id == athlete_id, lap.c.moving_duration_s.is_not(None))
            .distinct()
        )
    }
    activity_id_by_raw_object_id = {
        row.raw_object_id: row.activity_id
        for row in conn.execute(
            select(
                activity_source_link.c.raw_object_id, activity_source_link.c.activity_id
            ).where(activity_source_link.c.athlete_id == athlete_id)
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

        content = read_raw_bytes(archive_root, row.storage_path)
        batch = parse_fit(content)
        if batch.activity is None or not batch.activity.laps:
            continue

        for lap_row in batch.activity.laps:
            if lap_row.moving_duration_s is None:
                continue
            result = conn.execute(
                lap.update()
                .where(
                    lap.c.athlete_id == athlete_id,
                    lap.c.activity_id == activity_id,
                    lap.c.lap_index == lap_row.lap_index,
                )
                .values(moving_duration_s=lap_row.moving_duration_s)
            )
            updated += result.rowcount

        already_done.add(activity_id)

    return updated
