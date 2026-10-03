"""Pushing a planned workout's GPX route to Garmin as a private course: the pure payload builder,
the adapter call sequence against a fake client (no real network, ever), and the orchestration
inside `push_planned_workout` (a course failure never fails the workout). See
docs/adr/0017-planned-workout-gpx-route.md for the live verification these shapes come from."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from garminconnect import GarminConnectConnectionError
from sqlalchemy import Engine, select

from perseverer.adapters.garmin_connect import (
    GarminConnectAdapter,
    GarminRateLimitAborted,
    RateLimiter,
    RateLimitSettings,
)
from perseverer.archive import archive_raw_bytes
from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, metadata, planned_workout, planned_workout_step
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.garmin_course import (
    IMPORTED_SOURCE_TYPE_ID,
    PRIVATE_RULE_PK,
    CourseError,
    build_course_payload,
    course_privacy_key,
)
from perseverer.planned_workouts import push_planned_workout

GPX = b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg/></trk></gpx>'


def _draft() -> dict[str, Any]:
    return {
        "courseId": None,
        "courseName": "x",
        "geoPoints": [
            {"latitude": 37.0, "longitude": -122.0, "elevation": None, "distance": 0.0},
            {"latitude": 37.01, "longitude": -122.0, "elevation": None, "distance": 0.0},
        ],
        "courseLines": [{"sortOrder": 1}],
    }


class _Resp:
    def __init__(self, body: Any) -> None:
        self._body = body

    def json(self) -> Any:
        return self._body


class FakeCourseClient:
    """Stands in for `garminconnect.Garmin`: `.client.request(...)` is the generic call the adapter
    makes for `course-service`, and `login` loads the token store. Workout methods exist for the
    orchestration tests."""

    def __init__(
        self,
        *,
        privacy: str | None = "private",
        in_list: bool = True,
        raise_429_on: str | None = None,
        course_id: int = 777,
    ) -> None:
        self.privacy = privacy
        self.in_list = in_list
        self.raise_429_on = raise_429_on
        self.course_id = course_id
        self.calls: list[tuple[str, str]] = []
        self.created_payloads: list[dict[str, Any]] = []
        self.client = self  # adapter reaches `client.client.request`

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        return None, None

    def request(self, method: str, _domain: str, path: str, **kwargs: Any) -> _Resp:
        self.calls.append((method, path))
        if self.raise_429_on == method + " " + path:
            raise GarminConnectConnectionError("API Error 429 - Too many requests")
        if method == "POST" and path.endswith("/course/import"):
            assert "file" in kwargs["files"]
            return _Resp(_draft())
        if method == "POST" and path.endswith("/course"):
            self.created_payloads.append(kwargs["json"])
            return _Resp({"courseId": self.course_id})
        if method == "GET" and path.endswith("/course"):
            if not self.in_list:
                return _Resp([])
            return _Resp([{"courseId": self.course_id, "privacyRule": {"typeKey": self.privacy}}])
        if method == "DELETE":
            return _Resp({})
        raise AssertionError(f"unexpected call {method} {path}")

    # --- workout methods (orchestration tests) ---
    def delete_workout(self, workout_id: int) -> None: ...

    def upload_workout(self, workout_json: dict[str, Any]) -> dict[str, Any]:
        return {"workoutId": 99}

    def schedule_workout(self, workout_id: int, date_str: str) -> dict[str, Any]:
        return {"date": date_str}


def _adapter(client: FakeCourseClient, tmp_path: Path) -> GarminConnectAdapter:
    adapter = GarminConnectAdapter(tmp_path / "tokens", RateLimiter(0, 999), lambda: client)
    adapter.authenticate()
    return adapter


# --- pure ------------------------------------------------------------------------------------


def test_payload_sets_the_verified_fields_and_recomputes_distance() -> None:
    payload = build_course_payload(_draft(), "Long run 2026-10-10 (Perseverer)")
    assert payload["courseName"] == "Long run 2026-10-10 (Perseverer)"
    assert payload["activityTypePk"] == 1
    assert payload["sourceTypeId"] == IMPORTED_SOURCE_TYPE_ID
    assert payload["rulePK"] == PRIVATE_RULE_PK
    assert payload["distanceMeter"] == pytest.approx(1112, rel=0.01)
    assert payload["startPoint"] == {"latitude": 37.0, "longitude": -122.0}
    assert payload["geoPoints"][-1]["distance"] == pytest.approx(1112, rel=0.01)
    assert payload["courseLines"] == [{"sortOrder": 1}]  # the draft passes through


def test_payload_rejects_a_draft_without_points() -> None:
    with pytest.raises(CourseError):
        build_course_payload({"geoPoints": []}, "x")


def test_privacy_key_lookup() -> None:
    listing: list[dict[str, Any]] = [
        {"courseId": 1, "privacyRule": {"typeKey": "public"}},
        {"courseId": 2},
    ]
    assert course_privacy_key(listing, 1) == "public"
    assert course_privacy_key(listing, 2) is None
    assert course_privacy_key(listing, 3) is None


# --- adapter ---------------------------------------------------------------------------------


def test_push_course_imports_creates_and_verifies_private(tmp_path: Path) -> None:
    client = FakeCourseClient()
    course_id = _adapter(client, tmp_path).push_course(GPX, "My course")
    assert course_id == 777
    assert [c[0] for c in client.calls] == ["POST", "POST", "GET"]
    assert client.created_payloads[0]["rulePK"] == PRIVATE_RULE_PK


def test_a_stale_course_is_deleted_first(tmp_path: Path) -> None:
    client = FakeCourseClient()
    _adapter(client, tmp_path).push_course(GPX, "My course", existing_course_id=123)
    assert client.calls[0] == ("DELETE", "/course-service/course/123")


@pytest.mark.parametrize("privacy,in_list", [("public", True), (None, True), ("private", False)])
def test_a_course_not_verified_private_is_deleted_and_refused(
    tmp_path: Path, privacy: str | None, in_list: bool
) -> None:
    client = FakeCourseClient(privacy=privacy, in_list=in_list)
    with pytest.raises(CourseError):
        _adapter(client, tmp_path).push_course(GPX, "My course")
    assert client.calls[-1] == ("DELETE", "/course-service/course/777")


@pytest.mark.parametrize(
    "where", ["POST /course-service/course/import", "POST /course-service/course"]
)
def test_a_429_aborts_without_retrying(tmp_path: Path, where: str) -> None:
    client = FakeCourseClient(raise_429_on=where)
    with pytest.raises(GarminRateLimitAborted):
        _adapter(client, tmp_path).push_course(GPX, "My course")
    assert [c for c in client.calls if c == tuple(where.split(" "))] == [tuple(where.split(" "))]


# --- orchestration ---------------------------------------------------------------------------


def _engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    return engine


def _workout_with_route(engine: Engine, archive_dir: Path) -> int:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        raw_id = archive_raw_bytes(
            conn,
            archive_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="athlete_upload",
            kind="planned_workout_gpx",
            content=GPX,
        )
        wid = conn.execute(
            planned_workout.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-10-10",
                sport="running",
                name="Long run",
                source_text="60m easy",
                estimated_duration_s=3600,
                push_status="draft",
                route_raw_object_id=raw_id,
                route_name="Dam loop",
                route_distance_m=1000.0,
                route_polyline="x",
                route_uploaded_at=now,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key
        assert wid is not None
        conn.execute(
            planned_workout_step.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                planned_workout_id=wid[0],
                step_index=0,
                duration_type="time",
                duration_time_s=3600,
                intensity="active",
            )
        )
        conn.commit()
        return int(wid[0])


def _push(engine: Engine, wid: int, client: FakeCourseClient, tmp_path: Path) -> Any:
    with engine.connect() as conn:
        return push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=wid,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
            raw_archive_dir=tmp_path / "raw",
        )


def _row(engine: Engine, wid: int) -> Any:
    with engine.connect() as conn:
        return conn.execute(select(planned_workout).where(planned_workout.c.id == wid)).one()


def test_the_course_is_pushed_with_the_workout_and_not_pushed_twice(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    wid = _workout_with_route(engine, tmp_path / "raw")
    client = FakeCourseClient()
    assert _push(engine, wid, client, tmp_path).success
    row = _row(engine, wid)
    assert (row.push_status, row.garmin_course_id, row.garmin_course_error) == ("pushed", 777, None)
    assert client.created_payloads[0]["courseName"] == "Long run 2026-10-10 (Perseverer)"

    # An unchanged route is not re-uploaded on the next push.
    posts_before = [c for c in client.calls if c[0] == "POST"]
    assert _push(engine, wid, client, tmp_path).success
    assert [c for c in client.calls if c[0] == "POST"] == posts_before


def test_a_replaced_route_deletes_the_old_course_and_pushes_again(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    wid = _workout_with_route(engine, tmp_path / "raw")
    client = FakeCourseClient()
    _push(engine, wid, client, tmp_path)
    with engine.connect() as conn:  # a new upload after the push
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.id == wid)
            .values(
                route_uploaded_at=dt.datetime.now(dt.UTC).replace(tzinfo=None)
                + dt.timedelta(hours=1)
            )
        )
        conn.commit()
    _push(engine, wid, client, tmp_path)
    assert ("DELETE", "/course-service/course/777") in client.calls
    assert len(client.created_payloads) == 2


def test_a_course_failure_never_fails_the_workout_push(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    wid = _workout_with_route(engine, tmp_path / "raw")
    client = FakeCourseClient(privacy="public")
    result = _push(engine, wid, client, tmp_path)
    assert result.success
    row = _row(engine, wid)
    assert row.push_status == "pushed"
    assert row.garmin_course_id is None
    assert "not 'private'" in row.garmin_course_error


def test_no_route_means_no_course_calls(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    wid = _workout_with_route(engine, tmp_path / "raw")
    with engine.connect() as conn:
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.id == wid)
            .values(route_raw_object_id=None, route_polyline=None, route_uploaded_at=None)
        )
        conn.commit()
    client = FakeCourseClient()
    assert _push(engine, wid, client, tmp_path).success
    assert client.calls == []
