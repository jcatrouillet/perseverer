"""Fetches the per-set data (Garmin Connect `exerciseSets`) of already-ingested HIIT / strength
activities that have never been asked for, archives each response and derives
`activity_exercise_set` from it. An activity Garmin holds no sets for gets an empty response
archived, so a second run does not ask again.

Real, rate-limited Garmin calls, one per activity, same contract as the sync: a 429 stops the whole
run at once (never retried), and a run never falls back to credentials. Safe to interrupt and
re-run; each activity is committed on its own so the database is never write-locked across a call.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Connection, select

from perseverer.adapters.garmin_connect import GarminConnectAdapter
from perseverer.archive import archive_raw_bytes
from perseverer.db.schema import activity, activity_source_link, raw_object
from perseverer.exercise_sets import KIND_EXERCISE_SETS, ingest_exercise_sets

# Garmin activity ids live in the source link of every source that carries them: the live sync
# and the Garmin export both use the Garmin activity id as their external id.
_GARMIN_SOURCES = ("garmin_connect", "garmin_export")


def backfill_exercise_sets(
    conn: Connection,
    archive_root: Path,
    adapter: GarminConnectAdapter,
    *,
    athlete_id: str,
    limit: int | None = None,
) -> int:
    """Returns the number of activities asked about. `adapter` must be authenticated. Raises
    GarminRateLimitAborted on a 429 (activities done before it stay done)."""
    asked_already = set(
        conn.execute(
            select(raw_object.c.external_id).where(
                raw_object.c.athlete_id == athlete_id, raw_object.c.kind == KIND_EXERCISE_SETS
            )
        ).scalars()
    )
    candidates = conn.execute(
        select(activity_source_link.c.external_id, activity_source_link.c.source)
        .join(activity, activity.c.id == activity_source_link.c.activity_id)
        .where(
            activity_source_link.c.athlete_id == athlete_id,
            activity_source_link.c.source.in_(_GARMIN_SOURCES),
            activity.c.deleted_at.is_(None),
            activity.c.sub_sport.in_(("strength_training",)) | (activity.c.sport == "hiit"),
        )
        .order_by(activity.c.start_time_utc.desc())
    ).fetchall()

    done = 0
    for external_id, source in candidates:
        if external_id in asked_already:
            continue
        if limit is not None and done >= limit:
            break
        content = adapter.fetch_exercise_sets(external_id)
        if content is None:
            continue
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=source,
            kind=KIND_EXERCISE_SETS,
            content=content,
            locator=f"activity/{external_id}/exerciseSets",
            external_id=external_id,
        )
        ingest_exercise_sets(
            conn, athlete_id=athlete_id, source=source, external_id=external_id, content=content
        )
        conn.commit()
        asked_already.add(external_id)
        done += 1
    return done
