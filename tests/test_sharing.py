"""Tests for sharing.py's pure functions -- create/resolve/revoke and the two HTML renderers.
See tests/api/test_share.py for the endpoint-level tests (auth boundaries, HTTP status codes).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlalchemy import Engine, select

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, athlete, day_rollup, metadata, share_link
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.sharing import (
    create_share_link,
    render_activity_share_html,
    render_period_share_html,
    resolve_share_token,
    revoke_share_link,
)

OTHER_ATHLETE_ID = "01JOTHERATHLETE00000000000"


def _seed_athlete(engine: Engine, athlete_id: str = DEFAULT_ATHLETE_ID) -> None:
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=athlete_id,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()


def _seed_activity(engine: Engine, *, activity_id: str = "act1") -> None:
    now = dt.datetime(2026, 6, 15, 10, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-15",
                sport="running",
                sub_sport=None,
                name="Morning run",
                is_race=None,
                duration_s=1800.0,
                moving_duration_s=1700.0,
                distance_m=5000.0,
                elevation_gain_m=50.0,
                calories=300.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()


def _engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    return engine


def test_create_share_link_returns_id_and_stores_only_a_hash(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    with engine.connect() as conn:
        new_id, token = create_share_link(
            conn, athlete_id=DEFAULT_ATHLETE_ID, target_type="activity", target_id="act1"
        )
        conn.commit()
        row = conn.execute(select(share_link).where(share_link.c.id == new_id)).one()

    assert isinstance(new_id, int)
    assert len(token) > 20
    assert row.token_hash != token  # never the plaintext
    assert row.revoked_at is None


def test_resolve_share_token_finds_a_valid_token(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    with engine.connect() as conn:
        _, token = create_share_link(
            conn, athlete_id=DEFAULT_ATHLETE_ID, target_type="activity", target_id="act1"
        )
        conn.commit()
        target = resolve_share_token(conn, token)

    assert target is not None
    assert target.athlete_id == DEFAULT_ATHLETE_ID
    assert target.target_type == "activity"
    assert target.target_id == "act1"


def test_resolve_share_token_rejects_an_unknown_token(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    with engine.connect() as conn:
        assert resolve_share_token(conn, "not-a-real-token") is None


def test_resolve_share_token_rejects_a_revoked_token(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    with engine.connect() as conn:
        new_id, token = create_share_link(
            conn, athlete_id=DEFAULT_ATHLETE_ID, target_type="activity", target_id="act1"
        )
        conn.commit()
        assert revoke_share_link(conn, athlete_id=DEFAULT_ATHLETE_ID, id=new_id) is True
        conn.commit()
        assert resolve_share_token(conn, token) is None


def test_revoke_share_link_is_scoped_to_the_owning_athlete(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_athlete(engine, athlete_id=OTHER_ATHLETE_ID)
    with engine.connect() as conn:
        new_id, _ = create_share_link(
            conn, athlete_id=DEFAULT_ATHLETE_ID, target_type="activity", target_id="act1"
        )
        conn.commit()
        assert revoke_share_link(conn, athlete_id=OTHER_ATHLETE_ID, id=new_id) is False


def test_revoke_share_link_is_idempotent_false_the_second_time(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    with engine.connect() as conn:
        new_id, _ = create_share_link(
            conn, athlete_id=DEFAULT_ATHLETE_ID, target_type="activity", target_id="act1"
        )
        conn.commit()
        assert revoke_share_link(conn, athlete_id=DEFAULT_ATHLETE_ID, id=new_id) is True
        conn.commit()
        assert revoke_share_link(conn, athlete_id=DEFAULT_ATHLETE_ID, id=new_id) is False


def test_render_activity_share_html_includes_name_and_stats(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    with engine.connect() as conn:
        html = render_activity_share_html(conn, "act1")

    assert "Morning run" in html
    assert "5.00 km" in html
    assert '<meta property="og:title" content="Morning run">' in html


def test_render_activity_share_html_unavailable_for_unknown_activity(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    with engine.connect() as conn:
        html = render_activity_share_html(conn, "does-not-exist")

    assert "no longer available" in html


def test_render_activity_share_html_escapes_the_name(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 15, 10, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="act1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-15",
                sport="running",
                name="<script>alert(1)</script>",
                duration_s=1800.0,
                moving_duration_s=1700.0,
                distance_m=5000.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()
        html = render_activity_share_html(conn, "act1")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_period_share_html_aggregates_day_rollup_for_a_month(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        for day, count, dist in (("2026-06-01", 1, 5000.0), ("2026-06-15", 2, 12000.0)):
            conn.execute(
                day_rollup.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    local_date=day,
                    activity_count=count,
                    activity_distance_m=dist,
                    activity_elevation_gain_m=100.0,
                    refreshed_at=now,
                )
            )
        # Outside the requested month -- must not be counted.
        conn.execute(
            day_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-07-01",
                activity_count=5,
                activity_distance_m=99999.0,
                refreshed_at=now,
            )
        )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "17.00 km" in html  # 5000 + 12000 m
    assert ">3<" in html  # activity_count 1 + 2
    assert ">2<" in html  # active_days: two days with activity_count > 0


def test_render_period_share_html_all_ignores_date_range(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        for day in ("2020-01-01", "2026-06-15"):
            conn.execute(
                day_rollup.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    local_date=day,
                    activity_count=1,
                    activity_distance_m=1000.0,
                    refreshed_at=now,
                )
            )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "all", None)

    assert "2.00 km" in html  # both days counted, spanning years
    assert "All time summary" in html


def test_render_period_share_html_rejects_an_invalid_period_type(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    with engine.connect() as conn:
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "decade", "2026")

    assert "no longer available" in html
