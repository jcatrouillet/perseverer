"""Tests for MCP tools (api/mcp_server.py). Each tool is a plain async function tested by
direct call, not through the MCP wire protocol -- that's the SDK's own responsibility to get
right, not something this project needs to re-test. Reuses tests/api/conftest.py's
client/engine/duckdb_con/seed_activity fixtures unchanged -- see
docs/adr/0007-phase-4-mcp-server.md decision 6 for why that works.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.api.mcp_server import (
    complete_planned_workout,
    create_blood_test_panel,
    create_blood_test_result,
    create_bouldering_goal,
    create_note,
    create_planned_race,
    create_planned_workout,
    create_recurring_planned_workout,
    create_shoe,
    delete_blood_test_panel,
    delete_blood_test_result,
    delete_bouldering_goal,
    delete_goal,
    delete_note,
    delete_planned_race,
    delete_planned_workout,
    get_activity,
    get_activity_climb_comparisons,
    get_activity_comparisons,
    get_activity_context,
    get_activity_insights,
    get_activity_location,
    get_activity_sources,
    get_activity_stream,
    get_activity_weather,
    get_activity_workout,
    get_bouldering_goals,
    get_calendar,
    get_calendar_months,
    get_calendar_weeks,
    get_climbing_summary,
    get_fitness,
    get_gear_alerts,
    get_goal_progress,
    get_health_dashboard,
    get_health_stream,
    get_last_activity_forecast,
    get_pace_bands,
    get_pace_hr_zones,
    get_performance,
    get_performance_curve,
    get_planned_workouts_for_date,
    get_race_readiness,
    get_vo2max_analysis,
    get_weather_forecast,
    list_activities,
    list_activity_years,
    list_blood_tests,
    list_health_observations,
    list_insights,
    list_notes,
    list_planned_races,
    list_planned_workouts,
    list_shoes,
    list_sleep,
    push_planned_workout,
    retire_shoe,
    set_activity_fueling,
    set_activity_name,
    set_activity_race,
    set_activity_shoe,
    set_activity_sport,
    set_default_shoe,
    set_goal,
    uncomplete_planned_workout,
    update_blood_test_result,
    update_bouldering_goal,
    update_note,
    update_planned_race,
    update_planned_workout,
)
from perseverer.config import get_settings
from perseverer.db.schema import (
    activity_stream,
    day_rollup,
    health_observation,
    metric_definition,
    period_rollup,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import StreamPoint
from perseverer.streams import write_activity_stream
from tests.api.conftest import TEST_API_KEY, seed_activity


@pytest.fixture(autouse=True)
def _mcp_tool_api_key_env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """mcp_server.py's `_call_api` reads `get_settings()` directly (not via FastAPI's DI), so
    it never sees the `client` fixture's `app.dependency_overrides`. Point the real
    env-backed settings at TEST_API_KEY too, so the tool's outgoing in-process request is
    accepted by the same server it's calling.
    """
    monkeypatch.setenv("PERSEVERER_API_KEY", TEST_API_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_list_activities_returns_seeded_data(client: TestClient, engine: Engine) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="running")
        seed_activity(conn, activity_id="a2", sport="cycling")

    result = await list_activities(sport="cycling")
    assert result["total"] == 1
    assert result["items"][0]["id"] == "a2"


async def test_get_activity_returns_detail(client: TestClient, engine: Engine) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    result = await get_activity("a1")
    assert result["id"] == "a1"
    assert result["laps"] == []


async def test_get_activity_stream_defaults_to_low_tier(
    client: TestClient, engine: Engine, tmp_path: Path
) -> None:
    """Default behavior is unchanged -- a caller that doesn't ask for anything finer still gets
    the coarse, context-window-friendly tier, not a whole activity's worth of raw samples."""
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    start = dt.datetime(2025, 6, 1, 10, tzinfo=dt.UTC)
    points = [
        StreamPoint(timestamp_utc=start + dt.timedelta(seconds=i), values={"heart_rate": 100.0 + i})
        for i in range(5000)
    ]
    rel_path, n_samples, channels = write_activity_stream(
        tmp_path / "parquet", DEFAULT_ATHLETE_ID, "a1", points
    )
    with engine.connect() as conn:
        conn.execute(
            activity_stream.insert().values(
                activity_id="a1",
                athlete_id=DEFAULT_ATHLETE_ID,
                parquet_path=rel_path,
                n_samples=n_samples,
                channels=json.dumps(channels),
                sample_rate_hint=1.0,
            )
        )
        conn.commit()

    result = await get_activity_stream("a1")
    assert result["tier"] == "low"
    assert len(result["timestamps"]) < 5000


async def test_get_activity_stream_supports_high_tier_narrowed_to_a_window(
    client: TestClient, engine: Engine, tmp_path: Path
) -> None:
    """The reported gap: a caller needs 1-second data for a specific few-minute stretch of a
    much longer activity, not the whole thing bucketed down to fit a fixed point budget."""
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", duration_s=5000.0)

    start = dt.datetime(2025, 6, 1, 10, tzinfo=dt.UTC)
    points = [
        StreamPoint(timestamp_utc=start + dt.timedelta(seconds=i), values={"heart_rate": 100.0 + i})
        for i in range(5000)
    ]
    rel_path, n_samples, channels = write_activity_stream(
        tmp_path / "parquet", DEFAULT_ATHLETE_ID, "a1", points
    )
    with engine.connect() as conn:
        conn.execute(
            activity_stream.insert().values(
                activity_id="a1",
                athlete_id=DEFAULT_ATHLETE_ID,
                parquet_path=rel_path,
                n_samples=n_samples,
                channels=json.dumps(channels),
                sample_rate_hint=1.0,
            )
        )
        conn.commit()

    result = await get_activity_stream("a1", tier="high", start_s=1000, end_s=1180)
    assert result["tier"] == "high"
    assert len(result["timestamps"]) == 181
    assert result["series"]["heart_rate"][0] == 1100.0
    assert result["series"]["heart_rate"][-1] == 1280.0


async def test_list_health_observations(client: TestClient, engine: Engine) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            metric_definition.insert().values(
                metric_key="resting_heart_rate",
                display_name="resting_heart_rate",
                category="health",
                value_type="numeric",
                first_seen_at=now,
                first_seen_source="fit_folder",
            )
        )
        conn.execute(
            health_observation.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                metric_key="resting_heart_rate",
                observed_at_utc=now,
                local_date="2025-06-01",
                aggregation="daily",
                value_num=48.0,
                source="fit_folder",
            )
        )
        conn.commit()

    result = await list_health_observations(
        metric_key=["resting_heart_rate"], start_date="2025-06-01", end_date="2025-06-01"
    )
    assert result["total"] == 1
    assert result["items"][0]["value_num"] == 48.0


async def test_list_sleep_empty_range(client: TestClient) -> None:
    result = await list_sleep(start_date="2020-01-01", end_date="2020-01-02")
    assert result == []


async def test_get_calendar_reads_rollup_tables(client: TestClient, engine: Engine) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            day_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-06-01",
                activity_count=1,
                refreshed_at=now,
            )
        )
        conn.commit()

    result = await get_calendar(start_date="2025-06-01", end_date="2025-06-01")
    assert len(result["days"]) == 1
    assert result["days"][0]["activity_count"] == 1


async def test_create_note_then_list_notes_round_trip(client: TestClient) -> None:
    created = await create_note(entity_type="day", entity_id="2025-06-01", body="felt great")
    assert created["body"] == "felt great"

    notes = await list_notes(entity_type="day", entity_id="2025-06-01")
    assert len(notes) == 1
    assert notes[0]["body"] == "felt great"


async def test_create_note_404_for_unknown_activity(client: TestClient) -> None:
    with pytest.raises(httpx.HTTPStatusError, match="404"):
        await create_note(entity_type="activity", entity_id="doesnotexist", body="x")


async def test_update_note_then_delete_note_round_trip(client: TestClient) -> None:
    created = await create_note(entity_type="day", entity_id="2025-06-01", body="first draft")
    updated = await update_note(created["id"], "revised")
    assert updated["body"] == "revised"

    await delete_note(created["id"])
    assert await list_notes(entity_type="day", entity_id="2025-06-01") == []


async def test_get_calendar_weeks_and_months_read_period_rollups(
    client: TestClient, engine: Engine
) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            period_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="week",
                period_start="2025-06-02",
                period_end="2025-06-08",
                activity_count=3,
                refreshed_at=now,
            )
        )
        conn.execute(
            period_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="month",
                period_start="2025-06",
                period_end="2025-06-30",
                activity_count=10,
                refreshed_at=now,
            )
        )
        conn.commit()

    weeks = await get_calendar_weeks(start_date="2025-06-01", end_date="2025-06-30")
    assert weeks["periods"][0]["activity_count"] == 3

    # period_start for a month row is "YYYY-MM" (e.g. "2025-06"), compared as a plain string
    # against the query's own full-date bounds -- a narrow single-month range like
    # "2025-06-01".."2025-06-30" would never satisfy "2025-06" >= "2025-06-01" lexicographically,
    # so this needs a full-year-spanning range the way a real Year-view caller would use.
    months = await get_calendar_months(start_date="2025-01-01", end_date="2025-12-31")
    assert months["periods"][0]["activity_count"] == 10


async def test_list_activity_years_empty(client: TestClient) -> None:
    assert await list_activity_years() == []


async def test_get_climbing_summary_empty_range(client: TestClient) -> None:
    result = await get_climbing_summary(start_date="2020-01-01", end_date="2020-01-02")
    assert result["session_count"] == 0


async def test_get_activity_context_for_a_real_activity(client: TestClient, engine: Engine) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    # The seeded activity is itself a same-sport comparable within the lookback window, so
    # `recent` includes it -- this just confirms the tool reaches the endpoint and gets the
    # real shape back, not an empty-history assumption.
    result = await get_activity_context("a1")
    assert result["recent"][0]["id"] == "a1"


async def test_get_activity_comparisons_for_a_real_activity(
    client: TestClient, engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    result = await get_activity_comparisons("a1")
    assert result["rows"] == []


async def test_get_activity_climb_comparisons_for_a_bouldering_activity(
    client: TestClient, engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")

    result = await get_activity_climb_comparisons("a1")
    assert result["rows"] == []


async def test_get_activity_insights_for_a_real_activity(
    client: TestClient, engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    result = await get_activity_insights("a1")
    assert isinstance(result, list)


async def test_get_activity_weather_unavailable_with_no_gps(
    client: TestClient, engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    result = await get_activity_weather("a1")
    assert result["available"] is False


async def test_get_activity_location_unavailable_with_no_gps(
    client: TestClient, engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    result = await get_activity_location("a1")
    assert result["available"] is False


async def test_get_activity_workout_is_none_without_a_planned_workout(
    client: TestClient, engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    assert await get_activity_workout("a1") is None


async def test_get_activity_sources_for_a_real_activity(client: TestClient, engine: Engine) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    result = await get_activity_sources("a1")
    assert isinstance(result["sources"], list)


async def test_get_health_dashboard_empty_range(client: TestClient) -> None:
    result = await get_health_dashboard(start_date="2020-01-01", end_date="2020-01-02")
    assert result["metrics"] == []


async def test_get_health_stream_empty_arrays_without_data(client: TestClient) -> None:
    """Empty arrays, not a 404, when there's no stream data for this metric/day -- a normal
    state (see get_health_stream's own REST docstring)."""
    result = await get_health_stream(
        metric_key="garmin.daily_body_battery.level", for_date="2025-06-01"
    )
    assert result["timestamps"] == []
    assert result["values"] == []


async def test_get_fitness_and_get_performance_empty_range(client: TestClient) -> None:
    assert await get_fitness(start_date="2020-01-01", end_date="2020-01-02") == []
    assert await get_performance(start_date="2020-01-01", end_date="2020-01-02") == []


async def test_get_vo2max_analysis_no_qualifying_run(client: TestClient) -> None:
    result = await get_vo2max_analysis(as_of="2025-06-01")
    assert result["rolling_vdot"] is None
    assert result["driving_activity"] is None


async def test_get_pace_hr_zones_no_history(client: TestClient) -> None:
    result = await get_pace_hr_zones(as_of="2025-06-01")
    assert result["profile_vdot"] is None
    # The 5-zone table itself is a fixed scaffold -- always present, just with null pace/HR
    # ranges until there's real running history to compute them from.
    assert len(result["zones"]) == 5
    assert result["zones"][0]["pace_fast_s_per_km"] is None


async def test_get_race_readiness_unavailable_with_no_upcoming_race(client: TestClient) -> None:
    result = await get_race_readiness(as_of="2025-06-01")
    assert result["available"] is False


async def test_get_performance_curve_empty_range(client: TestClient) -> None:
    result = await get_performance_curve(
        metric="pace", start_date="2020-01-01", end_date="2020-01-02"
    )
    assert result["points"] == []


async def test_list_insights_and_pace_bands_empty(client: TestClient) -> None:
    assert await list_insights() == []
    bands = await get_pace_bands()
    assert all(b["seconds"] == 0.0 for b in bands)


async def test_get_weather_forecast_unavailable_without_a_home_location(
    client: TestClient,
) -> None:
    result = await get_weather_forecast()
    assert result["available"] is False


async def test_list_blood_tests_empty_range(client: TestClient) -> None:
    assert await list_blood_tests(start_date="2020-01-01", end_date="2020-01-02") == []


async def test_get_goal_progress_unavailable_when_none_set(client: TestClient) -> None:
    result = await get_goal_progress(period_type="year", period_start="2025")
    assert result["available"] is False


async def test_list_planned_workouts_and_by_date_empty(client: TestClient) -> None:
    assert await list_planned_workouts(start_date="2025-06-01", end_date="2025-06-30") == []
    assert await get_planned_workouts_for_date("2025-06-01") == []


async def test_list_planned_races_empty(client: TestClient) -> None:
    assert await list_planned_races(start_date="2025-06-01", end_date="2025-06-30") == []


async def test_list_shoes_and_gear_alerts_empty(client: TestClient) -> None:
    assert await list_shoes() == []
    assert await get_gear_alerts() == []


async def test_training_plan_round_trip_create_update_complete_delete(
    client: TestClient,
) -> None:
    created = await create_planned_workout(
        local_date="2026-10-05",
        sport="running",
        name="Tempo",
        source_text="Warmup 10m\n\n3x\n  5m 4:30-4:45/km Pace\n  recovery 2m\n\nCooldown 10m",
        scheduled_time="18:30",
    )
    assert created["parse_errors"] == []
    assert created["push_status"] == "draft"
    workout_id = created["id"]

    listed = await list_planned_workouts(start_date="2026-10-01", end_date="2026-10-31")
    assert [w["id"] for w in listed] == [workout_id]

    updated = await update_planned_workout(
        workout_id, sport="running", name="Easy", source_text="Active 30m"
    )
    assert updated["name"] == "Easy"

    done = await complete_planned_workout(workout_id)
    assert done["completed_at"] is not None
    assert (await uncomplete_planned_workout(workout_id))["completed_at"] is None

    await delete_planned_workout(workout_id)
    assert await get_planned_workouts_for_date("2026-10-05") == []


async def test_create_planned_workout_reports_parse_errors_instead_of_rejecting(
    client: TestClient,
) -> None:
    created = await create_planned_workout(
        local_date="2026-10-05", sport="running", source_text="10m nonsense-token"
    )
    assert created["parse_errors"]


async def test_create_strength_workout_from_structured_steps(client: TestClient) -> None:
    created = await create_planned_workout(
        local_date="2026-10-06",
        sport="strength_training",
        name="Upper",
        steps=[
            {
                "step_index": 0,
                "duration_type": "reps",
                "duration_reps": 10,
                "intensity": "active",
                "exercise_category": "BENCH_PRESS",
                "exercise_name": "",
                "weight_kg": 40,
            }
        ],
    )
    assert created["steps"][0]["exercise_category"] == "BENCH_PRESS"


async def test_create_recurring_planned_workout_weekly(client: TestClient) -> None:
    result = await create_recurring_planned_workout(
        local_date="2026-10-05",
        sport="yoga",
        frequency="weekly",
        name="Flow",
        duration_minutes=45,
        count=3,
    )
    assert result["created_dates"] == ["2026-10-05", "2026-10-12", "2026-10-19"]


async def test_push_planned_workout_404_for_unknown_workout(client: TestClient) -> None:
    with pytest.raises(httpx.HTTPStatusError, match="404"):
        await push_planned_workout(99999)


async def test_planned_race_crud(client: TestClient) -> None:
    race = await create_planned_race(
        local_date="2026-12-06", name="CIM", distance_m=42195, target_duration_s=14400
    )
    updated = await update_planned_race(
        race["id"], local_date="2026-12-06", name="CIM 2026", distance_m=42195
    )
    assert updated["name"] == "CIM 2026"
    assert len(await list_planned_races(start_date="2026-12-01", end_date="2026-12-31")) == 1
    await delete_planned_race(race["id"])
    assert await list_planned_races(start_date="2026-12-01", end_date="2026-12-31") == []


async def test_set_and_delete_goal(client: TestClient) -> None:
    goal = await set_goal("year", "2026", 2_000_000, sport="running")
    assert goal["target_distance_m"] == 2_000_000
    assert (await get_goal_progress("year", "2026"))["available"] is True
    await delete_goal(goal["id"])
    assert (await get_goal_progress("year", "2026"))["available"] is False


async def test_bouldering_goal_tools(client: TestClient) -> None:
    assert await get_bouldering_goals("year", "2026") == []
    goal = await create_bouldering_goal("year", "2026", 10, grade=4)
    assert goal["grade"] == 4
    assert goal["target_count"] == 10
    october = await create_bouldering_goal("month", "2026-10", 1, grade=5)
    assert october["period_start"] == "2026-10"
    listed = await get_bouldering_goals("year", "2026")
    assert [g["goal"]["id"] for g in listed] == [goal["id"]]
    assert listed[0]["current_count"] == 0
    updated = await update_bouldering_goal(goal["id"], "year", "2026", 12, grade=4)
    assert updated["target_count"] == 12
    await delete_bouldering_goal(goal["id"])
    assert await get_bouldering_goals("year", "2026") == []


async def test_blood_test_write_tools(client: TestClient) -> None:
    single = await create_blood_test_result("2026-09-01", "HbA1c", 5.2, unit="%")
    panel = await create_blood_test_panel(
        "2026-09-02", [{"marker": "LDL", "value_num": 90, "unit": "mg/dL"}], lab_name="Quest"
    )
    assert len(panel) == 1
    updated = await update_blood_test_result(single["id"], "2026-09-01", "HbA1c", 5.4, unit="%")
    assert updated["value_num"] == 5.4
    await delete_blood_test_result(single["id"])
    await delete_blood_test_panel("2026-09-02")
    assert await list_blood_tests("2026-09-01", "2026-09-30") == []


async def test_gear_write_tools(client: TestClient, engine: Engine) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    shoe = await create_shoe("Brooks", "Ghost", max_distance_km=None)
    assigned = await set_activity_shoe("a1", shoe["id"])
    assert assigned == {"shoe_id": shoe["id"], "is_default": False}
    default = await set_default_shoe("running", shoe["id"])
    assert default["default_sports"] == ["running"]
    retired = await retire_shoe(shoe["id"])
    assert retired["retired"] is True


async def test_activity_correction_tools(client: TestClient, engine: Engine) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    assert (await set_activity_sport("a1", "hiking"))["sport"] == "hiking"
    assert (await set_activity_race("a1", True))["is_race"] is True
    assert (await set_activity_name("a1", "Big day"))["name"] == "Big day"
    fueling = await set_activity_fueling("a1", carbohydrates_g=60, sodium_mg=500)
    assert fueling["carbohydrates_g"] == 60


async def test_get_last_activity_forecast_unavailable_without_a_located_activity(
    client: TestClient,
) -> None:
    result = await get_last_activity_forecast()
    assert result["available"] is False
    assert result["source_activity"] is None
