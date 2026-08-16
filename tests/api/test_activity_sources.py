"""Tests for GET /activities/{id}/sources and POST /activities/{id}/sources/{link_id}/split
(Phase 8 Milestone B -- see docs/adr/0012-phase-8-strava-merge-insights.md). The "both sources
inspectable" and "undo a wrong merge, non-destructively" halves of the acceptance criterion.
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from sporthealth.archive import archive_raw_bytes
from sporthealth.config import Settings
from sporthealth.db.schema import activity, activity_source_link, merge_decision
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from tests.api.conftest import seed_activity

_GPX_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="StravaGPX" version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
 <trk>
  <trkseg>
   <trkpt lat="37.6656890" lon="-112.1102740">
    <ele>2082.6</ele><time>2026-04-17T22:17:09Z</time>
   </trkpt>
   <trkpt lat="37.6657130" lon="-112.1102920">
    <ele>2085.0</ele><time>2026-04-17T22:47:09Z</time>
   </trkpt>
  </trkseg>
 </trk>
</gpx>
"""

_CSV_ROW_JSON = json.dumps(
    [
        ["Activity ID", "999111"],
        ["Activity Type", "Hike"],
        ["Activity Name", "Bryce Canyon hike"],
        ["Elapsed Time", "1800"],
        ["Distance", "4800.5"],
        ["Moving Time", "1780"],
        ["Elevation Gain", "50"],
        ["Calories", "300"],
    ]
).encode("utf-8")


def _add_link(
    conn: object,
    *,
    activity_id: str,
    source: str,
    external_id: str,
    raw_object_id: int,
    ingested_at: dt.datetime,
) -> int:
    result = conn.execute(  # type: ignore[attr-defined]
        activity_source_link.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            source=source,
            external_id=external_id,
            raw_object_id=raw_object_id,
            ingested_at=ingested_at,
        )
    )
    conn.commit()  # type: ignore[attr-defined]
    assert result.inserted_primary_key is not None
    link_id = result.inserted_primary_key[0]
    assert isinstance(link_id, int)
    return link_id


def test_sources_404_for_unknown_activity(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/activities/doesnotexist/sources", headers=auth_headers)
    assert r.status_code == 404


def test_sources_lists_links_and_merge_decisions(
    client: TestClient, auth_headers: dict[str, str], engine: Engine, test_settings: Settings
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
        raw_id = archive_raw_bytes(
            conn,
            test_settings.raw_archive_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="strava_export",
            kind="strava_export_gpx",
            content=_GPX_BODY,
        )
        now = dt.datetime(2026, 4, 18, 8, 0, 0)
        _add_link(
            conn,
            activity_id="a1",
            source="fit_folder",
            external_id="fit-ext-1",
            raw_object_id=raw_id,
            ingested_at=now,
        )
        _add_link(
            conn,
            activity_id="a1",
            source="strava_export",
            external_id="999111",
            raw_object_id=raw_id,
            ingested_at=now,
        )
        conn.execute(
            merge_decision.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                matched_activity_id="a1",
                candidate_ref="2026-04-17T22:17:09",
                decision="matched",
                reasons=json.dumps(["start_time delta 0s <= 180s", "sport family match"]),
                inputs=json.dumps({}),
                decided_at=now,
            )
        )
        conn.commit()

    r = client.get("/api/v1/activities/a1/sources", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert {s["source"] for s in body["sources"]} == {"fit_folder", "strava_export"}
    assert all(s["can_split"] for s in body["sources"])
    assert len(body["merge_decisions"]) == 1
    assert "sport family match" in body["merge_decisions"][0]["reasons"]


def test_split_rejects_the_only_source(
    client: TestClient, auth_headers: dict[str, str], engine: Engine, test_settings: Settings
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
        raw_id = archive_raw_bytes(
            conn,
            test_settings.raw_archive_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            kind="fit",
            content=b"irrelevant",
        )
        link_id = _add_link(
            conn,
            activity_id="a1",
            source="fit_folder",
            external_id="fit-ext-1",
            raw_object_id=raw_id,
            ingested_at=dt.datetime(2025, 6, 1),
        )

    r = client.post(f"/api/v1/activities/a1/sources/{link_id}/split", headers=auth_headers)
    assert r.status_code == 400


def test_split_creates_a_new_activity_with_csv_overlay(
    client: TestClient, auth_headers: dict[str, str], engine: Engine, test_settings: Settings
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="hiking")
        fit_raw_id = archive_raw_bytes(
            conn,
            test_settings.raw_archive_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            kind="fit",
            content=b"irrelevant",
        )
        _add_link(
            conn,
            activity_id="a1",
            source="fit_folder",
            external_id="fit-ext-1",
            raw_object_id=fit_raw_id,
            ingested_at=dt.datetime(2025, 6, 1),
        )

        gpx_raw_id = archive_raw_bytes(
            conn,
            test_settings.raw_archive_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="strava_export",
            kind="strava_export_gpx",
            content=_GPX_BODY,
        )
        archive_raw_bytes(
            conn,
            test_settings.raw_archive_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="strava_export",
            kind="strava_export_csv_row",
            content=_CSV_ROW_JSON,
            external_id="999111",
        )
        strava_link_id = _add_link(
            conn,
            activity_id="a1",
            source="strava_export",
            external_id="999111",
            raw_object_id=gpx_raw_id,
            ingested_at=dt.datetime(2026, 4, 18),
        )

    r = client.post(
        f"/api/v1/activities/a1/sources/{strava_link_id}/split", headers=auth_headers
    )
    assert r.status_code == 200
    new_id = r.json()["new_activity_id"]
    assert new_id != "a1"

    with engine.connect() as conn:
        new_row = conn.execute(select(activity).where(activity.c.id == new_id)).fetchone()
        assert new_row is not None
        assert new_row.sport == "hiking"
        assert new_row.name == "Bryce Canyon hike"
        assert new_row.distance_m == 4800.5  # from the CSV overlay, not the bare GPX

        links = conn.execute(
            select(activity_source_link.c.activity_id, activity_source_link.c.source).where(
                activity_source_link.c.id.in_([strava_link_id])
            )
        ).fetchall()
        assert links[0].activity_id == new_id  # repointed, not duplicated

        original = conn.execute(
            select(activity_source_link.c.source).where(
                activity_source_link.c.activity_id == "a1"
            )
        ).fetchall()
        assert [row.source for row in original] == ["fit_folder"]  # untouched
