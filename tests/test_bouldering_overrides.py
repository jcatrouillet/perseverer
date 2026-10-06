"""Tests for bouldering_overrides.py: route-status corrections and manually-added routes, both
durable and rebuild-safe (see that module's own docstring for why -- sync rebuild wipes and
re-derives activity/split with fresh identities on every run)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, delete, select

from perseverer.bouldering_overrides import (
    add_manual_route,
    apply_bouldering_route_overrides,
    delete_manual_route,
    set_route_grade_override,
    set_route_status_override,
)
from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, athlete, metadata, split
from perseverer.db.seed import DEFAULT_ATHLETE_ID


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
    conn: Connection, *, activity_id: str, start_time_utc: dt.datetime = dt.datetime(2026, 8, 19)
) -> None:
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start_time_utc,
            utc_offset_s=0,
            local_date=start_time_utc.date().isoformat(),
            sport="rock_climbing",
            sub_sport="bouldering",
            duration_s=1800.0,
            primary_source="garmin_connect",
            created_at=start_time_utc,
            updated_at=start_time_utc,
        )
    )


def _add_split(
    conn: Connection,
    *,
    activity_id: str,
    split_index: int,
    split_type: str = "climb_active",
    grade: int | None = 2,
    result: str | None = "attempt",
) -> None:
    conn.execute(
        split.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            split_index=split_index,
            split_type=split_type,
            climb_grade=grade,
            climb_result=result,
            duration_s=60.0,
        )
    )


class TestSetRouteStatusOverride:
    def test_overrides_the_live_split_immediately(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(conn, activity_id="a1", split_index=0, result="attempt")
            conn.commit()

            set_route_status_override(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                split_index=0,
                result="completed",
            )
            conn.commit()

            row = conn.execute(select(split.c.climb_result).where(split.c.split_index == 0)).one()
        assert row.climb_result == "completed"

    def test_rejects_an_unrecognized_result_value(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(conn, activity_id="a1", split_index=0)
            conn.commit()

            with pytest.raises(ValueError, match="result must be one of"):
                set_route_status_override(
                    conn,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id="a1",
                    split_index=0,
                    result="sent",
                )

    def test_rejects_overriding_a_rest_split(self, tmp_path: Path) -> None:
        """A rest interval has no route status to correct."""
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(
                conn,
                activity_id="a1",
                split_index=1,
                split_type="climb_rest",
                grade=None,
                result=None,
            )
            conn.commit()

            with pytest.raises(ValueError, match="no climb_active route"):
                set_route_status_override(
                    conn,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id="a1",
                    split_index=1,
                    result="completed",
                )

    def test_rejects_a_nonexistent_split_index(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            conn.commit()

            with pytest.raises(ValueError, match="no climb_active route"):
                set_route_status_override(
                    conn,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id="a1",
                    split_index=99,
                    result="completed",
                )

    def test_re_fixing_an_already_corrected_route_replaces_the_prior_override(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(conn, activity_id="a1", split_index=0, result="attempt")
            conn.commit()

            set_route_status_override(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                split_index=0,
                result="completed",
            )
            conn.commit()
            set_route_status_override(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                split_index=0,
                result="attempt",
            )
            conn.commit()

            row = conn.execute(select(split.c.climb_result).where(split.c.split_index == 0)).one()
        assert row.climb_result == "attempt"

    def test_survives_a_simulated_rebuild(self, tmp_path: Path) -> None:
        """The real bug this whole module exists to prevent: a bare UPDATE would vanish the
        moment `split` gets wiped and re-derived from raw bytes -- simulate exactly that (delete
        + re-insert the FIT-derived split with its original, un-corrected result) and confirm
        apply_bouldering_route_overrides restores the correction."""
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(conn, activity_id="a1", split_index=0, result="attempt")
            conn.commit()

            set_route_status_override(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                split_index=0,
                result="completed",
            )
            conn.commit()

            # Simulate `sync rebuild`'s wipe-and-replay: the split table is cleared and
            # re-derived fresh from the FIT bytes alone, which never knew about the correction.
            conn.execute(delete(split))
            _add_split(conn, activity_id="a1", split_index=0, result="attempt")
            conn.commit()

            changed = apply_bouldering_route_overrides(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            row = conn.execute(select(split.c.climb_result).where(split.c.split_index == 0)).one()
        assert changed == 1
        assert row.climb_result == "completed"


class TestAddManualRoute:
    def test_inserts_a_live_split_row_immediately(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            conn.commit()

            split_index = add_manual_route(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                grade=3,
                result="attempt",
            )
            conn.commit()

            row = conn.execute(
                select(
                    split.c.split_type,
                    split.c.climb_grade,
                    split.c.climb_result,
                    split.c.duration_s,
                    split.c.is_manual,
                ).where(split.c.split_index == split_index)
            ).one()
        assert row.split_type == "climb_active"
        assert row.climb_grade == 3
        assert row.climb_result == "attempt"
        assert row.duration_s is None
        assert row.is_manual is True

    def test_continues_the_activitys_own_split_index_sequence(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(conn, activity_id="a1", split_index=0)
            _add_split(conn, activity_id="a1", split_index=1, split_type="climb_rest")
            conn.commit()

            split_index = add_manual_route(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                grade=1,
                result="completed",
            )
        assert split_index == 2

    def test_two_manual_routes_get_sequential_split_indexes(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            conn.commit()

            first = add_manual_route(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                grade=1,
                result="attempt",
            )
            conn.commit()
            second = add_manual_route(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                grade=2,
                result="completed",
            )
        assert second == first + 1

    def test_survives_a_simulated_rebuild(self, tmp_path: Path) -> None:
        """A manual route has no FIT bytes behind it at all -- confirms it's re-derived from its
        own durable record, not lost, when the split table is wiped and replayed."""
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            conn.commit()
            add_manual_route(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                grade=4,
                result="completed",
            )
            conn.commit()

            # Simulate `sync rebuild`: the manual route's own live split row is gone (it was
            # never in the raw archive to begin with).
            conn.execute(delete(split))
            conn.commit()

            changed = apply_bouldering_route_overrides(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            row = conn.execute(
                select(split.c.climb_grade, split.c.climb_result, split.c.is_manual)
            ).one()
        assert changed == 1
        assert (row.climb_grade, row.climb_result, row.is_manual) == (4, "completed", True)


class TestDeleteManualRoute:
    def test_removes_both_the_durable_record_and_the_live_split(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            conn.commit()
            split_index = add_manual_route(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                grade=2,
                result="attempt",
            )
            conn.commit()

            delete_manual_route(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                split_index=split_index,
            )
            conn.commit()

            row = conn.execute(select(split.c.id)).fetchone()
        assert row is None

        # Confirms the durable record is gone too -- a rebuild afterward must not resurrect it.
        with engine.connect() as conn:
            changed = apply_bouldering_route_overrides(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert changed == 0

    def test_refuses_to_delete_a_fit_derived_route(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(conn, activity_id="a1", split_index=0)
            conn.commit()

            with pytest.raises(ValueError, match="no manually-added route"):
                delete_manual_route(
                    conn,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id="a1",
                    split_index=0,
                )

            # Never touched -- still there after the rejected delete.
            row = conn.execute(select(split.c.id)).fetchone()
        assert row is not None

    def test_refuses_a_nonexistent_split_index(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            conn.commit()

            with pytest.raises(ValueError, match="no manually-added route"):
                delete_manual_route(
                    conn,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id="a1",
                    split_index=0,
                )


class TestSetRouteGradeOverride:
    def test_overrides_the_live_split_immediately_for_a_fit_derived_route(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(conn, activity_id="a1", split_index=0, grade=2)
            conn.commit()

            set_route_grade_override(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                split_index=0,
                grade=5,
            )
            conn.commit()

            row = conn.execute(select(split.c.climb_grade)).one()
        assert row.climb_grade == 5

    def test_survives_a_simulated_rebuild_for_a_fit_derived_route(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(conn, activity_id="a1", split_index=0, grade=2)
            conn.commit()
            set_route_grade_override(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                split_index=0,
                grade=5,
            )
            conn.commit()

            # "Rebuild": wipe and re-derive the split fresh from raw bytes (grade back to what
            # the FIT file itself says), then re-apply the durable correction.
            conn.execute(delete(split))
            _add_split(conn, activity_id="a1", split_index=0, grade=2)
            conn.commit()

            changed = apply_bouldering_route_overrides(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            row = conn.execute(select(split.c.climb_grade)).one()
        assert changed == 1
        assert row.climb_grade == 5

    def test_grade_and_status_corrections_on_the_same_route_dont_clobber_each_other(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(conn, activity_id="a1", split_index=0, grade=2, result="attempt")
            conn.commit()

            set_route_status_override(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                split_index=0,
                result="completed",
            )
            conn.commit()
            set_route_grade_override(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                split_index=0,
                grade=5,
            )
            conn.commit()

            row = conn.execute(select(split.c.climb_grade, split.c.climb_result)).one()
        assert (row.climb_grade, row.climb_result) == (5, "completed")

        # Both corrections -- recorded independently on the same override row -- survive a
        # rebuild together.
        with engine.connect() as conn:
            conn.execute(delete(split))
            _add_split(conn, activity_id="a1", split_index=0, grade=2, result="attempt")
            conn.commit()
            apply_bouldering_route_overrides(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()
            row = conn.execute(select(split.c.climb_grade, split.c.climb_result)).one()
        assert (row.climb_grade, row.climb_result) == (5, "completed")

    def test_updates_bouldering_manual_route_for_a_manually_added_route(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            conn.commit()
            split_index = add_manual_route(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                grade=2,
                result="attempt",
            )
            conn.commit()

            set_route_grade_override(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                split_index=split_index,
                grade=7,
            )
            conn.commit()

            row = conn.execute(select(split.c.climb_grade, split.c.climb_result)).one()
        assert (row.climb_grade, row.climb_result) == (7, "attempt")

    def test_manual_route_grade_correction_survives_a_simulated_rebuild(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            conn.commit()
            split_index = add_manual_route(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                grade=2,
                result="attempt",
            )
            conn.commit()
            set_route_grade_override(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                split_index=split_index,
                grade=7,
            )
            conn.commit()

            # "Rebuild": wipe the live split entirely (a manual route has no FIT bytes to
            # re-derive from -- apply_bouldering_route_overrides must recreate it from scratch).
            conn.execute(delete(split))
            conn.commit()

            changed = apply_bouldering_route_overrides(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            row = conn.execute(select(split.c.climb_grade, split.c.climb_result)).one()
        assert changed == 1
        assert (row.climb_grade, row.climb_result) == (7, "attempt")

    def test_rejects_a_negative_grade(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(conn, activity_id="a1", split_index=0)
            conn.commit()

            with pytest.raises(ValueError, match="non-negative"):
                set_route_grade_override(
                    conn,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id="a1",
                    split_index=0,
                    grade=-1,
                )

    def test_404s_for_a_rest_split(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="a1")
            _add_split(conn, activity_id="a1", split_index=0, split_type="climb_rest", grade=None)
            conn.commit()

            with pytest.raises(ValueError, match="no climb_active route"):
                set_route_grade_override(
                    conn,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id="a1",
                    split_index=0,
                    grade=3,
                )
