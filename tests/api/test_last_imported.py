"""GET /activities/last-imported -- the activity whose source file was archived most recently, for
the "Last activity imported" starting page."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import activity, activity_source_link, raw_object
from perseverer.db.seed import DEFAULT_ATHLETE_ID


def _activity(
    engine: Engine, activity_id: str, start: dt.datetime, *, deleted: bool = False
) -> None:
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=start,
                utc_offset_s=0,
                local_date=start.date().isoformat(),
                sport="running",
                name=f"Run {activity_id}",
                primary_source="fit_folder",
                created_at=start,
                updated_at=start,
                deleted_at=start if deleted else None,
            )
        )
        conn.commit()


def _import(engine: Engine, activity_id: str, fetched_at: dt.datetime, source: str) -> None:
    with engine.connect() as conn:
        inserted = conn.execute(
            raw_object.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                source=source,
                kind="fit",
                fetched_at=fetched_at,
                sha256=f"{activity_id}-{source}".ljust(64, "0")[:64],
                byte_size=1,
                storage_path="x",
                created_at=fetched_at,
            )
        ).inserted_primary_key
        assert inserted is not None
        raw_id = inserted[0]
        conn.execute(
            activity_source_link.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=activity_id,
                source=source,
                external_id=f"{activity_id}-{source}",
                raw_object_id=raw_id,
                # A rebuild resets this; the endpoint must rank by fetched_at instead.
                ingested_at=dt.datetime(2026, 1, 1),
            )
        )
        conn.commit()


def test_returns_the_most_recently_imported_not_the_most_recent_activity(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _activity(engine, "recent-run", dt.datetime(2026, 10, 5, 7, 0))
    _import(engine, "recent-run", dt.datetime(2026, 10, 5, 9, 0), "garmin_connect")
    # An old run whose file arrived later (e.g. a history import) is the last one imported.
    _activity(engine, "old-run", dt.datetime(2021, 3, 1, 7, 0))
    _import(engine, "old-run", dt.datetime(2026, 10, 6, 20, 0), "strava_export")

    r = client.get("/api/v1/activities/last-imported", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "old-run"
    assert body["local_date"] == "2021-03-01"
    assert body["imported_at"].startswith("2026-10-06T20:00:00")


def test_a_merged_activity_counts_its_latest_source_and_deleted_ones_are_ignored(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _activity(engine, "merged", dt.datetime(2026, 9, 1, 7, 0))
    _import(engine, "merged", dt.datetime(2026, 9, 1, 9, 0), "garmin_connect")
    _import(engine, "merged", dt.datetime(2026, 10, 2, 9, 0), "strava_export")
    _activity(engine, "other", dt.datetime(2026, 10, 1, 7, 0))
    _import(engine, "other", dt.datetime(2026, 10, 1, 9, 0), "garmin_connect")
    _activity(engine, "deleted", dt.datetime(2026, 10, 3, 7, 0), deleted=True)
    _import(engine, "deleted", dt.datetime(2026, 10, 3, 9, 0), "garmin_connect")

    r = client.get("/api/v1/activities/last-imported", headers=auth_headers)
    assert r.json()["id"] == "merged"


def test_404_without_activities_and_401_without_credentials(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.get("/api/v1/activities/last-imported", headers=auth_headers).status_code == 404
    assert client.get("/api/v1/activities/last-imported").status_code in (401, 503)
