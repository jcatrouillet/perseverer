"""Tests for garmin_activity_summary.py: parsing summarizedActivitiesExport JSON, and matching
+ correcting activity rows from it. See that module's own docstring for the real bug this fixes
(a FIT file's device-recorded sport being simply wrong, corrected by Garmin's own
reclassification, which this project already archives raw but never parsed until now).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import Connection, Engine, select

from perseverer.archive import archive_raw_bytes
from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, athlete, metadata, raw_object
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.garmin_activity_summary import (
    CorrectionResult,
    GarminActivitySummaryEntry,
    backfill_activity_corrections,
    correct_activities_from_summary,
    parse_summarized_activities_json,
)


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


def _add_activity(
    conn: Connection,
    *,
    activity_id: str,
    start_time_utc: dt.datetime,
    sport: str = "running",
    sub_sport: str = "generic",
    name: str | None = None,
) -> None:
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start_time_utc,
            utc_offset_s=0,
            local_date=start_time_utc.date().isoformat(),
            sport=sport,
            sub_sport=sub_sport,
            name=name,
            duration_s=1800.0,
            distance_m=3000.0,
            primary_source="garmin_export",
            created_at=start_time_utc,
            updated_at=start_time_utc,
        )
    )
    conn.commit()


class TestParseSummarizedActivitiesJson:
    def test_extracts_id_name_type_and_converts_epoch_ms_to_naive_utc(self) -> None:
        raw = json.dumps(
            [
                {
                    "summarizedActivitiesExport": [
                        {
                            "activityId": 22587785816,
                            "name": "Sands Cave Hike",
                            "activityType": "hiking",
                            "beginTimestamp": 1776370997000,
                        }
                    ]
                }
            ]
        ).encode("utf-8")

        entries = parse_summarized_activities_json(raw)

        assert entries == [
            GarminActivitySummaryEntry(
                garmin_activity_id=22587785816,
                name="Sands Cave Hike",
                activity_type="hiking",
                event_type_id=None,
                begin_timestamp_utc=dt.datetime.fromtimestamp(
                    1776370997000 / 1000, tz=dt.UTC
                ).replace(tzinfo=None),
            )
        ]

    def test_skips_entries_with_no_begin_timestamp(self) -> None:
        raw = json.dumps(
            [
                {
                    "summarizedActivitiesExport": [
                        {"activityId": 1, "name": "x", "activityType": "running"}
                    ]
                }
            ]
        ).encode("utf-8")
        assert parse_summarized_activities_json(raw) == []

    def test_handles_multiple_blocks_and_empty_export_lists(self) -> None:
        raw = json.dumps(
            [
                {"summarizedActivitiesExport": []},
                {
                    "summarizedActivitiesExport": [
                        {
                            "activityId": 2,
                            "name": "Run",
                            "activityType": "running",
                            "beginTimestamp": 1700000000000,
                        }
                    ]
                },
            ]
        ).encode("utf-8")
        entries = parse_summarized_activities_json(raw)
        assert len(entries) == 1
        assert entries[0].garmin_activity_id == 2


def _entry(
    *,
    activity_type: str | None,
    name: str | None,
    begin: dt.datetime,
    garmin_id: int = 1,
    event_type_id: int | None = None,
) -> GarminActivitySummaryEntry:
    return GarminActivitySummaryEntry(
        garmin_activity_id=garmin_id,
        name=name,
        activity_type=activity_type,
        event_type_id=event_type_id,
        begin_timestamp_utc=begin,
    )


def _correct(conn: Connection, entries: list[GarminActivitySummaryEntry]) -> CorrectionResult:
    return correct_activities_from_summary(conn, athlete_id=DEFAULT_ATHLETE_ID, entries=entries)


class TestCorrectActivitiesFromSummary:
    def test_corrects_sport_and_fills_in_name_on_a_close_timestamp_match(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 4, 16, 21, 23, 17)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1", start_time_utc=start, sport="running")

        entry = _entry(
            activity_type="hiking",
            name="Sands Cave Hike",
            begin=start + dt.timedelta(seconds=17),  # matches the real 17s delta observed
        )

        with engine.connect() as conn:
            result = _correct(conn, [entry])
            conn.commit()

        assert result.corrected == 1
        assert result.corrections == [("a1", "running", "hiking", None, "Sands Cave Hike")]

        with engine.connect() as conn:
            row = conn.execute(
                select(activity.c.sport, activity.c.sub_sport, activity.c.name)
            ).fetchone()
        assert row is not None
        assert (row.sport, row.sub_sport, row.name) == ("hiking", "generic", "Sands Cave Hike")

    def test_overwrites_an_existing_name_with_garmins_own(self, tmp_path: Path) -> None:
        """Per explicit athlete directive: always trust Garmin Connect's own name, whatever it
        is, over whatever is currently stored (however it got there)."""
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 4, 16, 21, 23, 17)
        with engine.connect() as conn:
            _add_activity(
                conn, activity_id="a1", start_time_utc=start, sport="running", name="My Own Title"
            )

        entry = _entry(activity_type="hiking", name="Sands Cave Hike", begin=start)
        with engine.connect() as conn:
            _correct(conn, [entry])
            conn.commit()
            row = conn.execute(select(activity.c.name)).fetchone()
        assert row is not None
        assert row.name == "Sands Cave Hike"

    def test_does_not_touch_a_correctly_classified_activity(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 4, 16, 21, 23, 17)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1", start_time_utc=start, sport="hiking")

        entry = _entry(activity_type="hiking", name=None, begin=start)
        with engine.connect() as conn:
            result = _correct(conn, [entry])
        assert result.corrected == 0
        assert result.matched_no_change == 1

    def test_skips_an_entry_with_no_activity_within_tolerance(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 4, 16, 21, 23, 17)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1", start_time_utc=start, sport="running")

        # 10 minutes away -- far outside the match tolerance.
        far_begin = start + dt.timedelta(minutes=10)
        entry = _entry(activity_type="hiking", name="Far Away", begin=far_begin)
        with engine.connect() as conn:
            result = _correct(conn, [entry])
            conn.commit()
            row = conn.execute(select(activity.c.sport)).fetchone()

        assert result.corrected == 0
        assert result.unmatched_entries == 1
        assert row is not None
        assert row.sport == "running"

    def test_skips_an_unrecognized_activity_type_but_still_fills_name(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 4, 16, 21, 23, 17)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1", start_time_utc=start, sport="running")

        entry = _entry(
            activity_type="some_new_type_we_dont_map", name="Mystery Activity", begin=start
        )
        with engine.connect() as conn:
            result = _correct(conn, [entry])
            conn.commit()
            row = conn.execute(select(activity.c.sport, activity.c.name)).fetchone()

        assert result.corrected == 1
        assert row is not None
        assert row.sport == "running"  # unmapped type -> sport left untouched
        assert row.name == "Mystery Activity"  # name still filled in

    def test_matches_the_closest_activity_when_two_are_on_the_same_day(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        morning = dt.datetime(2026, 4, 17, 7, 0, 0)
        evening = dt.datetime(2026, 4, 17, 19, 0, 0)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="morning", start_time_utc=morning, sport="running")
            _add_activity(conn, activity_id="evening", start_time_utc=evening, sport="running")

        entry = _entry(
            activity_type="hiking", name="Evening Hike", begin=evening + dt.timedelta(seconds=30)
        )
        with engine.connect() as conn:
            result = _correct(conn, [entry])
            conn.commit()
            rows = {
                r.id: r.sport
                for r in conn.execute(select(activity.c.id, activity.c.sport)).fetchall()
            }

        assert result.corrected == 1
        assert rows == {"morning": "running", "evening": "hiking"}

    def test_returns_immediately_for_an_athlete_with_no_activities(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        entry = _entry(activity_type="running", name="x", begin=dt.datetime(2026, 1, 1))
        with engine.connect() as conn:
            result = _correct(conn, [entry])
        assert result.corrected == 0
        assert result.unmatched_entries == 0

    def test_sets_is_race_and_overwrites_a_generic_name_for_a_confirmed_race(
        self, tmp_path: Path
    ) -> None:
        """eventTypeId 1, confirmed against real named races in this athlete's own export."""
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 5, 10, 8, 0, 0)
        with engine.connect() as conn:
            _add_activity(
                conn, activity_id="a1", start_time_utc=start, sport="running", name="Run"
            )

        entry = _entry(
            activity_type="running",
            name="Bay to Breakers",
            begin=start,
            event_type_id=1,
        )
        with engine.connect() as conn:
            result = _correct(conn, [entry])
            conn.commit()
            row = conn.execute(select(activity.c.name, activity.c.is_race)).fetchone()

        assert result.corrected == 1
        assert row is not None
        assert row.name == "Bay to Breakers"
        assert row.is_race is True

    def test_sets_is_race_false_for_the_uncategorized_default_event_type(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 5, 10, 8, 0, 0)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1", start_time_utc=start, sport="running")

        entry = _entry(activity_type="running", name=None, begin=start, event_type_id=9)
        with engine.connect() as conn:
            _correct(conn, [entry])
            conn.commit()
            row = conn.execute(select(activity.c.is_race)).fetchone()

        assert row is not None
        assert row.is_race is False

    def test_leaves_is_race_null_when_event_type_id_is_absent(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 5, 10, 8, 0, 0)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1", start_time_utc=start, sport="running")

        entry = _entry(activity_type="running", name="x", begin=start, event_type_id=None)
        with engine.connect() as conn:
            _correct(conn, [entry])
            conn.commit()
            row = conn.execute(select(activity.c.is_race)).fetchone()

        assert row is not None
        assert row.is_race is None

    def test_overwrites_a_name_for_a_non_race_activity_too(self, tmp_path: Path) -> None:
        """The name overwrite isn't gated on is_race -- it's unconditional per athlete
        directive, for races and non-races alike."""
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 5, 10, 8, 0, 0)
        with engine.connect() as conn:
            _add_activity(
                conn, activity_id="a1", start_time_utc=start, sport="running", name="My Own Title"
            )

        entry = _entry(
            activity_type="running", name="Garmin's Name", begin=start, event_type_id=9
        )
        with engine.connect() as conn:
            _correct(conn, [entry])
            conn.commit()
            row = conn.execute(select(activity.c.name)).fetchone()

        assert row is not None
        assert row.name == "Garmin's Name"


def _archive_summary_json(
    conn: Connection,
    archive_root: Path,
    *,
    export_entries: list[dict[str, object]],
    fetched_at: dt.datetime,
    locator: str,
) -> None:
    """Archives one `*_summarizedActivities.json` blob (real shape: a list of blocks, each with
    its own `summarizedActivitiesExport` list) and backdates its `fetched_at` to a specific,
    controlled value -- `archive_raw_bytes` always stamps `datetime.now(UTC)`, which two calls
    made microseconds apart in a test can't reliably order on its own."""
    content = json.dumps([{"summarizedActivitiesExport": export_entries}]).encode("utf-8")
    raw_id = archive_raw_bytes(
        conn,
        archive_root,
        athlete_id=DEFAULT_ATHLETE_ID,
        source="garmin_export",
        kind="garmin_export_json",
        content=content,
        locator=locator,
    )
    conn.execute(
        raw_object.update().where(raw_object.c.id == raw_id).values(fetched_at=fetched_at)
    )
    conn.commit()


class TestBackfillActivityCorrectionsOrdering:
    """Regression tests for a real, confirmed bug: an athlete can have more than one
    `*_summarizedActivities.json` archived (e.g. two separate GDPR export requests taken months
    apart) that disagree about one activity's own auto-generated name -- both Garmin's own
    generic auto-template, not a real athlete-given title, so neither is inherently "more
    correct". `backfill_activity_corrections` used to read the archived blobs via an unordered
    SQL query, so which snapshot's name survived depended on SQLite's unspecified row order --
    confirmed live: repeated runs against the same real archive flip-flopped one activity's name
    between two values. Fixed by ordering the underlying query by `fetched_at` (then `id` as a
    tiebreak), so the newest snapshot's entries are always parsed last and always win."""

    def test_the_newer_snapshots_name_wins_regardless_of_which_was_archived_first(
        self, tmp_path: Path
    ) -> None:
        archive_root = tmp_path / "archive"
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 4, 16, 21, 23, 17)
        begin_ms = int(start.replace(tzinfo=dt.UTC).timestamp() * 1000)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1", start_time_utc=start, sport="hiking")

            _archive_summary_json(
                conn,
                archive_root,
                export_entries=[
                    {
                        "activityId": 1,
                        "name": "Monterey County Hiking",
                        "activityType": "hiking",
                        "beginTimestamp": begin_ms,
                    }
                ],
                fetched_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                locator="older_summarizedActivities.json",
            )
            _archive_summary_json(
                conn,
                archive_root,
                export_entries=[
                    {
                        "activityId": 1,
                        "name": "Monterey County Other",
                        "activityType": "hiking",
                        "beginTimestamp": begin_ms,
                    }
                ],
                fetched_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
                locator="newer_summarizedActivities.json",
            )

            # Run several times -- before the fix, this alternated between the two names
            # depending on SQLite's unordered row return; after it, every run converges on the
            # same, deterministic result.
            names = []
            for _ in range(4):
                backfill_activity_corrections(conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID)
                conn.commit()
                names.append(conn.execute(select(activity.c.name)).scalar_one())

        assert names == ["Monterey County Other"] * 4

    def test_the_newer_snapshots_name_wins_even_when_archived_first(
        self, tmp_path: Path
    ) -> None:
        """Same as above, but the *older* snapshot (by fetched_at) happens to be archived
        second -- proves the ordering is driven by fetched_at, not by insertion/call order."""
        archive_root = tmp_path / "archive"
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 4, 16, 21, 23, 17)
        begin_ms = int(start.replace(tzinfo=dt.UTC).timestamp() * 1000)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1", start_time_utc=start, sport="hiking")

            _archive_summary_json(
                conn,
                archive_root,
                export_entries=[
                    {
                        "activityId": 1,
                        "name": "Monterey County Other",
                        "activityType": "hiking",
                        "beginTimestamp": begin_ms,
                    }
                ],
                fetched_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
                locator="newer_summarizedActivities.json",
            )
            _archive_summary_json(
                conn,
                archive_root,
                export_entries=[
                    {
                        "activityId": 1,
                        "name": "Monterey County Hiking",
                        "activityType": "hiking",
                        "beginTimestamp": begin_ms,
                    }
                ],
                fetched_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                locator="older_summarizedActivities.json",
            )

            backfill_activity_corrections(conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()
            name = conn.execute(select(activity.c.name)).scalar_one()

        assert name == "Monterey County Other"
