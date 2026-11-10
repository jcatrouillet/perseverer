"""Per-set exercise data of a HIIT / strength activity, from Garmin Connect's `exerciseSets`
endpoint (`get_activity_exercise_sets`): one entry per set, each naming the exercise the watch
recognised (a Garmin `category` and `name`, with a probability), the reps, the duration and the
weight. The FIT file of the same activity only carries one summary set, so this response is the
only place the individual sets exist.

Raw first: the response is archived as `garmin_connect_exercise_sets_json` (an activity Garmin
holds no sets for is archived too, as an empty response, so it is not asked again), and
`activity_exercise_set` is derived from it here, which is also what `sync rebuild` replays.

The response shape is Garmin's: `{"activityId": ..., "exerciseSets": [{"exercises": [{"category",
"name", "probability"}], "duration", "repetitionCount", "weight", "setType", "startTime",
"messageIndex", "wktStepIndex"}]}`. Every key is kept in the archive; only the ones named here are
modeled. `weight` is in grams in that payload.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select

from perseverer.db.schema import activity_exercise_set, activity_source_link

KIND_EXERCISE_SETS = "garmin_connect_exercise_sets_json"


@dataclass(frozen=True)
class ParsedExerciseSet:
    set_index: int
    set_type: str | None  # "ACTIVE" | "REST"
    start_time_utc: datetime | None
    duration_s: float | None
    reps: int | None
    weight_kg: float | None
    exercise_category: str | None
    exercise_name: str | None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _start_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def parse_exercise_sets(content: bytes) -> list[ParsedExerciseSet]:
    """Pure parse of one archived `exerciseSets` response. No sets (null, empty, or a response
    that is not the expected shape) is an empty list."""
    try:
        data = json.loads(content)
    except ValueError:
        return []
    raw_sets = data.get("exerciseSets") if isinstance(data, dict) else None
    if not isinstance(raw_sets, list):
        return []

    sets: list[ParsedExerciseSet] = []
    for i, raw in enumerate(raw_sets):
        if not isinstance(raw, dict):
            continue
        exercises = [e for e in raw.get("exercises") or [] if isinstance(e, dict)]
        # The watch reports its guesses with a probability: the likeliest one is the exercise.
        best = max(exercises, key=lambda e: _float(e.get("probability")) or 0.0, default=None)
        weight_g = _float(raw.get("weight"))
        reps = _float(raw.get("repetitionCount"))
        set_type = raw.get("setType")
        sets.append(
            ParsedExerciseSet(
                set_index=i,
                set_type=str(set_type) if set_type is not None else None,
                start_time_utc=_start_time(raw.get("startTime")),
                duration_s=_float(raw.get("duration")),
                reps=int(reps) if reps is not None else None,
                weight_kg=weight_g / 1000.0 if weight_g is not None else None,
                exercise_category=(
                    str(best["category"]) if best and best.get("category") else None
                ),
                exercise_name=(str(best["name"]) if best and best.get("name") else None),
            )
        )
    return sets


def ingest_exercise_sets(
    conn: Connection, *, athlete_id: str, source: str, external_id: str, content: bytes
) -> int:
    """Replaces the sets of the activity this vendor id was ingested as (through
    `activity_source_link`) with the ones in `content`. Returns the number of sets stored; zero
    when the activity is not known (yet) or the response holds none."""
    activity_id = conn.execute(
        select(activity_source_link.c.activity_id).where(
            activity_source_link.c.athlete_id == athlete_id,
            activity_source_link.c.source == source,
            activity_source_link.c.external_id == external_id,
        )
    ).scalar_one_or_none()
    if activity_id is None:
        return 0

    sets = parse_exercise_sets(content)
    conn.execute(
        activity_exercise_set.delete().where(
            activity_exercise_set.c.athlete_id == athlete_id,
            activity_exercise_set.c.activity_id == activity_id,
        )
    )
    if sets:
        conn.execute(
            activity_exercise_set.insert(),
            [
                {
                    "athlete_id": athlete_id,
                    "activity_id": activity_id,
                    "set_index": s.set_index,
                    "set_type": s.set_type,
                    "start_time_utc": s.start_time_utc,
                    "duration_s": s.duration_s,
                    "reps": s.reps,
                    "weight_kg": s.weight_kg,
                    "exercise_category": s.exercise_category,
                    "exercise_name": s.exercise_name,
                }
                for s in sets
            ],
        )
    return len(sets)
