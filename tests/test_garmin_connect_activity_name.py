"""Tests for garmin_connect_activity_name.py: the Garmin-activityName title backfill. No network
call is involved -- the JSON summary is already archived at ingest time, read straight off disk
via archive.read_raw_bytes, same as test_weather_titles.py's fixtures read/write real gzip bytes
under tmp_path rather than mocking the archive layer itself.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import Connection, Engine, select

from perseverer.archive import archive_raw_bytes
from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, activity_source_link, athlete, metadata
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.garmin_connect_activity_name import backfill_garmin_activity_names


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


def _seed_garmin_activity(
    conn: Connection,
    archive_root: Path,
    *,
    activity_id: str,
    external_id: str = "24067233886",
    name: str | None = "Run",
    sport: str = "running",
    sub_sport: str | None = None,
    activity_name: str | None = "Santa Clara - W12 Fri . [Consolidation] Easy",
    start: dt.datetime = dt.datetime(2026, 8, 2, 8, 0, tzinfo=dt.UTC),
) -> None:
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start,
            utc_offset_s=0,
            local_date=start.date().isoformat(),
            sport=sport,
            sub_sport=sub_sport,
            name=name,
            duration_s=1800.0,
            distance_m=5000.0,
            primary_source="garmin_connect",
            created_at=start,
            updated_at=start,
        )
    )
    # A garmin_connect activity_source_link always points its raw_object_id at the FIT
    # download's own raw_object (see module docstring) -- archived here as arbitrary bytes,
    # since backfill_garmin_activity_names never reads through this FK at all.
    fit_raw_id = archive_raw_bytes(
        conn,
        archive_root,
        athlete_id=DEFAULT_ATHLETE_ID,
        source="garmin_connect",
        kind="fit",
        content=b"fake-fit-bytes",
        external_id=external_id,
    )
    if activity_name is not None:
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            kind="garmin_connect_json",
            content=json.dumps({"activityId": external_id, "activityName": activity_name}).encode(
                "utf-8"
            ),
            external_id=external_id,
        )
    conn.execute(
        activity_source_link.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            source="garmin_connect",
            external_id=external_id,
            raw_object_id=fit_raw_id,
            ingested_at=start,
        )
    )
    conn.commit()


class TestBackfillGarminActivityNames:
    def test_replaces_a_bare_generic_default_with_garmins_own_name(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_garmin_activity(conn, tmp_path / "raw", activity_id="a1", name="Run")

        with engine.connect() as conn:
            changes = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert changes == [
            ("a1", "Run", "Santa Clara - W12 Fri . [Consolidation] Easy"),
        ]
        with engine.connect() as conn:
            row = conn.execute(select(activity.c.name).where(activity.c.id == "a1")).one()
            assert row.name == "Santa Clara - W12 Fri . [Consolidation] Easy"

    def test_preserves_an_already_applied_weather_emoji_prefix(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_garmin_activity(conn, tmp_path / "raw", activity_id="a1", name="☀️ Run")

        with engine.connect() as conn:
            changes = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert changes == [
            (
                "a1",
                "☀️ Run",
                "☀️ Santa Clara - W12 Fri . [Consolidation] Easy",
            ),
        ]

    def test_never_touches_a_real_custom_title(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_garmin_activity(conn, tmp_path / "raw", activity_id="a1", name="My Favorite Loop")

        with engine.connect() as conn:
            changes = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert changes == []
        with engine.connect() as conn:
            row = conn.execute(select(activity.c.name).where(activity.c.id == "a1")).one()
            assert row.name == "My Favorite Loop"

    def test_skips_a_sport_with_no_generic_default(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_garmin_activity(
                conn, tmp_path / "raw", activity_id="a1", name="Ride", sport="cycling"
            )

        with engine.connect() as conn:
            changes = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert changes == []

    def test_replaces_the_bare_yoga_default_via_the_sub_sport_keyed_lookup(
        self, tmp_path: Path
    ) -> None:
        """Regression test for a real, confirmed gap found live: "training" has no single
        dominant default (unlike running/walking/...), so it's absent from
        _GENERIC_DEFAULT_NAME_BY_SPORT -- but "yoga" specifically does have one ("Yoga"), which
        3 real garmin_connect-sourced activities were stuck showing verbatim even though Garmin's
        own activityName was genuinely richer ("Foundation Yoga")."""
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_garmin_activity(
                conn,
                tmp_path / "raw",
                activity_id="a1",
                name="Yoga",
                sport="training",
                sub_sport="yoga",
                activity_name="Foundation Yoga",
            )

        with engine.connect() as conn:
            changes = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert changes == [("a1", "Yoga", "Foundation Yoga")]
        with engine.connect() as conn:
            row = conn.execute(select(activity.c.name).where(activity.c.id == "a1")).one()
            assert row.name == "Foundation Yoga"

    def test_a_training_sub_sport_with_no_recognized_default_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """strength_training's own real device default ("Strength") isn't in
        _GENERIC_DEFAULT_NAME_BY_SPORT_SUB_SPORT (only yoga has confirmed live evidence) --
        never touched, same as any other unrecognized generic default."""
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_garmin_activity(
                conn,
                tmp_path / "raw",
                activity_id="a1",
                name="Strength",
                sport="training",
                sub_sport="strength_training",
                activity_name="Alpine Fit",
            )

        with engine.connect() as conn:
            changes = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert changes == []

    def test_skips_when_garmins_own_name_is_no_more_informative(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_garmin_activity(
                conn, tmp_path / "raw", activity_id="a1", name="Run", activity_name="Run"
            )

        with engine.connect() as conn:
            changes = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert changes == []

    def test_skips_an_activity_with_no_garmin_connect_json_archived(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_garmin_activity(
                conn, tmp_path / "raw", activity_id="a1", name="Run", activity_name=None
            )

        with engine.connect() as conn:
            changes = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert changes == []

    def test_dry_run_reports_changes_without_writing_anything(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_garmin_activity(conn, tmp_path / "raw", activity_id="a1", name="Run")

        with engine.connect() as conn:
            changes = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID, dry_run=True
            )

        assert changes == [
            ("a1", "Run", "Santa Clara - W12 Fri . [Consolidation] Easy"),
        ]
        with engine.connect() as conn:
            row = conn.execute(select(activity.c.name).where(activity.c.id == "a1")).one()
            assert row.name == "Run"  # untouched

    def test_a_second_run_is_a_no_op(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_garmin_activity(conn, tmp_path / "raw", activity_id="a1", name="Run")

        with engine.connect() as conn:
            first = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )
        with engine.connect() as conn:
            second = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert len(first) == 1
        assert second == []

    def test_deduplicates_when_the_same_external_id_was_archived_multiple_times(
        self, tmp_path: Path
    ) -> None:
        """A real garmin_connect activity gets its garmin_connect_json summary re-archived on
        every sync run that still has it inside the rolling window -- the bytes shift slightly
        each time (e.g. embedded fetch metadata), so each re-fetch content-hashes to a brand new
        raw_object row rather than deduplicating against the last one, even though external_id
        never changes. Confirmed against the real dev database before this test was written."""
        engine = _engine(tmp_path)
        archive_root = tmp_path / "raw"
        with engine.connect() as conn:
            _seed_garmin_activity(
                conn, archive_root, activity_id="a1", name="Run", activity_name="First Name"
            )
            # A second, later sync run re-archives the same external_id with a different name.
            archive_raw_bytes(
                conn,
                archive_root,
                athlete_id=DEFAULT_ATHLETE_ID,
                source="garmin_connect",
                kind="garmin_connect_json",
                content=json.dumps(
                    {"activityId": "24067233886", "activityName": "Second Name"}
                ).encode("utf-8"),
                external_id="24067233886",
            )
            conn.commit()

        with engine.connect() as conn:
            changes = backfill_garmin_activity_names(
                conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID
            )

        assert changes == [("a1", "Run", "Second Name")]

    def test_skips_an_activity_with_no_garmin_connect_source_link(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            conn.execute(
                activity.insert().values(
                    id="a1",
                    athlete_id=DEFAULT_ATHLETE_ID,
                    start_time_utc=dt.datetime(2026, 8, 2, 8, 0, tzinfo=dt.UTC),
                    utc_offset_s=0,
                    local_date="2026-08-02",
                    sport="running",
                    name="Run",
                    duration_s=1800.0,
                    distance_m=5000.0,
                    primary_source="fit_folder",
                    created_at=dt.datetime(2026, 8, 2, 8, 0, tzinfo=dt.UTC),
                    updated_at=dt.datetime(2026, 8, 2, 8, 0, tzinfo=dt.UTC),
                )
            )
            conn.commit()

        with engine.connect() as conn:
            changes = backfill_garmin_activity_names(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID
            )

        assert changes == []
