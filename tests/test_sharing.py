"""Tests for sharing.py's pure functions -- create/resolve/revoke and the two HTML renderers.
See tests/api/test_share.py for the endpoint-level tests (auth boundaries, HTTP status codes).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import Connection, Engine, select

from perseverer.config import Settings
from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    activity_metric,
    activity_stream,
    activity_workout_step,
    athlete,
    day_rollup,
    fitness_daily_rollup,
    lap,
    metadata,
    metric_definition,
    period_rollup,
    route_geom,
    share_link,
)
from perseverer.db.schema import split as split_table
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import StreamPoint
from perseverer.sharing import (
    create_share_link,
    render_activity_share_html,
    render_period_share_html,
    resolve_share_token,
    revoke_share_link,
)
from perseverer.streams import write_activity_stream

OTHER_ATHLETE_ID = "01JOTHERATHLETE00000000000"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


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


def _add_metric(
    conn: Connection, *, activity_id: str, metric_key: str, value: float, source: str
) -> None:
    # activity_metric.metric_key FKs to metric_definition -- real ingestion auto-registers this
    # via metrics/registry.py before ever writing a value; tests have to do the same.
    now = dt.datetime.now(dt.UTC)
    exists = conn.execute(
        metric_definition.select().where(metric_definition.c.metric_key == metric_key)
    ).fetchone()
    if exists is None:
        conn.execute(
            metric_definition.insert().values(
                metric_key=metric_key,
                display_name=metric_key,
                category="activity",
                value_type="numeric",
                first_seen_at=now,
                first_seen_source=source,
            )
        )
    conn.execute(
        activity_metric.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            metric_key=metric_key,
            value_num=value,
            source=source,
            created_at=now,
        )
    )


def _write_stream(
    engine: Engine,
    settings: Settings,
    *,
    activity_id: str,
    base: dt.datetime,
    values_over_time: list[dict[str, float]],
) -> None:
    points = [
        StreamPoint(timestamp_utc=base + dt.timedelta(seconds=i), values=values)
        for i, values in enumerate(values_over_time)
    ]
    rel_path, n_samples, channels = write_activity_stream(
        settings.parquet_dir, DEFAULT_ATHLETE_ID, activity_id, points
    )
    with engine.connect() as conn:
        conn.execute(
            activity_stream.insert().values(
                activity_id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                parquet_path=rel_path,
                n_samples=n_samples,
                channels=json.dumps(channels),
                sample_rate_hint=1.0,
            )
        )
        conn.commit()


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
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "Morning run" in html
    assert "5.00 km" in html
    assert '<meta property="og:title" content="Morning run">' in html


def test_render_activity_share_html_unavailable_for_unknown_activity(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    with engine.connect() as conn:
        html = render_activity_share_html(conn, _settings(tmp_path), "does-not-exist")

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
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_activity_share_html_includes_a_laps_table(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    now = dt.datetime(2026, 6, 15, 10, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            lap.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="act1",
                lap_index=0,
                start_time_utc=now,
                duration_s=300.0,
                moving_duration_s=300.0,
                distance_m=1000.0,
                avg_hr=150.0,
                max_hr=160.0,
                avg_speed_mps=3.33,
            )
        )
        conn.commit()
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "Intervals" in html
    assert "150" in html  # avg HR
    assert "intervals-table" not in html  # not the frontend's own CSS class, just a sanity check


def test_render_activity_share_html_omits_laps_table_for_hiking(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 15, 10, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="hike1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-15",
                sport="hiking",
                distance_m=8000.0,
                duration_s=7200.0,
                moving_duration_s=7000.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            lap.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="hike1",
                lap_index=0,
                start_time_utc=now,
                duration_s=7000.0,
                distance_m=8000.0,
            )
        )
        conn.commit()
        html = render_activity_share_html(conn, _settings(tmp_path), "hike1")

    assert "Intervals" not in html


def test_render_activity_share_html_includes_route_map_when_route_exists(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    with engine.connect() as conn:
        conn.execute(
            route_geom.insert().values(
                activity_id="act1",
                athlete_id=DEFAULT_ATHLETE_ID,
                encoded_polyline="_p~iF~ps|U_ulLnnqC_mqNvxq`@",
                min_lat=37.0,
                min_lng=-122.5,
                max_lat=37.1,
                max_lng=-122.4,
                start_lat=37.0,
                start_lng=-122.5,
                end_lat=37.1,
                end_lng=-122.4,
            )
        )
        conn.commit()
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "new Map(" in html
    assert "maplibre-gl.mjs" in html
    assert "route-map" in html
    assert "Route" in html


def test_render_activity_share_html_no_route_section_without_a_route(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    with engine.connect() as conn:
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "maplibre" not in html


def test_render_activity_share_html_includes_bouldering_routes_table(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 15, 10, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="climb1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-15",
                sport="rock_climbing",
                sub_sport="bouldering",
                duration_s=3600.0,
                calories=400.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            split_table.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="climb1",
                split_index=0,
                split_type="climb_active",
                duration_s=120.0,
                climb_grade=4,
                climb_result="completed",
                climb_avg_hr=140.0,
            )
        )
        conn.execute(
            split_table.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="climb1",
                split_index=1,
                split_type="climb_active",
                duration_s=90.0,
                climb_grade=5,
                climb_result="attempt",
                climb_avg_hr=145.0,
            )
        )
        conn.commit()
        html = render_activity_share_html(conn, _settings(tmp_path), "climb1")

    assert "Time &amp; calories" in html
    assert "Routes by grade" in html
    assert "V4" in html
    assert "V5" in html
    assert "Completed" in html
    assert "Attempt" in html
    assert "Intervals" not in html


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


def test_render_period_share_html_includes_a_running_breakdown(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 1, 8, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="run1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-01",
                sport="running",
                distance_m=10000.0,
                moving_duration_s=3000.0,  # 5:00/km
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            activity.insert().values(
                id="run2",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-15",
                sport="running",
                distance_m=20000.0,
                moving_duration_s=7200.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        # A different (but sport_family-equivalent) sport, and a wholly unrelated one -- neither
        # must be counted: MonthView/YearView's own `useActivities({sport: "running"})` call does
        # a plain `sport == "running"` match server-side, not a sport_family() grouping, confirmed
        # against GET /activities's own query -- trail_running is a real, deliberate exclusion.
        conn.execute(
            activity.insert().values(
                id="trail1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-12",
                sport="trail_running",
                distance_m=99000.0,
                moving_duration_s=99000.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            activity.insert().values(
                id="ride1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-10",
                sport="cycling",
                distance_m=40000.0,
                moving_duration_s=3600.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "Running" in html
    assert "30 km" in html  # 10km + 20km run distance, cycling + trail_running excluded
    assert ">2<" in html  # two runs, not three
    assert "20.00 km" in html  # longest run


def test_render_period_share_html_includes_a_monthly_distance_chart_for_a_year(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        for period_start, dist in (("2026-01", 50000.0), ("2026-02", 80000.0)):
            conn.execute(
                period_rollup.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    period_type="month",
                    period_start=period_start,
                    period_end=f"{period_start}-28",
                    activity_count=4,
                    activity_distance_m=dist,
                    refreshed_at=now,
                )
            )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "year", "2026")

    assert "Distance by month" in html
    assert "<svg" in html
    assert ">Jan<" in html
    assert ">Feb<" in html


def test_render_period_share_html_includes_a_fitness_form_chart(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        for day, ctl, atl in (("2026-06-01", 40.0, 35.0), ("2026-06-15", 45.0, 50.0)):
            conn.execute(
                fitness_daily_rollup.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    local_date=day,
                    training_load=100.0,
                    ctl=ctl,
                    atl=atl,
                    tsb=ctl - atl,
                    refreshed_at=now,
                )
            )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "Fitness &amp; Form" in html
    assert "Fitness (CTL)" in html
    assert "Fatigue (ATL)" in html
    assert "<svg" in html


def test_render_period_share_html_omits_charts_with_no_data(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    with engine.connect() as conn:
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "week", "2026-06-01")

    assert "<h2>Running</h2>" not in html
    assert "Fitness &amp; Form" not in html
    assert "Distance by month" not in html


def test_render_period_share_html_includes_a_hiking_breakdown(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 1, 8, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="hike1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-01",
                sport="hiking",
                distance_m=12000.0,
                moving_duration_s=10800.0,
                elevation_gain_m=600.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "Hiking" in html
    assert "600 m on 2026-06-01" in html


def test_render_period_share_html_includes_a_climbing_breakdown(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 1, 8, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="climb1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-01",
                sport="rock_climbing",
                sub_sport="bouldering",
                duration_s=3600.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            split_table.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="climb1",
                split_index=0,
                split_type="climb_active",
                duration_s=120.0,
                climb_grade=6,
                climb_result="completed",
            )
        )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "Climbing" in html
    assert "V6" in html


def test_render_period_share_html_includes_a_fitness_training_breakdown(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 1, 8, 0, 0)
    with engine.connect() as conn:
        for sport, activity_id in (("hiit", "hiit1"), ("strength_training", "strength1")):
            conn.execute(
                activity.insert().values(
                    id=activity_id,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    start_time_utc=now,
                    utc_offset_s=0,
                    local_date="2026-06-01",
                    sport=sport,
                    duration_s=1800.0,
                    moving_duration_s=1800.0,
                    primary_source="fit_folder",
                    created_at=now,
                    updated_at=now,
                )
            )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "Fitness" in html
    assert ">2<" in html  # two sessions (hiit + strength_training)


def test_render_period_share_html_omits_sport_buckets_with_no_matching_activity(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    with engine.connect() as conn:
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "Hiking" not in html
    assert "Climbing" not in html
    assert "Fitness</h2>" not in html


def test_render_period_share_html_month_has_icon_chips_and_extra_stats(tmp_path: Path) -> None:
    """The top-level stat grid, for Month/Year/All-time, now mirrors PeriodStatsCard.tsx's own
    richer set (streak/busiest-week/favorite-day/averages), not just the original six totals --
    plus every tile carries an icon-chip + tone, matching the activity share page's own
    icon/tone treatment."""
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 1, 8, 0, 0)
    with engine.connect() as conn:
        # Three runs on three different Mondays -- enough to give "Favorite day"/"Busiest week"/
        # "Longest streak" real, non-trivial values rather than degenerate single-activity ones.
        for i, day in enumerate(("2026-06-01", "2026-06-08", "2026-06-15")):
            activity_id = f"run{i}"
            conn.execute(
                activity.insert().values(
                    id=activity_id,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    start_time_utc=now,
                    utc_offset_s=0,
                    local_date=day,
                    sport="running",
                    distance_m=10000.0,
                    moving_duration_s=3000.0,
                    primary_source="fit_folder",
                    created_at=now,
                    updated_at=now,
                )
            )
            _add_metric(
                conn,
                activity_id=activity_id,
                metric_key="fit.session.avg_heart_rate",
                value=140.0 + i,
                source="fit_folder",
            )
            _add_metric(
                conn,
                activity_id=activity_id,
                metric_key="fit.session.max_heart_rate",
                value=160.0 + i,
                source="fit_folder",
            )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "Longest streak" in html
    assert "Busiest week" in html
    assert "Week of Jun 1" in html
    assert "Favorite day" in html
    assert "Mon" in html
    assert "Average distance" in html
    assert "Average heart rate" in html
    assert "141 bpm" in html  # mean of 140/141/142
    assert "Max heart rate" in html
    assert "162 bpm" in html  # max of 160/161/162
    assert 'class="icon-chip"' in html
    assert "tone-pace" in html


def test_render_period_share_html_week_keeps_the_simpler_stat_set(tmp_path: Path) -> None:
    """WeekView.tsx has its own, materially different "Week stats" card -- no streak/busiest/
    favorite-day/averages concept for a single week -- so a week share must not invent them."""
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 1, 8, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="run1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-01",
                sport="running",
                distance_m=10000.0,
                moving_duration_s=3000.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "week", "2026-06-01")

    assert "Longest streak" not in html
    assert "Busiest" not in html
    assert "Favorite day" not in html
    assert "Average distance" not in html
    assert 'class="icon-chip"' in html  # the simpler set is still icon/tone-treated


def test_render_period_share_html_includes_activities_by_type_breakdown(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 1, 8, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="run1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-01",
                sport="running",
                distance_m=10000.0,
                moving_duration_s=3000.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            activity.insert().values(
                id="ride1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-10",
                sport="cycling",
                distance_m=40000.0,
                moving_duration_s=3600.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "Activities by type" in html
    assert "type-breakdown__row" in html
    assert "type-breakdown__fill" in html
    assert "Running" in html
    assert "Cycling" in html
    assert "icon--filled" in html  # the Phosphor sport pictograms, not the hand-rolled glyphs


def test_render_period_share_html_month_shows_running_charts_and_heatmap(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 1, 8, 0, 0)
    with engine.connect() as conn:
        for day in ("2026-06-01", "2026-06-08", "2026-06-15"):
            conn.execute(
                activity.insert().values(
                    id=f"run-{day}",
                    athlete_id=DEFAULT_ATHLETE_ID,
                    start_time_utc=now,
                    utc_offset_s=0,
                    local_date=day,
                    sport="running",
                    distance_m=8000.0,
                    moving_duration_s=2400.0,
                    duration_s=2400.0,
                    primary_source="fit_folder",
                    created_at=now,
                    updated_at=now,
                )
            )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "Distance per day" in html
    assert "Trailing 7-day kilometers" in html
    assert "Daily distance" in html
    assert 'class="running-heatmap__strip"' in html
    assert 'style="--day-count:30"' in html  # June has 30 days
    assert "running-heatmap__legend-item" in html  # the always-shown gradient/pie legend swatches


def test_render_period_share_html_year_shows_month_bucket_and_month_tiles(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 3, 1, 8, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="run1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-03-15",
                sport="running",
                distance_m=8000.0,
                moving_duration_s=2400.0,
                duration_s=2400.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        for day, count, dist, moving_s in (
            ("2026-03-01", 4, 32000.0, 14400.0),
            ("2026-07-01", 2, 16000.0, 7200.0),
        ):
            conn.execute(
                period_rollup.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    period_type="month",
                    period_start=day,
                    period_end=day,
                    activity_count=count,
                    activity_distance_m=dist,
                    activity_moving_duration_s=moving_s,
                    activity_days_count=count,
                    refreshed_at=now,
                )
            )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "year", "2026")

    assert "Distance per month" in html
    assert "Trailing 90-day kilometers" in html
    assert 'class="running-heatmap__grid"' in html  # the week-grid layout, not the daily strip
    assert 'class="running-heatmap__strip"' not in html
    # The month-tile grid: March and July have real rollup data, every other month is empty.
    assert 'class="stat-grid month-tile-grid"' in html
    assert "March" in html
    assert "32.0 km" in html
    assert "4.0h" in html
    assert "July" in html
    assert "16.0 km" in html
    assert "No activity" in html  # every other month


def test_render_period_share_html_personal_records_and_new_pr_badge(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 1, 8, 0, 0)
    with engine.connect() as conn:
        # A slower 5k earlier in the athlete's history -- the June one below must outrun it to
        # earn the "all-time PR" badge.
        conn.execute(
            activity.insert().values(
                id="old5k",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=dt.datetime(2026, 1, 1, 8, 0, 0),
                utc_offset_s=0,
                local_date="2026-01-01",
                sport="running",
                distance_m=5000.0,
                moving_duration_s=1800.0,  # 6:00/km
                duration_s=1800.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            activity.insert().values(
                id="new5k",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-15",
                sport="running",
                distance_m=5000.0,
                moving_duration_s=1500.0,  # 5:00/km -- a real all-time PR
                duration_s=1500.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "Personal records" in html
    assert "fastest whole recorded run near each distance" in html
    assert "running-records__table" in html
    assert "5 km" in html
    assert "5:00 /km" in html
    assert "1 all-time PR set this period: 5 km" in html
    assert "running-records__pr-badge" in html


def test_render_period_share_html_featured_hikes(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 1, 8, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="hike-long",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-01",
                sport="hiking",
                name="Ridge Trail",
                distance_m=18000.0,
                moving_duration_s=14400.0,
                elevation_gain_m=200.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            activity.insert().values(
                id="hike-high-gain",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-10",
                sport="hiking",
                name="Hike",  # the generic device default -- must fall back to "Hike" verbatim
                distance_m=6000.0,
                moving_duration_s=7200.0,
                elevation_gain_m=900.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        # A separate, lower-gain hike that reaches the highest absolute point -- must be its own
        # featured card, distinct from "Highest elevation gain" above (a hike already featured
        # under one label is never featured again under another, same dedup HikeStatsCard.tsx's
        # own pickFeaturedHikes uses).
        conn.execute(
            activity.insert().values(
                id="hike-high-point",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-20",
                sport="hiking",
                distance_m=4000.0,
                moving_duration_s=5400.0,
                elevation_gain_m=100.0,
                max_altitude_m=3200.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "month", "2026-06")

    assert "hike-featured-card" in html
    assert "Longest hike" in html
    assert "Ridge Trail" in html
    assert "Highest elevation gain" in html
    assert "Highest point reached" in html
    assert "3200 m peak" in html


def test_render_period_share_html_week_has_no_running_charts_or_records(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 1, 8, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="run1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-01",
                sport="running",
                distance_m=5000.0,
                moving_duration_s=1500.0,
                duration_s=1500.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()
        html = render_period_share_html(conn, DEFAULT_ATHLETE_ID, "week", "2026-06-01")

    assert "<h2>Running</h2>" in html
    # Only-conditionally-emitted markers, not the bare class names -- those are always present in
    # the page's own <style> block regardless of whether the section itself rendered.
    assert '<div class="running-heatmap"' not in html
    assert '<div class="running-stats__charts"' not in html
    assert "Personal records" not in html


# --- New activity-share features: units + hover data, weather, time in zone, interval overlay,
# route playback, and theme colors -----------------------------------------------------------


def test_render_activity_share_html_uses_real_theme_color_tokens(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    with engine.connect() as conn:
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "--color-heart-rate: #ef5a6f" in html
    assert "--color-pace: #4da3ff" in html
    assert "prefers-color-scheme: light" in html
    assert "#f5f5f5" not in html  # the old flat gray palette is gone


def test_render_activity_share_html_stats_use_icon_chips_and_tone_colors(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    with engine.connect() as conn:
        _add_metric(
            conn,
            activity_id="act1",
            metric_key="fit.session.total_training_effect",
            value=3.5,
            source="fit_folder",
        )
        _add_metric(
            conn,
            activity_id="act1",
            metric_key="fit.session.avg_power",
            value=210.0,
            source="fit_folder",
        )
        _add_metric(
            conn,
            activity_id="act1",
            metric_key="fit.session.avg_temperature",
            value=18.0,
            source="fit_folder",
        )
        _add_metric(
            conn,
            activity_id="act1",
            metric_key="fit.session.enhanced_avg_respiration_rate",
            value=32.0,
            source="fit_folder",
        )
        conn.commit()
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    # Distance & time and heart-rate-less basic sections always carry icon chips now.
    assert 'class="icon-chip"' in html
    assert "tone-pace" in html
    assert "tone-elevation" in html
    assert "tone-cadence" in html
    assert "tone-load" in html
    # The five sections converted last (training effect / power / temperature / respiration).
    assert "tone-power" in html
    assert "Aerobic effect" in html
    assert "Avg power" in html
    assert "Avg temperature" in html
    assert "Avg respiration" in html


def test_render_activity_share_html_charts_embed_units_and_elapsed_time(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    settings = _settings(tmp_path)
    base = dt.datetime(2026, 6, 15, 10, 0, 0)
    _write_stream(
        engine,
        settings,
        activity_id="act1",
        base=base,
        values_over_time=[{"heart_rate": 120.0 + i, "altitude_m": 50.0 + i} for i in range(300)],
    )

    with engine.connect() as conn:
        html = render_activity_share_html(conn, settings, "act1")

    assert "Charts" in html
    assert "ichart-tooltip" in html
    assert "ichart-cursor" in html
    assert '"unit": " bpm"' in html or '"unit":" bpm"' in html.replace(", ", ",")
    assert "_CHART_HOVER_SCRIPT" not in html  # sanity: the constant name itself never leaks
    assert "elapsed_s" in html


def test_render_activity_share_html_no_hover_script_without_a_stream(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    with engine.connect() as conn:
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "ichart-data" not in html
    assert "_CHART_HOVER_SCRIPT" not in html
    assert "addEventListener" not in html


def test_render_activity_share_html_shows_cached_weather(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    with engine.connect() as conn:
        for key, value in (
            ("weather.open_meteo.temperature_min_c", 12.0),
            ("weather.open_meteo.temperature_max_c", 12.0),
            ("weather.open_meteo.humidity_min_pct", 60.0),
            ("weather.open_meteo.humidity_max_pct", 60.0),
            ("weather.open_meteo.weather_code", 1.0),
            ("weather.open_meteo.wind_speed_mps", 3.0),
        ):
            _add_metric(conn, activity_id="act1", metric_key=key, value=value, source="open-meteo")
        conn.commit()
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "Mainly clear" in html
    assert "12°C" in html
    assert "60% RH" in html
    assert "Wind 3" in html


def test_render_activity_share_html_omits_weather_when_not_cached(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    with engine.connect() as conn:
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert '<div class="weather-row">' not in html


def test_sharing_module_never_imports_the_live_weather_fetch() -> None:
    """The share page must only ever read cached weather -- confirms no network-capable fetch
    function from weather.py is imported/reachable from this module at all."""
    import perseverer.sharing as sharing_module

    assert not hasattr(sharing_module, "get_or_fetch_activity_weather")


def test_render_activity_share_html_shows_time_in_zone_for_running(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    with engine.connect() as conn:
        zone_data = {
            "fit.time_in_zone.time_in_hr_zone_0": 60.0,
            "fit.time_in_zone.time_in_hr_zone_1": 300.0,
            "fit.time_in_zone.time_in_hr_zone_2": 900.0,
            "fit.time_in_zone.hr_zone_high_boundary_0": 120.0,
            "fit.time_in_zone.hr_zone_high_boundary_1": 140.0,
        }
        for key, value in zone_data.items():
            _add_metric(conn, activity_id="act1", metric_key=key, value=value, source="fit")
        conn.commit()
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "Time in zone" in html
    assert "Z1" in html
    assert "Z2" in html
    assert "&lt; 120" in html  # zone 0's own range label
    # Regression guard: `.time-in-zone__fill` is a bare <span>, inline by default, which ignores
    # a percentage `width` entirely (confirmed live: renders as a 0-width box despite the correct
    # value in its inline style) -- the exact bug TimeInZoneChart.tsx's own CSS already
    # documents and fixes the same way for the authenticated app's version of this chart.
    assert "display: block" in html.split(".time-in-zone__fill")[1].split("}")[0]


def test_render_activity_share_html_omits_time_in_zone_for_bouldering(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    now = dt.datetime(2026, 6, 15, 10, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id="climb1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-06-15",
                sport="rock_climbing",
                sub_sport="bouldering",
                duration_s=1800.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        _add_metric(
            conn,
            activity_id="climb1",
            metric_key="fit.time_in_zone.time_in_hr_zone_1",
            value=300.0,
            source="fit",
        )
        conn.commit()
        html = render_activity_share_html(conn, _settings(tmp_path), "climb1")

    assert "Time in zone" not in html


def test_render_activity_share_html_intervals_include_expected_columns_from_workout(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    now = dt.datetime(2026, 6, 15, 10, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            lap.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="act1",
                lap_index=0,
                start_time_utc=now,
                duration_s=300.0,
                moving_duration_s=300.0,
                distance_m=1000.0,
                avg_hr=150.0,
                max_hr=160.0,
            )
        )
        conn.execute(
            activity_workout_step.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="act1",
                step_index=0,
                duration_type="distance",
                duration_distance_m=1000.0,
                target_type="speed",
                target_low_mps=3.0,
                target_high_mps=3.5,
                intensity="active",
            )
        )
        conn.commit()
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "Interval" in html
    assert "Exp. pace or dist." in html
    assert "Expected pace" in html
    assert "Active" in html
    assert "1km" in html


def test_render_activity_share_html_no_expected_columns_without_a_workout(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    now = dt.datetime(2026, 6, 15, 10, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            lap.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="act1",
                lap_index=0,
                start_time_utc=now,
                duration_s=300.0,
                distance_m=1000.0,
            )
        )
        conn.commit()
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "Exp. pace or dist." not in html
    assert "Expected pace" not in html


def test_render_activity_share_html_route_map_includes_playback_when_gps_stream_exists(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    settings = _settings(tmp_path)
    base = dt.datetime(2026, 6, 15, 10, 0, 0)
    with engine.connect() as conn:
        conn.execute(
            route_geom.insert().values(
                activity_id="act1",
                athlete_id=DEFAULT_ATHLETE_ID,
                encoded_polyline="_p~iF~ps|U_ulLnnqC_mqNvxq`@",
                min_lat=37.0,
                min_lng=-122.5,
                max_lat=37.1,
                max_lng=-122.4,
                start_lat=37.0,
                start_lng=-122.5,
                end_lat=37.1,
                end_lng=-122.4,
            )
        )
        conn.commit()
    _write_stream(
        engine,
        settings,
        activity_id="act1",
        base=base,
        values_over_time=[{"lat": 37.0 + i * 0.001, "lon": -122.5 + i * 0.001} for i in range(50)],
    )

    with engine.connect() as conn:
        html = render_activity_share_html(conn, settings, "act1")

    assert "route-playback" in html
    assert "route-playback-toggle" in html
    assert "playbackCoords" in html


def test_render_activity_share_html_route_map_no_playback_without_gps_stream(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _seed_athlete(engine)
    _seed_activity(engine)
    with engine.connect() as conn:
        conn.execute(
            route_geom.insert().values(
                activity_id="act1",
                athlete_id=DEFAULT_ATHLETE_ID,
                encoded_polyline="_p~iF~ps|U_ulLnnqC_mqNvxq`@",
                min_lat=37.0,
                min_lng=-122.5,
                max_lat=37.1,
                max_lng=-122.4,
                start_lat=37.0,
                start_lng=-122.5,
                end_lat=37.1,
                end_lng=-122.4,
            )
        )
        conn.commit()
        html = render_activity_share_html(conn, _settings(tmp_path), "act1")

    assert "route-playback-toggle" not in html
    assert "playbackCoords" not in html
