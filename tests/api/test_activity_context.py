"""Tests for GET /activities/{id}/context (Milestone E of Phase 6.1 -- see
docs/adr/0010-phase-6.1-frontend-design.md's plan). percentile_rank and the distance-band
comparison pool are cross-checked independently below (not just re-asserting whatever the
endpoint itself computes), matching the manual cross-check already run against the real
archive during development.
"""

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import activity_metric, metric_definition
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from tests.api.conftest import seed_activity


def _add_metric(engine: Engine, *, activity_id: str, metric_key: str, value: float) -> None:
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        conn.execute(
            metric_definition.insert().values(
                metric_key=metric_key,
                display_name=metric_key,
                category="activity",
                value_type="numeric",
                first_seen_at=now,
                first_seen_source="fit_folder",
            )
        )
        conn.execute(
            activity_metric.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=activity_id,
                metric_key=metric_key,
                value_num=value,
                source="fit_folder",
                created_at=now,
            )
        )
        conn.commit()


def test_context_404_for_unknown_activity(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get("/api/v1/activities/doesnotexist/context", headers=auth_headers)
    assert r.status_code == 404


def test_no_percentile_with_no_comparable_activities(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """A first-of-its-kind effort has nothing to rank against -- percentile_rank must be null,
    not 0 or 100 (AGENTS.md's "never invent a plausible-looking number")."""
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="running", distance_m=5000.0, duration_s=1500.0)

    r = client.get("/api/v1/activities/a1/context", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["percentile_rank"] is None
    assert body["comparable_count"] == 0
    # `recent` legitimately includes the activity itself -- it's part of its own 90-day
    # window, and a sparkline needs "this one" plotted among its contemporaries.
    assert [r["id"] for r in body["recent"]] == ["a1"]


def test_percentile_excludes_self_from_the_comparison_pool(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """With exactly one other comparable activity, comparable_count must be 1 (not 2) -- the
    target's own trivially-in-band distance must not count as a comparison against itself."""
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="a1",
            sport="running",
            distance_m=5000.0,
            duration_s=1500.0,
            moving_duration_s=1500.0,
        )
        seed_activity(
            conn,
            activity_id="a2",
            sport="running",
            local_date="2025-06-02",
            distance_m=5000.0,
            duration_s=1500.0,
            moving_duration_s=1500.0,
        )

    r = client.get("/api/v1/activities/a1/context", headers=auth_headers)
    body = r.json()
    assert body["comparable_count"] == 1
    # Identical pace -> "faster than or equal to" -> 100%.
    assert body["percentile_rank"] == 100.0


def test_percentile_rank_matches_independent_calculation(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """4 comparable 5Ks at distinct paces; the target beats 2 of them (slower-or-equal) ->
    2/4 = 50%. Computed by hand here rather than re-deriving the endpoint's own formula."""
    with engine.connect() as conn:
        # Target: 5000m in 1500s moving -> pace 0.3 s/m.
        seed_activity(
            conn,
            activity_id="target",
            sport="running",
            distance_m=5000.0,
            duration_s=1500.0,
            moving_duration_s=1500.0,
        )
        # Faster than target (lower pace): excluded from "slower or equal" count.
        seed_activity(
            conn,
            activity_id="faster1",
            sport="running",
            local_date="2025-06-02",
            distance_m=5000.0,
            duration_s=1200.0,
            moving_duration_s=1200.0,
        )
        seed_activity(
            conn,
            activity_id="faster2",
            sport="running",
            local_date="2025-06-03",
            distance_m=5000.0,
            duration_s=1300.0,
            moving_duration_s=1300.0,
        )
        # Slower than target: included in "slower or equal" count.
        seed_activity(
            conn,
            activity_id="slower1",
            sport="running",
            local_date="2025-06-04",
            distance_m=5000.0,
            duration_s=1600.0,
            moving_duration_s=1600.0,
        )
        seed_activity(
            conn,
            activity_id="slower2",
            sport="running",
            local_date="2025-06-05",
            distance_m=5000.0,
            duration_s=1800.0,
            moving_duration_s=1800.0,
        )

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    assert body["comparable_count"] == 4
    assert body["percentile_rank"] == 50.0


def test_distance_band_excludes_activities_outside_15_percent(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """A 10K is not a comparable effort for a 5K's percentile -- only activities within +/-15%
    of the target's own distance count."""
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="target",
            sport="running",
            distance_m=5000.0,
            duration_s=1500.0,
            moving_duration_s=1500.0,
        )
        # Within band: 5000 * 1.1 = 5500.
        seed_activity(
            conn,
            activity_id="in_band",
            sport="running",
            local_date="2025-06-02",
            distance_m=5500.0,
            duration_s=1600.0,
            moving_duration_s=1600.0,
        )
        # Outside band: a 10K.
        seed_activity(
            conn,
            activity_id="out_of_band",
            sport="running",
            local_date="2025-06-03",
            distance_m=10000.0,
            duration_s=3000.0,
            moving_duration_s=3000.0,
        )

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    assert body["comparable_count"] == 1


def test_distance_band_is_scoped_to_the_same_sport(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """A cycling activity at the same distance must not count as a comparable running effort."""
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="target",
            sport="running",
            distance_m=5000.0,
            duration_s=1500.0,
            moving_duration_s=1500.0,
        )
        seed_activity(
            conn,
            activity_id="bike",
            sport="cycling",
            local_date="2025-06-02",
            distance_m=5000.0,
            duration_s=900.0,
            moving_duration_s=900.0,
        )

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    assert body["comparable_count"] == 0
    assert body["percentile_rank"] is None


def test_recent_window_is_90_days_up_to_the_activity_own_date_not_today(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """Viewing an old activity should show its own contemporaries -- the 90-day window ends at
    that activity's local_date, not at "today"."""
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running", local_date="2025-06-01")
        # Within 90 days before the target's own date.
        seed_activity(conn, activity_id="within", sport="running", local_date="2025-04-01")
        # More than 90 days before the target's own date -- excluded.
        seed_activity(conn, activity_id="too_old", sport="running", local_date="2024-12-01")
        # After the target's own date -- excluded (this is "recent as of that day", not a
        # window centered on it).
        seed_activity(conn, activity_id="after", sport="running", local_date="2025-06-15")

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    recent_ids = {r["id"] for r in body["recent"]}
    assert recent_ids == {"target", "within"}


def test_fastest_rows_carry_avg_hr_bpm_via_the_same_alias_merge_as_the_activity_detail_endpoint(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="target",
            sport="running",
            distance_m=5000.0,
            duration_s=1500.0,
            moving_duration_s=1500.0,
        )
        seed_activity(
            conn,
            activity_id="strava_only",
            sport="running",
            local_date="2025-06-02",
            distance_m=5000.0,
            duration_s=1400.0,
            moving_duration_s=1400.0,
        )
    _add_metric(engine, activity_id="target", metric_key="fit.session.avg_heart_rate", value=142.0)
    # GPX/TCX-sourced Strava activity: no fit.session.* key, only the strava.session.* alias.
    _add_metric(
        engine, activity_id="strava_only", metric_key="strava.session.avg_heart_rate", value=138.0
    )

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    by_id = {row["id"]: row["avg_hr_bpm"] for row in body["fastest"]}
    assert by_id["target"] == 142.0
    assert by_id["strava_only"] == 138.0


def test_fastest_is_sorted_by_pace_ascending_and_includes_self(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """The same +/-15% distance-band pool percentile_rank draws on, re-sorted fastest-first --
    unlike `other_paces` used for the percentile math, `fastest` includes the target itself if
    it earns a spot."""
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="target",
            sport="running",
            distance_m=5000.0,
            duration_s=1500.0,
            moving_duration_s=1500.0,  # pace 0.30 s/m
        )
        seed_activity(
            conn,
            activity_id="faster",
            sport="running",
            local_date="2025-06-02",
            distance_m=5000.0,
            duration_s=1200.0,
            moving_duration_s=1200.0,  # pace 0.24 s/m
        )
        seed_activity(
            conn,
            activity_id="slower",
            sport="running",
            local_date="2025-06-03",
            distance_m=5000.0,
            duration_s=1800.0,
            moving_duration_s=1800.0,  # pace 0.36 s/m
        )

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    assert [row["id"] for row in body["fastest"]] == ["faster", "target", "slower"]


def test_fastest_is_capped_at_30(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="target",
            sport="running",
            distance_m=5000.0,
            duration_s=1500.0,
            moving_duration_s=1500.0,
        )
        for i in range(35):
            seed_activity(
                conn,
                activity_id=f"a{i}",
                sport="running",
                local_date=f"2025-07-{i % 28 + 1:02d}",
                distance_m=5000.0,
                duration_s=1000.0 + i,
                moving_duration_s=1000.0 + i,
            )

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    assert len(body["fastest"]) == 30


def test_fastest_is_scoped_to_the_same_distance_band_and_sport(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="target",
            sport="running",
            distance_m=5000.0,
            duration_s=1500.0,
            moving_duration_s=1500.0,
        )
        seed_activity(
            conn,
            activity_id="out_of_band",
            sport="running",
            local_date="2025-06-02",
            distance_m=10000.0,
            duration_s=2000.0,
            moving_duration_s=2000.0,
        )
        seed_activity(
            conn,
            activity_id="wrong_sport",
            sport="cycling",
            local_date="2025-06-03",
            distance_m=5000.0,
            duration_s=600.0,
            moving_duration_s=600.0,
        )

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    assert [row["id"] for row in body["fastest"]] == ["target"]


def test_fastest_is_scoped_to_the_same_whole_kilometre_bucket_not_the_wider_15pct_band(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """ "only the runs exactly between 26.00km and 26.99km for a 26km activity, nothing else" --
    narrower than the +/-15% band `percentile_rank` uses (whose ~22.3-30.2km window at this
    distance would otherwise let e.g. a 24km or 27.02km run sneak into the fastest-30 list)."""
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="target",
            sport="running",
            distance_m=26290.0,
            duration_s=9500.0,
            moving_duration_s=9500.0,
        )
        seed_activity(
            conn,
            activity_id="in_bucket_low_edge",
            sport="running",
            local_date="2025-06-02",
            distance_m=26000.0,
            duration_s=9000.0,
            moving_duration_s=9000.0,
        )
        seed_activity(
            conn,
            activity_id="in_bucket_high_edge",
            sport="running",
            local_date="2025-06-03",
            distance_m=26990.0,
            duration_s=9600.0,
            moving_duration_s=9600.0,
        )
        # Just below the bucket, but still comfortably within the +/-15% band -- must be excluded.
        seed_activity(
            conn,
            activity_id="just_under_bucket",
            sport="running",
            local_date="2025-06-04",
            distance_m=25920.0,
            duration_s=8900.0,
            moving_duration_s=8900.0,
        )
        # Just above the bucket, again within the +/-15% band -- must be excluded.
        seed_activity(
            conn,
            activity_id="just_over_bucket",
            sport="running",
            local_date="2025-06-05",
            distance_m=27020.0,
            duration_s=9700.0,
            moving_duration_s=9700.0,
        )
        # Well within the +/-15% band (24km, ~9% short of 26.29km) but a different km bucket.
        seed_activity(
            conn,
            activity_id="within_15pct_but_wrong_bucket",
            sport="running",
            local_date="2025-06-06",
            distance_m=24000.0,
            duration_s=8500.0,
            moving_duration_s=8500.0,
        )

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    assert {row["id"] for row in body["fastest"]} == {
        "target",
        "in_bucket_low_edge",
        "in_bucket_high_edge",
    }


def test_recent_is_ordered_chronologically(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="third", sport="running", local_date="2025-06-03")
        seed_activity(conn, activity_id="first", sport="running", local_date="2025-06-01")
        seed_activity(conn, activity_id="second", sport="running", local_date="2025-06-02")

    r = client.get("/api/v1/activities/third/context", headers=auth_headers)
    body = r.json()
    assert [r["id"] for r in body["recent"]] == ["first", "second", "third"]
