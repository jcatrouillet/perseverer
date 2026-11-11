"""Handwritten workout notes of a HIIT / strength activity:

- `GET/POST /activities/{id}/photos`, `GET /activities/{id}/photos/{photo_id}/image`,
  `DELETE /activities/{id}/photos/{photo_id}`: the photos of the athlete's notes.
- `POST /activities/{id}/photos/{photo_id}/read`: reads the handwriting (Claude vision, in the
  background; poll the photo list). The result is a suggestion stored on the photo.
- `GET/PUT /activities/{id}/logbook`: the sets the athlete confirmed, in the order done.

Everything here is keyed by the activity's start time, not its id, so it survives `sync rebuild`.
See db/schema.py::activity_photo and handwriting.py.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import Connection, Engine, delete, select

from perseverer.api.dependencies import SettingsDep, get_conn, get_engine, require_api_key
from perseverer.api.schemas.common import to_utc
from perseverer.api.schemas.logbook import (
    LogbookIn,
    LogbookOut,
    LogbookStatusOut,
    LoggedExerciseOut,
    LoggedSetIn,
    PhotoOut,
)
from perseverer.archive import archive_raw_bytes, read_raw_bytes
from perseverer.config import Settings
from perseverer.db.schema import (
    activity,
    activity_logged_set,
    activity_photo,
    raw_object,
)
from perseverer.handwriting import DecodedLogbook, decode_logbook_photo, prepare_image_for_reading

log = logging.getLogger(__name__)
router = APIRouter()

MAX_PHOTO_BYTES = 15 * 1024 * 1024
MAX_PHOTOS_PER_ACTIVITY = 12
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
# A reading is a background job on one worker: one that has not finished after this long was lost
# (a restart), and is reported as failed rather than pending forever.
STALE_READ_AFTER = timedelta(minutes=5)


def _start_time(conn: Connection, athlete_id: str, activity_id: str) -> datetime:
    row = conn.execute(
        select(activity.c.start_time_utc).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="activity not found")
    start: datetime = row.start_time_utc
    return start


def _photo_out(row) -> PhotoOut:  # type: ignore[no-untyped-def]
    status = row.decode_status
    error = row.decode_error
    if (
        status == "pending"
        and row.decode_started_at is not None
        and datetime.now(UTC).replace(tzinfo=None) - row.decode_started_at > STALE_READ_AFTER
    ):
        status, error = "failed", "Reading was interrupted. Try again."
    decoded = None
    if status == "done" and row.decoded_json:
        decoded = DecodedLogbook.model_validate_json(row.decoded_json)
    return PhotoOut(
        id=row.id,
        content_type=row.content_type,
        byte_size=row.byte_size,
        created_at=to_utc(row.created_at),
        decode_status=status,
        decode_error=error,
        decoded=decoded,
        decoded_at=to_utc(row.decoded_at) if row.decoded_at else None,
    )


def _photo_row(conn: Connection, athlete_id: str, start: datetime, photo_id: int):  # type: ignore[no-untyped-def]
    row = conn.execute(
        select(activity_photo).where(
            activity_photo.c.id == photo_id,
            activity_photo.c.athlete_id == athlete_id,
            activity_photo.c.activity_start_time_utc == start,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="photo not found")
    return row


@router.get("/logbook/status")
def get_logbook_status(
    athlete_id: Annotated[str, Depends(require_api_key)], settings: SettingsDep
) -> LogbookStatusOut:
    return LogbookStatusOut(can_read_handwriting=bool(settings.anthropic_api_key))


@router.get("/activities/{activity_id}/photos")
def list_photos(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[PhotoOut]:
    start = _start_time(conn, athlete_id, activity_id)
    rows = conn.execute(
        select(activity_photo)
        .where(
            activity_photo.c.athlete_id == athlete_id,
            activity_photo.c.activity_start_time_utc == start,
        )
        .order_by(activity_photo.c.id)
    ).fetchall()
    return [_photo_out(r) for r in rows]


@router.post("/activities/{activity_id}/photos", status_code=201)
def upload_photo(
    activity_id: str,
    file: UploadFile,
    athlete_id: Annotated[str, Depends(require_api_key)],
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
) -> PhotoOut:
    """Attaches a photo of the athlete's handwritten notes. The original is archived untouched.
    400 for something that is not a readable JPEG/PNG/WebP/GIF image, 413 over 15 MB, 409 at the
    per-activity limit."""
    start = _start_time(conn, athlete_id, activity_id)
    content = file.file.read(MAX_PHOTO_BYTES + 1)
    if len(content) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="the photo is too large (15 MB maximum)")
    try:
        prepare_image_for_reading(content)  # proves it decodes, before anything is stored
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="use a JPEG, PNG, WebP or GIF photo (not HEIC)")
    existing = conn.execute(
        select(activity_photo.c.id).where(
            activity_photo.c.athlete_id == athlete_id,
            activity_photo.c.activity_start_time_utc == start,
        )
    ).fetchall()
    if len(existing) >= MAX_PHOTOS_PER_ACTIVITY:
        raise HTTPException(status_code=409, detail="too many photos on this activity")

    raw_id = archive_raw_bytes(
        conn,
        settings.raw_archive_dir,
        athlete_id=athlete_id,
        source="athlete_upload",
        kind="activity_photo",
        content=content,
        external_id=start.isoformat(),
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    result = conn.execute(
        activity_photo.insert().values(
            athlete_id=athlete_id,
            activity_start_time_utc=start,
            raw_object_id=raw_id,
            content_type=content_type,
            byte_size=len(content),
            created_at=now,
        )
    )
    conn.commit()
    assert result.inserted_primary_key is not None
    row = _photo_row(conn, athlete_id, start, int(result.inserted_primary_key[0]))
    return _photo_out(row)


@router.get("/activities/{activity_id}/photos/{photo_id}/image")
def get_photo_image(
    activity_id: str,
    photo_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
) -> Response:
    """The photo, as a JPEG at most 1600 px on its long side (the original stays in the archive)."""
    start = _start_time(conn, athlete_id, activity_id)
    row = _photo_row(conn, athlete_id, start, photo_id)
    storage_path = conn.execute(
        select(raw_object.c.storage_path).where(raw_object.c.id == row.raw_object_id)
    ).scalar_one()
    content = read_raw_bytes(settings.raw_archive_dir, storage_path)
    image, media_type = prepare_image_for_reading(content)
    return Response(content=image, media_type=media_type, headers={"Cache-Control": "private"})


@router.delete("/activities/{activity_id}/photos/{photo_id}", status_code=204)
def delete_photo(
    activity_id: str,
    photo_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> None:
    """Removes the photo and what was read from it. The sets already confirmed stay. The archived
    original is never deleted."""
    start = _start_time(conn, athlete_id, activity_id)
    _photo_row(conn, athlete_id, start, photo_id)
    conn.execute(
        activity_logged_set.update()
        .where(activity_logged_set.c.photo_id == photo_id)
        .values(photo_id=None)
    )
    conn.execute(delete(activity_photo).where(activity_photo.c.id == photo_id))
    conn.commit()


def run_read(engine: Engine, settings: Settings, athlete_id: str, photo_id: int) -> None:
    """The background job behind POST .../read. Nothing is held open across the API call: the
    bytes are read first, the call made with no transaction, and only then the result written."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(activity_photo.c.raw_object_id).where(
                    activity_photo.c.id == photo_id, activity_photo.c.athlete_id == athlete_id
                )
            ).first()
            if row is None:
                return
            storage_path = conn.execute(
                select(raw_object.c.storage_path).where(raw_object.c.id == row.raw_object_id)
            ).scalar_one()
        content = read_raw_bytes(settings.raw_archive_dir, storage_path)
        assert settings.anthropic_api_key
        decoded = decode_logbook_photo(
            content, api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
        values: dict[str, object] = {
            "decode_status": "done",
            "decode_error": None,
            "decoded_json": decoded.model_dump_json(),
            "decode_model": settings.anthropic_model,
            "decoded_at": datetime.now(UTC).replace(tzinfo=None),
        }
    except Exception as exc:
        log.warning("reading photo %s failed: %s", photo_id, exc)
        message = str(exc) if isinstance(exc, ValueError) else "Could not read the handwriting."
        values = {"decode_status": "failed", "decode_error": message[:500]}
    with engine.connect() as conn:
        conn.execute(
            activity_photo.update()
            .where(activity_photo.c.id == photo_id, activity_photo.c.athlete_id == athlete_id)
            .values(**values)
        )
        conn.commit()


@router.post("/activities/{activity_id}/photos/{photo_id}/read", status_code=202)
def read_photo(
    activity_id: str,
    photo_id: int,
    background_tasks: BackgroundTasks,
    athlete_id: Annotated[str, Depends(require_api_key)],
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
    engine: Engine = Depends(get_engine),
) -> PhotoOut:
    """Starts reading the handwriting of this photo with Claude (the photo is sent to the
    Anthropic API). Returns at once with the photo marked pending; poll the photo list. 503 when
    no API key is configured, 409 while a reading is already running."""
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=503,
            detail="reading handwriting needs PERSEVERER_ANTHROPIC_API_KEY in the configuration",
        )
    start = _start_time(conn, athlete_id, activity_id)
    row = _photo_row(conn, athlete_id, start, photo_id)
    if _photo_out(row).decode_status == "pending":
        raise HTTPException(status_code=409, detail="this photo is already being read")
    now = datetime.now(UTC).replace(tzinfo=None)
    conn.execute(
        activity_photo.update()
        .where(activity_photo.c.id == photo_id)
        .values(decode_status="pending", decode_started_at=now, decode_error=None)
    )
    conn.commit()
    background_tasks.add_task(run_read, engine, settings, athlete_id, photo_id)
    return _photo_out(_photo_row(conn, athlete_id, start, photo_id))


def _logbook(conn: Connection, athlete_id: str, start: datetime) -> LogbookOut:
    rows = conn.execute(
        select(activity_logged_set)
        .where(
            activity_logged_set.c.athlete_id == athlete_id,
            activity_logged_set.c.activity_start_time_utc == start,
        )
        .order_by(activity_logged_set.c.position)
    ).fetchall()
    exercises: list[LoggedExerciseOut] = []
    last_block = -1
    for r in rows:
        if r.block_index == last_block and exercises:
            exercises[-1].sets.append(
                LoggedSetIn(reps=r.reps, weight_kg=r.weight_kg, duration_s=r.duration_s)
            )
            continue
        last_block = r.block_index
        exercises.append(
            LoggedExerciseOut(
                label=r.exercise_label,
                exercise_category=r.exercise_category,
                exercise_name=r.exercise_name,
                note=r.note,
                photo_id=r.photo_id,
                sets=[LoggedSetIn(reps=r.reps, weight_kg=r.weight_kg, duration_s=r.duration_s)],
            )
        )
    return LogbookOut(exercises=exercises)


@router.get("/activities/{activity_id}/logbook")
def get_logbook(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> LogbookOut:
    """The sets the athlete confirmed from their notes, exercises in the order done. Empty when
    nothing was saved."""
    return _logbook(conn, athlete_id, _start_time(conn, athlete_id, activity_id))


@router.put("/activities/{activity_id}/logbook")
def put_logbook(
    activity_id: str,
    payload: LogbookIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> LogbookOut:
    """Replaces the logbook of this activity with these exercises, in this order. An exercise with
    no set is dropped; an empty list clears the logbook."""
    start = _start_time(conn, athlete_id, activity_id)
    photo_ids = {
        r.id
        for r in conn.execute(
            select(activity_photo.c.id).where(
                activity_photo.c.athlete_id == athlete_id,
                activity_photo.c.activity_start_time_utc == start,
            )
        )
    }
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = []
    position = 0
    block = 0
    for exercise in payload.exercises:
        if not exercise.sets:
            continue
        for s in exercise.sets:
            rows.append(
                {
                    "athlete_id": athlete_id,
                    "activity_start_time_utc": start,
                    "position": position,
                    "block_index": block,
                    "exercise_label": exercise.label.strip(),
                    "exercise_category": exercise.exercise_category,
                    "exercise_name": exercise.exercise_name,
                    "reps": s.reps,
                    "weight_kg": s.weight_kg,
                    "duration_s": s.duration_s,
                    "note": exercise.note,
                    "photo_id": exercise.photo_id if exercise.photo_id in photo_ids else None,
                    "created_at": now,
                }
            )
            position += 1
        block += 1
    conn.execute(
        delete(activity_logged_set).where(
            activity_logged_set.c.athlete_id == athlete_id,
            activity_logged_set.c.activity_start_time_utc == start,
        )
    )
    if rows:
        conn.execute(activity_logged_set.insert(), rows)
    conn.commit()
    return _logbook(conn, athlete_id, start)
