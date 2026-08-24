"""Tests for GET .../merge-preview/{other_id}, POST .../merge, and the duplicate_candidates
field on GET /activities/{id}. See src/perseverer/activity_merge.py for the underlying logic.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.archive import archive_raw_bytes
from perseverer.config import Settings
from perseverer.db.schema import activity, activity_merge_override, activity_source_link
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.ingest_dispatch import FIT_KIND

START = dt.datetime(2022, 6, 7, 12, 48, 32)


def _seed_activity(
    engine: Engine,
    test_settings: Settings,
    *,
    activity_id: str,
    source: str,
    content: bytes,
    distance_m: float,
    duration_s: float = 41913.0,
    calories: float | None = None,
    sport: str = "hiking",
) -> None:
    with engine.connect() as conn:
        raw_object_id = archive_raw_bytes(
            conn,
            test_settings.raw_archive_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source=source,
            kind=FIT_KIND,
            content=content,
        )
        now = dt.datetime.now(dt.UTC)
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=START,
                utc_offset_s=0,
                local_date="2022-06-07",
                sport=sport,
                sub_sport="generic",
                name="Half Dome",
                duration_s=duration_s,
                moving_duration_s=duration_s,
                distance_m=distance_m,
                elevation_gain_m=1593.1,
                calories=calories,
                primary_source=source,
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            activity_source_link.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=activity_id,
                source=source,
                external_id=f"{source}-ext",
                raw_object_id=raw_object_id,
                ingested_at=now,
            )
        )
        conn.commit()


def _seed_pair(engine: Engine, test_settings: Settings) -> None:
    _seed_activity(
        engine,
        test_settings,
        activity_id="self1",
        source="fit_folder",
        content=b"fit-bytes",
        distance_m=29445.7,
        calories=None,
    )
    _seed_activity(
        engine,
        test_settings,
        activity_id="other1",
        source="strava_export",
        content=b"strava-bytes",
        distance_m=26863.1,
        calories=378.0,
    )


class TestDuplicateCandidatesOnGetActivity:
    def test_flags_a_cross_source_duplicate(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_pair(engine, test_settings)

        r = client.get("/api/v1/activities/self1", headers=auth_headers)
        assert r.status_code == 200
        candidates = r.json()["duplicate_candidates"]
        assert [c["id"] for c in candidates] == ["other1"]

    def test_empty_for_a_genuinely_unique_activity(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_activity(
            engine,
            test_settings,
            activity_id="solo1",
            source="fit_folder",
            content=b"solo-bytes",
            distance_m=5000.0,
        )

        r = client.get("/api/v1/activities/solo1", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["duplicate_candidates"] == []


class TestGetActivityMergePreview:
    def test_lists_field_comparisons(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_pair(engine, test_settings)

        r = client.get("/api/v1/activities/self1/merge-preview/other1", headers=auth_headers)
        assert r.status_code == 200
        by_field = {f["field"]: f for f in r.json()["fields"]}
        assert by_field["distance_m"]["self_value"] == 29445.7
        assert by_field["distance_m"]["other_value"] == 26863.1
        assert by_field["calories"]["self_value"] is None
        assert by_field["calories"]["other_value"] == 378.0

    def test_404_for_unknown_other_activity(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_pair(engine, test_settings)

        r = client.get(
            "/api/v1/activities/self1/merge-preview/doesnotexist", headers=auth_headers
        )
        assert r.status_code == 404


class TestPostActivityMerge:
    def test_merges_and_returns_the_survivor(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_pair(engine, test_settings)

        r = client.post(
            "/api/v1/activities/self1/merge",
            json={"other_activity_id": "other1", "field_choices": {"calories": "other"}},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "self1"
        assert body["calories"] == 378.0
        assert body["distance_m"] == 29445.7  # not chosen -- stays self's own value

        with engine.connect() as conn:
            other_row = conn.execute(
                select(activity.c.deleted_at).where(activity.c.id == "other1")
            ).fetchone()
            override_row = conn.execute(
                select(activity_merge_override).where(
                    activity_merge_override.c.athlete_id == DEFAULT_ATHLETE_ID
                )
            ).fetchone()
        assert other_row is not None
        assert other_row.deleted_at is not None
        assert override_row is not None

    def test_404_for_unknown_other_activity(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_pair(engine, test_settings)

        r = client.post(
            "/api/v1/activities/self1/merge",
            json={"other_activity_id": "doesnotexist", "field_choices": {}},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_404_for_unknown_self_activity(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        r = client.post(
            "/api/v1/activities/doesnotexist/merge",
            json={"other_activity_id": "alsomissing", "field_choices": {}},
            headers=auth_headers,
        )
        assert r.status_code == 404
