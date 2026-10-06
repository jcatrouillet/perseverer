"""Tests for activity_merge.py: detecting a cross-source duplicate, comparing fields side by
side, merging with a per-field choice of which side wins, and surviving a simulated rebuild.
Fixture shape mirrors the real case this module was built for: two activities recorded for the
same real-world hike, one from a Garmin source, one from Strava, sharing the same start_time_utc
but disagreeing on several fields.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, delete, select

from perseverer.activity_merge import (
    apply_activity_merge_overrides,
    build_merge_preview,
    find_all_duplicate_pairs,
    find_duplicate_candidates,
    merge_activities,
    merge_activities_and_record,
)
from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    activity_merge_override,
    activity_metric,
    activity_source_link,
    activity_stream,
    athlete,
    lap,
    metadata,
    raw_object,
    route_geom,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.metrics.registry import get_or_register_metric

START = dt.datetime(2022, 6, 7, 12, 48, 32)


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
    source: str,
    external_id: str,
    start_time_utc: dt.datetime = START,
    sport: str = "hiking",
    distance_m: float = 26863.1,
    duration_s: float = 41913.0,
    elevation_gain_m: float = 1593.1,
    calories: float | None = 300.0,
    avg_hr: float | None = None,
    max_hr: float | None = None,
    training_load: float | None = None,
) -> None:
    now = dt.datetime.now(dt.UTC)
    # Content-addressed and idempotent, same as the real archive_raw_bytes -- lets this be
    # called again for the same (source, external_id) to simulate a rebuild's replay, which
    # never re-archives raw bytes it already has.
    sha256 = f"sha-{source}-{external_id}"
    existing = conn.execute(
        select(raw_object.c.id).where(
            raw_object.c.athlete_id == DEFAULT_ATHLETE_ID, raw_object.c.sha256 == sha256
        )
    ).scalar_one_or_none()
    if existing is not None:
        raw_object_id = existing
    else:
        result = conn.execute(
            raw_object.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                source=source,
                kind="fit",
                external_id=external_id,
                fetched_at=now,
                sha256=sha256,
                byte_size=1,
                storage_path=f"{source}/{external_id}.gz",
                created_at=now,
            )
        )
        assert result.inserted_primary_key is not None
        raw_object_id = result.inserted_primary_key[0]
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start_time_utc,
            utc_offset_s=0,
            local_date=start_time_utc.date().isoformat(),
            sport=sport,
            sub_sport="generic",
            name="Half Dome",
            duration_s=duration_s,
            moving_duration_s=duration_s,
            distance_m=distance_m,
            elevation_gain_m=elevation_gain_m,
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
            external_id=external_id,
            raw_object_id=raw_object_id,
            ingested_at=now,
        )
    )
    for key, value in (
        ("fit.session.avg_heart_rate", avg_hr),
        ("fit.session.max_heart_rate", max_hr),
        ("fit.session.training_load_peak", training_load),
    ):
        if value is None:
            continue
        get_or_register_metric(conn, metric_key=key, source=source, category="activity")
        conn.execute(
            activity_metric.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=activity_id,
                metric_key=key,
                value_num=value,
                source=source,
                created_at=now,
            )
        )


def _seed_pair(conn: Connection) -> None:
    """ "self" = fit_folder (the survivor in these tests), "other" = strava_export."""
    _add_activity(
        conn,
        activity_id="self1",
        source="fit_folder",
        external_id="fit-ext-1",
        distance_m=29445.7,
        duration_s=41913.0,
        elevation_gain_m=1593.1,
        calories=None,
        avg_hr=None,
    )
    _add_activity(
        conn,
        activity_id="other1",
        source="strava_export",
        external_id="strava-ext-1",
        distance_m=26863.1,
        duration_s=41913.0,
        elevation_gain_m=1520.0,
        calories=378.0,
        avg_hr=118.0,
        max_hr=150.0,
        training_load=42.0,
    )
    conn.execute(
        route_geom.insert().values(
            activity_id="self1",
            athlete_id=DEFAULT_ATHLETE_ID,
            encoded_polyline="self_polyline",
            simplified_polyline="self_polyline",
            start_lat=1.0,
            start_lng=1.0,
        )
    )
    conn.execute(
        route_geom.insert().values(
            activity_id="other1",
            athlete_id=DEFAULT_ATHLETE_ID,
            encoded_polyline="other_polyline",
            simplified_polyline="other_polyline",
            start_lat=2.0,
            start_lng=2.0,
        )
    )
    conn.execute(
        lap.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id="self1",
            lap_index=0,
            start_time_utc=START,
            duration_s=41913.0,
            distance_m=29445.7,
        )
    )
    conn.execute(
        lap.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id="other1",
            lap_index=0,
            start_time_utc=START,
            duration_s=41913.0,
            distance_m=26863.1,
        )
    )
    conn.execute(
        activity_stream.insert().values(
            activity_id="self1",
            athlete_id=DEFAULT_ATHLETE_ID,
            parquet_path="self1.parquet",
            n_samples=100,
            channels="[]",
        )
    )
    conn.execute(
        activity_stream.insert().values(
            activity_id="other1",
            athlete_id=DEFAULT_ATHLETE_ID,
            parquet_path="other1.parquet",
            n_samples=200,
            channels="[]",
        )
    )
    conn.commit()


class TestFindDuplicateCandidates:
    def test_finds_a_cross_source_same_activity(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_pair(conn)
            candidates = find_duplicate_candidates(
                conn, athlete_id=DEFAULT_ATHLETE_ID, activity_id="self1"
            )
        assert [c.id for c in candidates] == ["other1"]

    def test_none_when_activities_are_genuinely_different(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(
                conn,
                activity_id="a1",
                source="fit_folder",
                external_id="e1",
                start_time_utc=START,
                sport="running",
            )
            _add_activity(
                conn,
                activity_id="a2",
                source="strava_export",
                external_id="e2",
                start_time_utc=START + dt.timedelta(days=5),
                sport="cycling",
            )
            conn.commit()
            candidates = find_duplicate_candidates(
                conn, athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1"
            )
        assert candidates == []


class TestFindAllDuplicatePairs:
    def test_finds_the_pair_exactly_once(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_pair(conn)
            pairs = find_all_duplicate_pairs(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert len(pairs) == 1
        ids = {pairs[0][0].id, pairs[0][1].id}
        assert ids == {"self1", "other1"}

    def test_empty_when_nothing_duplicates(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(
                conn,
                activity_id="a1",
                source="fit_folder",
                external_id="e1",
                start_time_utc=START,
                sport="running",
            )
            _add_activity(
                conn,
                activity_id="a2",
                source="strava_export",
                external_id="e2",
                start_time_utc=START + dt.timedelta(days=5),
                sport="cycling",
            )
            conn.commit()
            pairs = find_all_duplicate_pairs(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert pairs == []


class TestBuildMergePreview:
    def test_lists_all_mergeable_fields_with_both_sides_values(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_pair(conn)
            comparisons = build_merge_preview(
                conn, athlete_id=DEFAULT_ATHLETE_ID, self_id="self1", other_id="other1"
            )
        by_field = {c.field: c for c in comparisons}
        assert by_field["distance_m"].self_value == pytest.approx(29445.7)
        assert by_field["distance_m"].other_value == pytest.approx(26863.1)
        assert by_field["calories"].self_value is None
        assert by_field["calories"].other_value == pytest.approx(378.0)
        assert by_field["avg_hr_bpm"].self_value is None
        assert by_field["avg_hr_bpm"].other_value == pytest.approx(118.0)
        assert "route" in by_field
        assert "laps" in by_field
        assert "stream" in by_field


class TestMergeActivities:
    def test_default_choices_leave_self_untouched(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_pair(conn)
            merge_activities(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                self_id="self1",
                other_id="other1",
                field_choices={},
            )
            conn.commit()
            row = conn.execute(select(activity).where(activity.c.id == "self1")).fetchone()
        assert row is not None
        assert row.distance_m == pytest.approx(29445.7)  # self's own original value

    def test_chosen_scalar_and_metric_fields_are_copied_from_other(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_pair(conn)
            merge_activities(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                self_id="self1",
                other_id="other1",
                field_choices={"calories": "other", "avg_hr_bpm": "other"},
            )
            conn.commit()
            row = conn.execute(select(activity).where(activity.c.id == "self1")).fetchone()
            assert row is not None
            assert row.calories == pytest.approx(378.0)
            # distance_m wasn't chosen -- stays self's own value.
            assert row.distance_m == pytest.approx(29445.7)

            hr_row = conn.execute(
                select(activity_metric).where(
                    activity_metric.c.activity_id == "self1",
                    activity_metric.c.metric_key == "fit.session.avg_heart_rate",
                )
            ).fetchone()
        assert hr_row is not None
        assert hr_row.value_num == pytest.approx(118.0)
        assert hr_row.source == "strava_export"  # provenance preserved, not just the number

    def test_other_choice_clears_selfs_own_value_under_a_different_alias_key(
        self, tmp_path: Path
    ) -> None:
        """Real bug, found live: avg_hr_bpm (and max_hr_bpm/training_load, same shape) can be
        stored under either a fit.session.* or strava.session.* metric_key (see
        _AVG_HR_METRIC_KEYS), and the display layer always prefers the fit.session.* one when
        both exist. Choosing "other" for avg_hr_bpm when self *already has its own*
        fit.session.avg_heart_rate row (unlike the no-prior-value case above) must clear that
        stale row -- otherwise the athlete's explicit choice is silently masked at display time
        even though the merge reported success."""
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(
                conn,
                activity_id="self1",
                source="fit_folder",
                external_id="fit-ext-1",
                distance_m=9751.64,
                duration_s=3618.58,
            )
            _add_activity(
                conn,
                activity_id="other1",
                source="strava_export",
                external_id="strava-ext-1",
                distance_m=16997.0,
                duration_s=6202.0,
            )
            now = dt.datetime.now(dt.UTC)
            # self's own pre-existing value, under the fit.session.* alias _add_activity's own
            # avg_hr/max_hr params always use -- not what's being tested here, so inserted
            # directly to control the exact metric_key (the strava.session.* alias, from a
            # Strava GPX/TCX CSV-totals overlay, is what a real strava_export activity uses).
            get_or_register_metric(
                conn,
                metric_key="fit.session.avg_heart_rate",
                source="fit_folder",
                category="activity",
            )
            get_or_register_metric(
                conn,
                metric_key="strava.session.avg_heart_rate",
                source="strava_export",
                category="activity",
            )
            conn.execute(
                activity_metric.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id="self1",
                    metric_key="fit.session.avg_heart_rate",
                    value_num=134.0,
                    source="fit_folder",
                    created_at=now,
                )
            )
            conn.execute(
                activity_metric.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id="other1",
                    metric_key="strava.session.avg_heart_rate",
                    value_num=135.0,
                    source="strava_export",
                    created_at=now,
                )
            )
            conn.commit()
            merge_activities(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                self_id="self1",
                other_id="other1",
                field_choices={"avg_hr_bpm": "other"},
            )
            conn.commit()
            rows = conn.execute(
                select(activity_metric).where(
                    activity_metric.c.activity_id == "self1",
                    activity_metric.c.metric_key.in_(
                        ("fit.session.avg_heart_rate", "strava.session.avg_heart_rate")
                    ),
                )
            ).fetchall()
        # Exactly the copied row survives -- not both, and not the stale fit.session.* one.
        assert len(rows) == 1
        assert rows[0].metric_key == "strava.session.avg_heart_rate"
        assert rows[0].value_num == pytest.approx(135.0)

    def test_collection_fields_swap_wholesale(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_pair(conn)
            merge_activities(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                self_id="self1",
                other_id="other1",
                field_choices={"route": "other", "laps": "other", "stream": "other"},
            )
            conn.commit()

            route_row = conn.execute(
                select(route_geom).where(route_geom.c.activity_id == "self1")
            ).fetchone()
            assert route_row is not None
            assert route_row.encoded_polyline == "other_polyline"

            laps = conn.execute(select(lap).where(lap.c.activity_id == "self1")).fetchall()
            assert len(laps) == 1
            assert laps[0].distance_m == pytest.approx(26863.1)

            stream_row = conn.execute(
                select(activity_stream).where(activity_stream.c.activity_id == "self1")
            ).fetchone()
        assert stream_row is not None
        assert stream_row.parquet_path == "other1.parquet"

    def test_moves_source_links_and_soft_deletes_other(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_pair(conn)
            merge_activities(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                self_id="self1",
                other_id="other1",
                field_choices={},
            )
            conn.commit()

            links = conn.execute(
                select(activity_source_link.c.activity_id, activity_source_link.c.source).where(
                    activity_source_link.c.athlete_id == DEFAULT_ATHLETE_ID
                )
            ).fetchall()
            assert {(link_row.activity_id, link_row.source) for link_row in links} == {
                ("self1", "fit_folder"),
                ("self1", "strava_export"),
            }

            other_row = conn.execute(
                select(activity.c.deleted_at).where(activity.c.id == "other1")
            ).fetchone()
        assert other_row is not None
        assert other_row.deleted_at is not None

    def test_rejects_merging_an_activity_with_itself(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_pair(conn)
            with pytest.raises(ValueError, match="cannot merge"):
                merge_activities(
                    conn,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    self_id="self1",
                    other_id="self1",
                    field_choices={},
                )

    def test_rejects_a_nonexistent_activity(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_pair(conn)
            with pytest.raises(ValueError, match="no activity"):
                merge_activities(
                    conn,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    self_id="self1",
                    other_id="doesnotexist",
                    field_choices={},
                )


class TestMergeActivitiesAndRecordSurvivesRebuild:
    def test_reapplies_after_a_simulated_rebuild(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_pair(conn)
            merge_activities_and_record(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                self_id="self1",
                other_id="other1",
                field_choices={"calories": "other"},
            )
            conn.commit()

            override_row = conn.execute(
                select(activity_merge_override).where(
                    activity_merge_override.c.athlete_id == DEFAULT_ATHLETE_ID
                )
            ).fetchone()
            assert override_row is not None
            assert override_row.keep_source == "fit_folder"
            assert override_row.absorbed_source == "strava_export"

            # "Rebuild": both activities come back fresh from raw bytes, re-split apart again --
            # same shape both sides had before the original merge.
            conn.execute(delete(activity_source_link))
            conn.execute(delete(activity_metric))
            conn.execute(delete(route_geom))
            conn.execute(delete(lap))
            conn.execute(delete(activity_stream))
            conn.execute(delete(activity))
            _seed_pair(conn)
            conn.commit()

            merged = apply_activity_merge_overrides(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            row = conn.execute(select(activity).where(activity.c.id == "self1")).fetchone()
            other_row = conn.execute(
                select(activity.c.deleted_at).where(activity.c.id == "other1")
            ).fetchone()
        assert merged == 1
        assert row is not None
        assert row.calories == pytest.approx(378.0)
        assert other_row is not None
        assert other_row.deleted_at is not None

    def test_skips_a_pair_already_merged_by_natural_matching(self, tmp_path: Path) -> None:
        """If a rebuild's own _find_merge_match already caught the duplicate (e.g. thanks to
        the hike/walk merge-family leniency), both anchors resolve to the same activity_id --
        apply_activity_merge_overrides must not error or double-apply in that case."""
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _seed_pair(conn)
            merge_activities_and_record(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                self_id="self1",
                other_id="other1",
                field_choices={},
            )
            conn.commit()

            # Simulate natural merge-matching already having produced one activity with both
            # source links, instead of two separate rows -- reusing the same raw_object rows
            # _seed_pair already created (real content-addressed archiving is idempotent on
            # sha256, so a fresh _add_activity call with the same source/external_id would
            # collide rather than genuinely represent this scenario).
            fit_raw_id = conn.execute(
                select(raw_object.c.id).where(
                    raw_object.c.athlete_id == DEFAULT_ATHLETE_ID,
                    raw_object.c.source == "fit_folder",
                )
            ).scalar_one()
            strava_raw_id = conn.execute(
                select(raw_object.c.id).where(
                    raw_object.c.athlete_id == DEFAULT_ATHLETE_ID,
                    raw_object.c.source == "strava_export",
                )
            ).scalar_one()
            conn.execute(delete(activity_source_link))
            conn.execute(delete(activity_metric))
            conn.execute(delete(route_geom))
            conn.execute(delete(lap))
            conn.execute(delete(activity_stream))
            conn.execute(delete(activity))
            now = dt.datetime.now(dt.UTC)
            conn.execute(
                activity.insert().values(
                    id="merged1",
                    athlete_id=DEFAULT_ATHLETE_ID,
                    start_time_utc=START,
                    utc_offset_s=0,
                    local_date=START.date().isoformat(),
                    sport="hiking",
                    sub_sport="generic",
                    duration_s=41913.0,
                    primary_source="fit_folder",
                    created_at=now,
                    updated_at=now,
                )
            )
            for source, external_id, raw_id in (
                ("fit_folder", "fit-ext-1", fit_raw_id),
                ("strava_export", "strava-ext-1", strava_raw_id),
            ):
                conn.execute(
                    activity_source_link.insert().values(
                        athlete_id=DEFAULT_ATHLETE_ID,
                        activity_id="merged1",
                        source=source,
                        external_id=external_id,
                        raw_object_id=raw_id,
                        ingested_at=now,
                    )
                )
            conn.commit()

            merged = apply_activity_merge_overrides(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert merged == 0
