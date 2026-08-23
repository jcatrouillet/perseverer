"""Corrects `activity.name` using Garmin Connect's own cloud-side `activityName` field --
already archived raw as `garmin_connect_json` at ingest time (`garmin_connect.py`'s own
`list_activity_summaries` call archives the full activity summary dict before anything else
happens to it), just never parsed out of that JSON until now.

Real, confirmed need (not hypothetical): verified live against this athlete's own Garmin Connect
account (never assumed -- see CLAUDE.md's "verify against live third-party data" discipline) that
Garmin's own `activityName` can be genuinely richer than anything this project derives itself.
One real example: a FIT file's own on-device name was just "Running" (the generic default), and
`activity_workout.name` (the FIT workout message's own name) was "W12 Fri . [Consolidation]
Easy " -- real, but missing the location context Garmin's own cloud-side field adds on top:
`activityName` for that same activity was "Santa Clara - W12 Fri . [Consolidation] Easy",
confirmed via a live `client.get_activity()` call to match the archived JSON exactly.

Deliberately narrow, unlike the reverted attempt described in `sport_override.py`'s own
docstring (which blindly overwrote every activity's name with Garmin's own, mass-replacing 396
real custom titles with a generic "Santa Clara Other" auto-template): this only ever replaces a
name that's still the sport's own generic device default (`_GENERIC_DEFAULT_NAME_BY_SPORT`,
mirroring `frontend/src/yearStats.ts::GENERIC_DEFAULT_NAME_BY_SPORT` exactly -- same cross-
language duplication precedent as `weatherCode.py`/`weatherCode.ts`) -- a real custom title the
athlete (or a previous correction) already gave the activity is never touched.

`weather_titles.py::strip_leading_emoji` is used to see past an emoji this feature's own backfill
may have already prepended -- without it, "☀️ Run" doesn't look like the bare generic default
"Run" to a naive string comparison, so an activity already touched by the weather-emoji feature
would incorrectly look like it already has a real custom title and never get corrected. The
existing emoji (if any) is preserved on the corrected name, not dropped.

Uses `sport_override.py::set_name_override` (the same durable, rebuild-safe override table
`weather_titles.py` and `PATCH /activities/{id}/name` both write to), not a bare `activity.name`
column update -- `sync rebuild` doesn't currently re-run any Garmin-summary correction pass (see
`garmin_activity_summary.py`'s own `backfill_activity_corrections`, which is NOT wired into
`rebuild.py` either), so a bare mutation here would silently vanish on the next rebuild. The
override table's own `apply_sport_overrides`, already called at the end of every `sync rebuild`,
is what makes this survive one without any new rebuild.py wiring at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import Connection, func, select

from perseverer.archive import read_raw_bytes
from perseverer.db.schema import activity, activity_source_link, raw_object
from perseverer.sport_override import set_name_override
from perseverer.weather_titles import strip_leading_emoji

# Mirrors frontend/src/yearStats.ts::GENERIC_DEFAULT_NAME_BY_SPORT exactly -- the FIT on-device
# default name for each sport that has one. Sports without a dominant default (cycling,
# training, rowing, ...) are deliberately absent, same reasoning as the TS original: this
# correction only ever fires when the current name is *known* to carry zero real information.
_GENERIC_DEFAULT_NAME_BY_SPORT: dict[str, str] = {
    "running": "Run",
    "walking": "Walk",
    "hiking": "Hike",
    "alpine_skiing": "Ski",
    "snowshoeing": "Snowshoe",
}

# "training" itself has no single dominant default -- yoga/strength_training/breathing each get
# a different device placeholder ("Yoga"/"Strength"/"Relax and Focus"), so it's deliberately
# absent from the sport-keyed dict above (same reasoning as its own comment). Keyed by
# (sport, sub_sport) instead, so only a specifically confirmed generic default is recognized.
# Real, confirmed gap found auditing this athlete's real archive (verified against Garmin's own
# archived activityName directly, not assumed): 3 real garmin_connect-sourced yoga activities
# were stuck showing the bare "Yoga" placeholder while Garmin's own name was genuinely richer
# ("Foundation Yoga", "Flow") -- undetected until now purely because "training" wasn't in the
# dict above at all. Only "yoga" is added here (not strength_training/breathing) since that's
# the only sub_sport this audit actually found a real mismatch for -- adding the others
# speculatively, without confirmed evidence they're ever auto-generated placeholders rather than
# already-real titles, isn't worth the risk of a wrong match.
_GENERIC_DEFAULT_NAME_BY_SPORT_SUB_SPORT: dict[tuple[str, str], str] = {
    ("training", "yoga"): "Yoga",
}


def backfill_garmin_activity_names(
    conn: Connection, archive_root: Path, *, athlete_id: str, dry_run: bool = False
) -> list[tuple[str, str, str]]:
    """Returns (activity_id, old_title, new_title) for every activity actually changed -- or
    that *would* change, when `dry_run=True`. Re-derivable from the raw archive alone, no
    network call -- `garmin_connect_json` is already fully archived by the time this ever runs.
    """
    # A garmin_connect_json summary is re-archived every sync run that still has this activity
    # inside its rolling window (its bytes shift slightly run to run -- e.g. embedded fetch
    # metadata -- so each re-fetch content-hashes to a new raw_object row, never deduplicating
    # against the last one). Picking the highest id (== most recently inserted, autoincrement
    # PK) per external_id keeps this a one-row-per-activity join instead of fanning out into one
    # duplicate `changes` entry per archived copy.
    latest_json = (
        select(
            raw_object.c.external_id,
            func.max(raw_object.c.id).label("raw_object_id"),
        )
        .where(
            raw_object.c.athlete_id == athlete_id,
            raw_object.c.kind == "garmin_connect_json",
        )
        .group_by(raw_object.c.external_id)
        .subquery()
    )

    rows = conn.execute(
        select(
            activity.c.id,
            activity.c.name,
            activity.c.sport,
            activity.c.sub_sport,
            raw_object.c.storage_path,
        )
        .select_from(
            activity.join(
                activity_source_link,
                (activity.c.id == activity_source_link.c.activity_id)
                & (activity_source_link.c.source == "garmin_connect"),
            )
            .join(latest_json, latest_json.c.external_id == activity_source_link.c.external_id)
            .join(raw_object, raw_object.c.id == latest_json.c.raw_object_id)
        )
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
        .order_by(activity.c.start_time_utc)
    ).fetchall()

    changes: list[tuple[str, str, str]] = []
    for row in rows:
        current_name = row.name
        if current_name is None:
            continue
        generic_default = _GENERIC_DEFAULT_NAME_BY_SPORT.get(
            row.sport
        ) or _GENERIC_DEFAULT_NAME_BY_SPORT_SUB_SPORT.get((row.sport, row.sub_sport))
        if generic_default is None:
            continue

        bare_name = strip_leading_emoji(current_name)
        if bare_name != generic_default:
            continue  # a real custom title (or an unrecognized generic default) -- never touched

        emoji_prefix = current_name[: len(current_name) - len(bare_name)]

        raw_bytes = read_raw_bytes(archive_root, row.storage_path)
        garmin_activity_name = json.loads(raw_bytes).get("activityName")
        if not garmin_activity_name:
            continue
        garmin_activity_name = garmin_activity_name.strip()
        if not garmin_activity_name or garmin_activity_name == bare_name:
            continue

        new_name = f"{emoji_prefix}{garmin_activity_name}"
        changes.append((row.id, current_name, new_name))

        if not dry_run:
            set_name_override(conn, athlete_id=athlete_id, activity_id=row.id, name=new_name)
            conn.commit()

    return changes
