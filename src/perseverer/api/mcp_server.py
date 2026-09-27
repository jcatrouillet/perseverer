"""MCP (Model Context Protocol) server exposing the read API + notes as tools -- the write
path AGENTS.md's mission statement calls for ("a REST/JSON API an AI agent can write notes
through"), now a first-class tool surface instead of raw HTTP a human has to proxy. Mounted
into the same FastAPI app as the REST API (api/main.py), not a separate container/process --
see docs/adr/0007-phase-4-mcp-server.md.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from perseverer.auth.oauth import SCOPE, oauth_provider
from perseverer.config import get_settings

_public_base_url = (get_settings().public_base_url or "http://localhost:8000").rstrip("/")

mcp = FastMCP(
    "Perseverer",
    # Stateless: `api` runs 2 uvicorn workers that share no process memory, and Streamable HTTP
    # sessions are otherwise held in the memory of whichever worker handled `initialize` -- a
    # follow-up request landing on the other worker got "session not found" (404/400), observed
    # live as ~4 of 11 real claude.ai requests failing. Every tool here is a plain request/
    # response, so nothing needs a persistent session.
    stateless_http=True,
    # OAuth 2.1 (dynamic client registration + PKCE) so a remote MCP client that can't send a
    # custom header -- claude.ai's custom connector -- can authenticate. The SDK serves
    # /.well-known/*, /authorize, /token, /register and /revoke; auth/oauth.py supplies storage
    # and api/routers/oauth.py the login page. The header-key path is preserved by the ASGI
    # wrapper below. See ADR 0007 decision 10.
    auth_server_provider=oauth_provider,
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(_public_base_url),
        resource_server_url=AnyHttpUrl(f"{_public_base_url}/mcp"),
        required_scopes=[SCOPE],
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
        ),
        revocation_options=RevocationOptions(enabled=True),
    ),
    # DNS-rebinding protection is redundant here -- the API key (_require_api_key_asgi below)
    # is the real gate, and enabling this would need a deployment-specific hostname baked into
    # config for no real security gain against that specific threat. See ADR 0007 decision 4.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


async def _call_api(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Every tool's single entry point back into the REST layer -- in-process, no real network
    hop, reusing 100% of the REST API's auth/validation/rollup/response-shaping instead of a
    second implementation against `Connection`/`duckdb` directly. See ADR 0007 decision 6.
    """
    from perseverer.api.main import app  # lazy: api/main.py mounts this module's ASGI app

    settings = get_settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp-internal") as client:
        response = await client.request(
            method, path, headers={"X-API-Key": settings.api_key or ""}, **kwargs
        )
    response.raise_for_status()
    return response


@mcp.tool()
async def list_activities(
    start_date: str | None = None,
    end_date: str | None = None,
    sport: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List activities (SI units: metres/seconds/etc, UTC timestamps), optionally filtered by
    an ISO date range (start_date/end_date, e.g. "2025-06-01") and/or sport. Paginated."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if sport:
        params["sport"] = sport
    response = await _call_api("GET", "/api/v1/activities", params=params)
    return dict(response.json())


@mcp.tool()
async def get_activity(activity_id: str) -> dict[str, Any]:
    """Full detail for one activity: laps, splits, route bounding box, device, and any
    open-ended per-activity metrics with no dedicated field."""
    response = await _call_api("GET", f"/api/v1/activities/{activity_id}")
    return dict(response.json())


@mcp.tool()
async def get_activity_stream(
    activity_id: str,
    tier: str = "low",
    channels: list[str] | None = None,
    start_s: float | None = None,
    end_s: float | None = None,
) -> dict[str, Any]:
    """Time-series data for one activity (heart rate, pace, cadence, etc), elapsed seconds from
    the activity's own start. Defaults to the coarse "low" tier (~200 points spanning the whole
    activity) to keep a whole-activity request out of the context window by default -- pass
    tier="high" for near-1-second resolution (activities under ~5.5h at 1Hz get their true raw
    samples; a longer one still gets sub-6s buckets across its full span).

    To inspect a specific stretch at full resolution without paying for the whole activity's own
    high-tier response, narrow with start_s/end_s (elapsed seconds from the activity's own start,
    e.g. start_s=1200, end_s=1380 for the 20:00-23:00 mark) -- bucket width is sized from that
    narrowed span, not the whole activity, so even a multi-hour activity's own 3-minute stretch
    comes back at true 1-second resolution under tier="high". channels optionally limits which
    series are returned (e.g. ["heart_rate", "cadence"]) -- omit for every channel the activity
    actually recorded. See list_activities/get_activity for which channels an activity has."""
    params: dict[str, Any] = {"tier": tier}
    if channels:
        params["channels"] = channels
    if start_s is not None:
        params["start_s"] = start_s
    if end_s is not None:
        params["end_s"] = end_s
    response = await _call_api("GET", f"/api/v1/activities/{activity_id}/stream", params=params)
    return dict(response.json())


@mcp.tool()
async def list_health_observations(
    metric_key: list[str],
    start_date: str,
    end_date: str,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """List raw health observations for one or more metric_keys (e.g.
    ["resting_heart_rate"]) in an ISO date range. For per-day summaries/aggregates, prefer
    get_calendar instead -- this returns individual readings, not rollups."""
    params: list[tuple[str, str]] = [("metric_key", m) for m in metric_key]
    params += [
        ("start_date", start_date),
        ("end_date", end_date),
        ("limit", str(limit)),
        ("offset", str(offset)),
    ]
    response = await _call_api("GET", "/api/v1/health/observations", params=params)
    return dict(response.json())


@mcp.tool()
async def list_sleep(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """List sleep sessions (with stage breakdown: light/deep/rem/awake) in an ISO date
    range."""
    response = await _call_api(
        "GET", "/api/v1/sleep", params={"start_date": start_date, "end_date": end_date}
    )
    return list(response.json())


@mcp.tool()
async def get_calendar(
    start_date: str, end_date: str, metric_keys: list[str] | None = None
) -> dict[str, Any]:
    """Precomputed per-day rollup (activity totals, sleep, health metric aggregates) for an
    ISO date range -- the best starting point for "how was my week/month" style questions,
    since it's already aggregated rather than raw per-observation data."""
    params: list[tuple[str, str]] = [("start_date", start_date), ("end_date", end_date)]
    if metric_keys:
        params += [("metric_keys", m) for m in metric_keys]
    response = await _call_api("GET", "/api/v1/calendar", params=params)
    return dict(response.json())


@mcp.tool()
async def get_calendar_weeks(start_date: str, end_date: str) -> dict[str, Any]:
    """Precomputed week-level rollups (activity totals, distance, elevation, moving time) for an
    ISO date range -- a sum-of-sums over get_calendar's own daily rollups, one row per Monday-
    starting week overlapping the range. Prefer this over get_calendar for a "how was this month,
    week by week" question."""
    response = await _call_api(
        "GET", "/api/v1/calendar/weeks", params={"start_date": start_date, "end_date": end_date}
    )
    return dict(response.json())


@mcp.tool()
async def get_calendar_months(start_date: str, end_date: str) -> dict[str, Any]:
    """Precomputed month-level rollups, the same shape as get_calendar_weeks but bucketed by
    calendar month instead of week."""
    response = await _call_api(
        "GET", "/api/v1/calendar/months", params={"start_date": start_date, "end_date": end_date}
    )
    return dict(response.json())


@mcp.tool()
async def list_activity_years() -> list[int]:
    """Distinct calendar years with at least one activity, descending -- the cheap way to learn
    how far back this athlete's own history goes before picking a date range for another tool."""
    response = await _call_api("GET", "/api/v1/activities/years")
    return list(response.json())


@mcp.tool()
async def get_climbing_summary(
    start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    """Aggregate bouldering stats (routes attempted/completed, by grade) across every session in
    an optional ISO date range -- omit both dates for the athlete's whole climbing history."""
    params: dict[str, Any] = {}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    response = await _call_api("GET", "/api/v1/activities/climbing-summary", params=params)
    return dict(response.json())


@mcp.tool()
async def get_activity_context(activity_id: str) -> dict[str, Any]:
    """How this activity stacks up against the athlete's own same-sport history: a percentile
    rank and same-sport/similar-distance peer activities from the 90 days up to and including
    this one, plus this activity's own all-time-fastest same-sport/similar-distance peers."""
    response = await _call_api("GET", f"/api/v1/activities/{activity_id}/context")
    return dict(response.json())


@mcp.tool()
async def get_activity_comparisons(activity_id: str) -> dict[str, Any]:
    """A running activity's pace/HR/cadence compared against its own recent similar-effort
    history -- the numbers behind the activity detail page's comparison table."""
    response = await _call_api("GET", f"/api/v1/activities/{activity_id}/comparisons")
    return dict(response.json())


@mcp.tool()
async def get_activity_climb_comparisons(activity_id: str) -> dict[str, Any]:
    """A bouldering session's routes-by-grade compared against the athlete's own recent
    bouldering history."""
    response = await _call_api("GET", f"/api/v1/activities/{activity_id}/climb-comparisons")
    return dict(response.json())


@mcp.tool()
async def get_activity_insights(activity_id: str) -> list[dict[str, Any]]:
    """Rules-based insights computed specifically about this one activity (e.g. "longest run in
    the last 12 months", an all-time best, a current streak) -- always derived from the
    athlete's own past, never anything that happened after this activity."""
    response = await _call_api("GET", f"/api/v1/activities/{activity_id}/insights")
    return list(response.json())


@mcp.tool()
async def get_activity_weather(activity_id: str) -> dict[str, Any]:
    """Weather during this activity's own time window at its own GPS location (temperature, dew
    point, wind, precipitation, an hour-by-hour trajectory) -- read from this app's own archived
    Open-Meteo fetch, never a live vendor call. `available: false` if nothing was ever fetched
    for this activity (no GPS start point, or the fetch failed)."""
    response = await _call_api("GET", f"/api/v1/activities/{activity_id}/weather")
    return dict(response.json())


@mcp.tool()
async def get_activity_location(activity_id: str) -> dict[str, Any]:
    """The reverse-geocoded place name (city/region/country) for this activity's own GPS start
    point, from this app's own cache -- a background lookup is triggered on first request if
    nothing is cached yet, so a repeat call shortly after may return a freshly-resolved name."""
    response = await _call_api("GET", f"/api/v1/activities/{activity_id}/location")
    return dict(response.json())


@mcp.tool()
async def get_activity_workout(activity_id: str) -> dict[str, Any] | None:
    """The pre-planned structured workout (if any) this activity was recorded against -- steps,
    targets, and how each executed lap compares. `null` if this activity has no associated
    planned workout."""
    response = await _call_api("GET", f"/api/v1/activities/{activity_id}/workout")
    result = response.json()
    return dict(result) if result is not None else None


@mcp.tool()
async def get_activity_sources(activity_id: str) -> dict[str, Any]:
    """Which raw sources (Garmin device, Strava, etc) contributed to this activity, and why they
    were merged into one record if more than one did."""
    response = await _call_api("GET", f"/api/v1/activities/{activity_id}/sources")
    return dict(response.json())


@mcp.tool()
async def get_health_dashboard(start_date: str, end_date: str) -> dict[str, Any]:
    """Per-day body/vitals metrics (weight, BMI, resting/max HR, HRV, SpO2, steps, body
    composition, blood pressure, etc) merged across whichever source actually recorded each
    logical metric -- the same data the Health page's trend charts read."""
    response = await _call_api(
        "GET", "/api/v1/health/dashboard", params={"start_date": start_date, "end_date": end_date}
    )
    return dict(response.json())


@mcp.tool()
async def get_health_stream(metric_key: str, for_date: str) -> dict[str, Any]:
    """An intraday (sub-daily) health series for one metric on one date -- e.g.
    "garmin.daily_body_battery.level" or a stress series -- read from health_stream, not the
    daily-scalar health_observation table get_health_dashboard/list_health_observations use."""
    response = await _call_api(
        "GET", "/api/v1/health/stream", params={"metric_key": metric_key, "date": for_date}
    )
    return dict(response.json())


@mcp.tool()
async def get_fitness(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Daily Coggan/Banister training load, CTL (fitness), ATL (fatigue), and TSB (form) over an
    ISO date range -- this project's own independently-computed estimate, shown alongside
    (never reconciled against) Garmin's own Training Readiness/Status."""
    response = await _call_api(
        "GET", "/api/v1/fitness", params={"start_date": start_date, "end_date": end_date}
    )
    return list(response.json())


@mcp.tool()
async def get_performance(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Daily rolling VDOT, max HR, threshold pace/HR (aerobic + anaerobic), and race-time
    predictions (5k/10k/half/marathon) over an ISO date range -- this project's own
    independently-computed estimates, shown alongside (never reconciled against) Garmin's own
    race predictions/lactate threshold."""
    response = await _call_api(
        "GET", "/api/v1/performance", params={"start_date": start_date, "end_date": end_date}
    )
    return list(response.json())


@mcp.tool()
async def get_vo2max_analysis(as_of: str | None = None) -> dict[str, Any]:
    """Which activity is currently driving the athlete's own rolling VDOT/VO2max estimate, which
    other recent runs are ready to take over, and a plain-language explanation of any gap (no
    qualifying run yet, the driving run is about to age out, etc). Defaults `as_of` to today."""
    params = {"as_of": as_of} if as_of else {}
    response = await _call_api("GET", "/api/v1/performance/vo2max-analysis", params=params)
    return dict(response.json())


@mcp.tool()
async def get_pace_hr_zones(as_of: str | None = None) -> dict[str, Any]:
    """The complete 5-zone pace + heart-rate table (Recovery/Basic Endurance/Aerobic Threshold/
    Lactate Threshold/VO2 Max), each with a real pace range, HR range, and a plain-language
    "when/how to use it" -- built from the athlete's own entire running history. Defaults `as_of`
    to today."""
    params = {"as_of": as_of} if as_of else {}
    response = await _call_api("GET", "/api/v1/performance/pace-hr-zones", params=params)
    return dict(response.json())


@mcp.tool()
async def get_race_readiness(
    race_id: int | None = None, as_of: str | None = None
) -> dict[str, Any]:
    """Has the athlete run enough recent weekly distance and long-run volume for their next
    scheduled race, not just "are they fit" -- readiness percentage, current volume vs target,
    a VDOT-based prognosis shown alongside (never blended into) readiness, and the week-by-week
    history behind it. Defaults to the athlete's own nearest upcoming running race; `race_id`
    targets a specific one. `available: false` (never fabricated) with no upcoming race."""
    params: dict[str, Any] = {}
    if race_id is not None:
        params["race_id"] = race_id
    if as_of:
        params["as_of"] = as_of
    response = await _call_api("GET", "/api/v1/performance/race-readiness", params=params)
    return dict(response.json())


@mcp.tool()
async def get_performance_curve(
    metric: str, start_date: str, end_date: str, sports: str | None = None
) -> dict[str, Any]:
    """The best sustained average value for each of a fixed set of durations (1s-2h) across
    every qualifying activity in an ISO date range -- a Runalyze-style "Heart Rate Curve"/cycling
    "Critical Power Curve" applied to this athlete's own history. `metric` is "pace", "gap", or
    "heart_rate" ("pace"/"gap" are always running-only; "heart_rate" honors `sports`, a
    comma-separated list, e.g. "running,cycling")."""
    params: dict[str, Any] = {"metric": metric, "start_date": start_date, "end_date": end_date}
    if sports:
        params["sports"] = sports
    response = await _call_api("GET", "/api/v1/performance/curve", params=params)
    return dict(response.json())


@mcp.tool()
async def list_insights(kind: str | None = None, window: str | None = None) -> list[dict[str, Any]]:
    """Athlete-wide rules-based insights (personal bests, streaks, notable efforts, training-load
    flags, health-metric callouts) -- deterministic, not LLM-generated, refreshed on every
    ingest. Optionally filter by `kind` and/or `window`."""
    params: dict[str, Any] = {}
    if kind:
        params["kind"] = kind
    if window:
        params["window"] = window
    response = await _call_api("GET", "/api/v1/insights", params=params)
    return list(response.json())


@mcp.tool()
async def get_pace_bands() -> list[dict[str, Any]]:
    """Total time-in-band across the athlete's whole running history, fastest to slowest band."""
    response = await _call_api("GET", "/api/v1/insights/pace-bands")
    return list(response.json())


@mcp.tool()
async def get_weather_forecast(days: int | None = None) -> dict[str, Any]:
    """Weather forecast for the athlete's own configured home location -- a coarse per-day
    icon/temperature for up to 16 days, plus rich hour-by-hour conditions (dew point, wind,
    precipitation, sunrise/sunset) for the next few days. `available: false` if the athlete
    hasn't set a home location. Defaults `days` to the maximum (16)."""
    params = {"days": days} if days is not None else {}
    response = await _call_api("GET", "/api/v1/weather/forecast", params=params)
    return dict(response.json())


@mcp.tool()
async def get_last_activity_forecast(days: int | None = None) -> dict[str, Any]:
    """Weather forecast (default the next 3 days, plus rich hour-by-hour detail: dew point, wind,
    precipitation, sunrise/sunset) at the GPS start point of the athlete's most recent activity
    that has one -- use this instead of get_weather_forecast when the athlete is away from home.
    The response names the source_activity and the timezone its local dates are in.
    `available: false` if there is no activity with a location or the fetch failed."""
    params = {"days": days} if days is not None else {}
    response = await _call_api("GET", "/api/v1/weather/forecast/last-activity", params=params)
    return dict(response.json())


@mcp.tool()
async def list_blood_tests(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Athlete-entered lab results (one row per marker per draw) in an ISO date range, with each
    marker's own reference range and an out-of-range flag against the athlete's own stated
    range."""
    response = await _call_api(
        "GET", "/api/v1/blood-tests", params={"start_date": start_date, "end_date": end_date}
    )
    return list(response.json())


@mcp.tool()
async def get_goal_progress(period_type: str, period_start: str) -> dict[str, Any]:
    """Progress toward a distance goal for one calendar year or month. `period_type` is "year" or
    "month"; `period_start` is "YYYY" for a year or "YYYY-MM" for a month. `available: false`
    (never fabricated) when no goal is set for that exact period."""
    response = await _call_api(
        "GET",
        "/api/v1/goals",
        params={"period_type": period_type, "period_start": period_start},
    )
    return dict(response.json())


@mcp.tool()
async def list_planned_workouts(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Scheduled workouts (any sport tier: running/yoga/bouldering/hiit/strength_training) in an
    ISO date range -- each with its own completion status (explicit or matched against a
    recorded activity) and, for running, an estimated distance/duration/load."""
    response = await _call_api(
        "GET", "/api/v1/planned-workouts", params={"start_date": start_date, "end_date": end_date}
    )
    return list(response.json())


@mcp.tool()
async def get_planned_workouts_for_date(local_date: str) -> list[dict[str, Any]]:
    """Every scheduled workout on one specific date (a day can hold more than one, e.g. a
    morning run plus an evening strength session), in full detail including steps/targets."""
    response = await _call_api("GET", f"/api/v1/planned-workouts/by-date/{local_date}")
    return list(response.json())


@mcp.tool()
async def list_planned_races(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Races on the calendar in an ISO date range, each with days-until and a target-vs-
    predicted finish time (from this athlete's own VDOT-based prediction, when the race's
    distance matches a standard race distance)."""
    response = await _call_api(
        "GET", "/api/v1/planned-races", params={"start_date": start_date, "end_date": end_date}
    )
    return list(response.json())


@mcp.tool()
async def list_shoes(include_retired: bool = False) -> list[dict[str, Any]]:
    """The athlete's own shoe pairs, each with live accumulated distance (from activities
    explicitly assigned to it, plus its sport's own dated default when applicable),
    remaining distance to its configured limit, and which sport(s) it's the current default
    for."""
    response = await _call_api(
        "GET", "/api/v1/gear/shoes", params={"include_retired": include_retired}
    )
    return list(response.json())


@mcp.tool()
async def get_gear_alerts() -> list[dict[str, Any]]:
    """Every shoe pair whose live accumulated distance has reached its own configured
    replacement limit."""
    response = await _call_api("GET", "/api/v1/gear/alerts")
    return list(response.json())


@mcp.tool()
async def update_note(note_id: int, body: str) -> dict[str, Any]:
    """Edit an existing note's own text, keeping its entity/author/created_at unchanged."""
    response = await _call_api("PUT", f"/api/v1/notes/{note_id}", json={"body": body})
    return dict(response.json())


@mcp.tool()
async def delete_note(note_id: int) -> None:
    """Delete a note."""
    await _call_api("DELETE", f"/api/v1/notes/{note_id}")


@mcp.tool()
async def create_note(
    entity_type: str, entity_id: str, body: str, author: str | None = None
) -> dict[str, Any]:
    """Attach a note. entity_type is "activity" (entity_id = the activity's id), "day"
    (entity_id = an ISO date like "2025-06-01"), or "week" (entity_id = that week's Monday, same
    ISO date format)."""
    response = await _call_api(
        "POST",
        "/api/v1/notes",
        json={
            "entity_type": entity_type,
            "entity_id": entity_id,
            "body": body,
            "author": author,
        },
    )
    return dict(response.json())


@mcp.tool()
async def list_notes(entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    """List notes attached to one activity, day, or week (see create_note for entity_type/
    entity_id semantics)."""
    response = await _call_api(
        "GET", "/api/v1/notes", params={"entity_type": entity_type, "entity_id": entity_id}
    )
    return list(response.json())


def _drop_none(payload: dict[str, Any]) -> dict[str, Any]:
    """Omit unset optional fields from a request body rather than sending explicit nulls."""
    return {k: v for k, v in payload.items() if v is not None}


# --- Write tools -----------------------------------------------------------------------------
# Planned workouts / races (training plans), goals, blood tests, gear, and per-activity
# corrections. Deliberately NOT exposed: every settings/* endpoint (credentials, rebuild, bulk
# import), and trim/merge/split/climb-route edits (visual-review workflows). See ADR 0007
# decision 9.

_WORKOUT_SYNTAX_HELP = """
sport is "running", "yoga", "bouldering", "hiit", or "strength_training".

running -- put the workout in source_text, ONE STEP PER LINE, grammar:
    [intensity] duration [target] [cadence] [# comment]
  intensity (optional): warmup | cooldown | recovery | rest | active
  duration (required): 10m, 5m30s, 90s (time; bare "m" = MINUTES) or 2km, 800mtr, 1mi
    (distance; "mtr" = meters), or "lap" (runs until the athlete presses the watch lap button;
    "lap 5km" adds a calendar-only estimate, never an end condition)
  target (optional): "5:10/km Pace" or "5:00-5:20/km Pace"; or "150 HR", "140-150 HR", "Z2 HR"
  cadence (optional): "170-180spm" or "175spm"
  A standalone "Nx" line (e.g. "5x") starts a repeat block: every following non-blank line, up to
  the next BLANK LINE, is a child repeated N times. A step meant to come AFTER the block needs its
  own blank line before it -- indentation does not end a block. Example:
      Warmup 15m 6:30-7:00/km Pace

      5x
        1km 4:50-5:00/km Pace
        recovery 90s

      Cooldown 10m 6:30-7:00/km Pace
  Unrecognized tokens don't reject the workout -- they come back in parse_errors, so check it.

yoga / bouldering -- source_text is freeform notes (never parsed); set duration_minutes.

hiit / strength_training -- pass structured steps (source_text ignored), each a dict:
    {"step_index": 0, "duration_type": "reps"|"time"|"rest", "duration_reps": 10,
     "duration_time_s": 45, "intensity": "active"|"rest", "exercise_category": "BENCH_PRESS",
     "exercise_name": "" (or e.g. "HAMMER_CURL"), "weight_kg": 40, "comment": "...",
     "repeat_from_step": 0, "repeat_count": 3}
  A repeat step has repeat_from_step/repeat_count and repeats the steps from repeat_from_step up
  to (not including) its own step_index. Category/name come from Garmin's exercise catalog.

scheduled_time is "HH:MM" 24h (display only). comment is a note for the whole workout.
Creating/updating only saves the workout as a draft in Perseverer; workouts due within the push
window (default 7 days) are pushed to the athlete's Garmin automatically each day, or call
push_planned_workout to push one now.
"""


@mcp.tool(
    description=(
        "Schedule a workout on an ISO date (a day may hold several). Returns the saved workout, "
        "including parse_errors and the computed estimated_duration_s/distance/load. Call once "
        "per session to build a training plan (or create_recurring_planned_workout for a "
        "repeating one)." + _WORKOUT_SYNTAX_HELP
    )
)
async def create_planned_workout(
    local_date: str,
    sport: str,
    name: str | None = None,
    source_text: str | None = None,
    scheduled_time: str | None = None,
    duration_minutes: float | None = None,
    steps: list[dict[str, Any]] | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """Schedule a workout on an ISO date (a day may hold several). Returns the saved workout,
    including parse_errors and the computed estimated_duration_s/distance/load. Call once per
    session to build a training plan (or create_recurring_planned_workout for a repeating one).
    """
    response = await _call_api(
        "POST",
        "/api/v1/planned-workouts",
        json=_drop_none(
            {
                "local_date": local_date,
                "sport": sport,
                "name": name,
                "source_text": source_text,
                "scheduled_time": scheduled_time,
                "duration_minutes": duration_minutes,
                "steps": steps,
                "comment": comment,
            }
        ),
    )
    return dict(response.json())


@mcp.tool()
async def update_planned_workout(
    workout_id: int,
    sport: str,
    name: str | None = None,
    source_text: str | None = None,
    scheduled_time: str | None = None,
    duration_minutes: float | None = None,
    steps: list[dict[str, Any]] | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """Replace an existing workout's content wholesale (same fields as create_planned_workout,
    minus the date -- to move a workout, delete and re-create it). Omitted optional fields are
    cleared, so pass the full desired content."""
    response = await _call_api(
        "PUT",
        f"/api/v1/planned-workouts/{workout_id}",
        json=_drop_none(
            {
                "sport": sport,
                "name": name,
                "source_text": source_text,
                "scheduled_time": scheduled_time,
                "duration_minutes": duration_minutes,
                "steps": steps,
                "comment": comment,
            }
        ),
    )
    return dict(response.json())


@mcp.tool()
async def delete_planned_workout(workout_id: int) -> None:
    """Delete a scheduled workout (also removes its Garmin-side template, best-effort, if it was
    already pushed)."""
    await _call_api("DELETE", f"/api/v1/planned-workouts/{workout_id}")


@mcp.tool()
async def complete_planned_workout(workout_id: int) -> dict[str, Any]:
    """Mark a scheduled workout as done (the athlete's own manual marker)."""
    response = await _call_api("POST", f"/api/v1/planned-workouts/{workout_id}/complete")
    return dict(response.json())


@mcp.tool()
async def uncomplete_planned_workout(workout_id: int) -> dict[str, Any]:
    """Clear a scheduled workout's manual done marker."""
    response = await _call_api("POST", f"/api/v1/planned-workouts/{workout_id}/uncomplete")
    return dict(response.json())


@mcp.tool()
async def push_planned_workout(workout_id: int) -> dict[str, Any]:
    """Push one scheduled workout to the athlete's Garmin account now (it appears on their watch
    calendar). Runs in the background -- read the workout back afterwards and check push_status
    ("pushed" / "push_failed" + push_error). Workouts due within the push window are pushed
    automatically each day anyway; use this to push one further out or retry a failure."""
    response = await _call_api("POST", f"/api/v1/planned-workouts/{workout_id}/push")
    return dict(response.json())


@mcp.tool(
    description=(
        "Schedule the same workout repeatedly starting at local_date. frequency is 'weekly', "
        "'every_n_days' (needs interval_days >= 1), or 'monthly'. Give exactly one of count "
        "(total occurrences, including the first) or until (ISO date, inclusive). Returns "
        "created_dates. Content fields work exactly as in create_planned_workout."
        + _WORKOUT_SYNTAX_HELP
    )
)
async def create_recurring_planned_workout(
    local_date: str,
    sport: str,
    frequency: str,
    name: str | None = None,
    source_text: str | None = None,
    scheduled_time: str | None = None,
    duration_minutes: float | None = None,
    steps: list[dict[str, Any]] | None = None,
    comment: str | None = None,
    interval_days: int | None = None,
    count: int | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Schedule the same workout repeatedly starting at local_date. frequency is "weekly",
    "every_n_days" (needs interval_days >= 1), or "monthly". Give exactly one of count (total
    occurrences, including the first) or until (ISO date, inclusive). Returns created_dates. The
    workout content fields work exactly as in create_planned_workout."""
    response = await _call_api(
        "POST",
        "/api/v1/planned-workouts/recurring",
        json=_drop_none(
            {
                "local_date": local_date,
                "sport": sport,
                "name": name,
                "source_text": source_text,
                "scheduled_time": scheduled_time,
                "duration_minutes": duration_minutes,
                "steps": steps,
                "comment": comment,
                "frequency": frequency,
                "interval_days": interval_days,
                "count": count,
                "until": until,
            }
        ),
    )
    return dict(response.json())


@mcp.tool()
async def create_planned_race(
    local_date: str,
    name: str,
    distance_m: float,
    sport: str = "running",
    scheduled_time: str | None = None,
    target_duration_s: float | None = None,
) -> dict[str, Any]:
    """Add a race to the calendar (distance in metres, optional goal finish time in seconds).
    Returns it with days_until and a predicted finish time when the distance is standard."""
    response = await _call_api(
        "POST",
        "/api/v1/planned-races",
        json=_drop_none(
            {
                "local_date": local_date,
                "name": name,
                "sport": sport,
                "distance_m": distance_m,
                "scheduled_time": scheduled_time,
                "target_duration_s": target_duration_s,
            }
        ),
    )
    return dict(response.json())


@mcp.tool()
async def update_planned_race(
    race_id: int,
    local_date: str,
    name: str,
    distance_m: float,
    sport: str = "running",
    scheduled_time: str | None = None,
    target_duration_s: float | None = None,
) -> dict[str, Any]:
    """Replace a race's fields wholesale (pass the full desired content)."""
    response = await _call_api(
        "PUT",
        f"/api/v1/planned-races/{race_id}",
        json=_drop_none(
            {
                "local_date": local_date,
                "name": name,
                "sport": sport,
                "distance_m": distance_m,
                "scheduled_time": scheduled_time,
                "target_duration_s": target_duration_s,
            }
        ),
    )
    return dict(response.json())


@mcp.tool()
async def delete_planned_race(race_id: int) -> None:
    """Remove a race from the calendar."""
    await _call_api("DELETE", f"/api/v1/planned-races/{race_id}")


@mcp.tool()
async def set_goal(
    period_type: str,
    period_start: str,
    target_distance_m: float,
    sport: str | None = None,
) -> dict[str, Any]:
    """Set (or replace) the distance goal for one year ("YYYY") or month ("YYYY-MM"), in metres;
    sport=None means every sport combined. One goal per period -- setting again replaces it."""
    response = await _call_api(
        "PUT",
        "/api/v1/goals",
        json=_drop_none(
            {
                "period_type": period_type,
                "period_start": period_start,
                "sport": sport,
                "target_distance_m": target_distance_m,
            }
        ),
    )
    return dict(response.json())


@mcp.tool()
async def delete_goal(goal_id: int) -> None:
    """Delete a distance goal by id (from set_goal's response)."""
    await _call_api("DELETE", f"/api/v1/goals/{goal_id}")


@mcp.tool()
async def get_bouldering_goals(period_type: str, period_start: str) -> list[dict[str, Any]]:
    """Every bouldering goal set for one week, month or year, each with its progress. `period_type`
    is "week" (period_start = the ISO date the 7-day week starts on), "month" ("YYYY-MM") or
    "year" ("YYYY"). A goal counts COMPLETED routes only, of one V-grade (optionally "or harder")
    or of any grade; a period can hold several. Empty list when none is set."""
    response = await _call_api(
        "GET",
        "/api/v1/bouldering-goals",
        params={"period_type": period_type, "period_start": period_start},
    )
    return list(response.json())


@mcp.tool()
async def create_bouldering_goal(
    period_type: str,
    period_start: str,
    target_count: int,
    grade: int | None = None,
    and_harder: bool = False,
) -> dict[str, Any]:
    """Set a bouldering goal: `target_count` completed routes in a week ("week", period_start = the
    ISO start date), month ("month", "YYYY-MM") or year ("year", "YYYY"). `grade` is the V-grade
    number (4 = V4); omit it to count routes of any grade; `and_harder` makes it "that grade or
    harder". e.g. 10x V4 in 2026: period_type="year", period_start="2026", grade=4,
    target_count=10. Several goals may share one period, but an identical one (same period, grade
    and and_harder) is refused -- change its target with update_bouldering_goal."""
    response = await _call_api(
        "POST",
        "/api/v1/bouldering-goals",
        json=_drop_none(
            {
                "period_type": period_type,
                "period_start": period_start,
                "grade": grade,
                "and_harder": and_harder,
                "target_count": target_count,
            }
        ),
    )
    return dict(response.json())


@mcp.tool()
async def update_bouldering_goal(
    goal_id: int,
    period_type: str,
    period_start: str,
    target_count: int,
    grade: int | None = None,
    and_harder: bool = False,
) -> dict[str, Any]:
    """Replace a bouldering goal (by id, from create_bouldering_goal/get_bouldering_goals) -- send
    every field, same meaning as create_bouldering_goal."""
    response = await _call_api(
        "PUT",
        f"/api/v1/bouldering-goals/{goal_id}",
        json=_drop_none(
            {
                "period_type": period_type,
                "period_start": period_start,
                "grade": grade,
                "and_harder": and_harder,
                "target_count": target_count,
            }
        ),
    )
    return dict(response.json())


@mcp.tool()
async def delete_bouldering_goal(goal_id: int) -> None:
    """Delete a bouldering goal by id."""
    await _call_api("DELETE", f"/api/v1/bouldering-goals/{goal_id}")


@mcp.tool()
async def create_blood_test_result(
    local_date: str,
    marker: str,
    value_num: float,
    unit: str | None = None,
    reference_low: float | None = None,
    reference_high: float | None = None,
    lab_name: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Record one lab marker for a draw date. reference_low/high are the athlete's own
    lab-reported range (never invented)."""
    response = await _call_api(
        "POST",
        "/api/v1/blood-tests",
        json=_drop_none(
            {
                "local_date": local_date,
                "marker": marker,
                "value_num": value_num,
                "unit": unit,
                "reference_low": reference_low,
                "reference_high": reference_high,
                "lab_name": lab_name,
                "notes": notes,
            }
        ),
    )
    return dict(response.json())


@mcp.tool()
async def create_blood_test_panel(
    local_date: str,
    results: list[dict[str, Any]],
    lab_name: str | None = None,
    notes: str | None = None,
) -> list[dict[str, Any]]:
    """Record a whole panel at once: one draw date plus results, each
    {"marker": str, "value_num": float, "unit": str?, "reference_low": float?,
    "reference_high": float?}."""
    response = await _call_api(
        "POST",
        "/api/v1/blood-tests/batch",
        json=_drop_none(
            {"local_date": local_date, "lab_name": lab_name, "notes": notes, "results": results}
        ),
    )
    return list(response.json())


@mcp.tool()
async def update_blood_test_result(
    result_id: int,
    local_date: str,
    marker: str,
    value_num: float,
    unit: str | None = None,
    reference_low: float | None = None,
    reference_high: float | None = None,
    lab_name: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Replace one blood-test result's fields wholesale."""
    response = await _call_api(
        "PUT",
        f"/api/v1/blood-tests/{result_id}",
        json=_drop_none(
            {
                "local_date": local_date,
                "marker": marker,
                "value_num": value_num,
                "unit": unit,
                "reference_low": reference_low,
                "reference_high": reference_high,
                "lab_name": lab_name,
                "notes": notes,
            }
        ),
    )
    return dict(response.json())


@mcp.tool()
async def delete_blood_test_result(result_id: int) -> None:
    """Delete one blood-test result."""
    await _call_api("DELETE", f"/api/v1/blood-tests/{result_id}")


@mcp.tool()
async def delete_blood_test_panel(local_date: str) -> None:
    """Delete every blood-test result drawn on one ISO date."""
    await _call_api("DELETE", f"/api/v1/blood-tests/by-date/{local_date}")


@mcp.tool()
async def create_shoe(
    brand: str,
    model: str,
    size: str | None = None,
    comments: str | None = None,
    initial_distance_km: float = 0.0,
    max_distance_km: float | None = 800.0,
) -> dict[str, Any]:
    """Add a shoe pair (initial_distance_km = mileage already on it; max_distance_km=None for no
    replacement limit)."""
    response = await _call_api(
        "POST",
        "/api/v1/gear/shoes",
        json={
            "brand": brand,
            "model": model,
            "size": size,
            "comments": comments,
            "initial_distance_km": initial_distance_km,
            "max_distance_km": max_distance_km,
        },
    )
    return dict(response.json())


@mcp.tool()
async def set_default_shoe(sport: str, shoe_id: str) -> dict[str, Any]:
    """Make a pair the default for a sport from now on (never reassigns past activities)."""
    response = await _call_api("PUT", f"/api/v1/gear/defaults/{sport}", json={"shoe_id": shoe_id})
    return dict(response.json())


@mcp.tool()
async def retire_shoe(shoe_id: str) -> dict[str, Any]:
    """Retire a pair (history kept; clears it as any sport's default; stops alerts)."""
    response = await _call_api("PUT", f"/api/v1/gear/shoes/{shoe_id}/retire")
    return dict(response.json())


@mcp.tool()
async def set_activity_shoe(activity_id: str, shoe_id: str | None) -> dict[str, Any]:
    """Assign a shoe pair to one distance-bearing activity; None clears the explicit choice
    (falling back to the sport default)."""
    response = await _call_api(
        "PUT", f"/api/v1/gear/activities/{activity_id}/shoe", json={"shoe_id": shoe_id}
    )
    return dict(response.json())


@mcp.tool()
async def set_activity_sport(
    activity_id: str, sport: str, sub_sport: str | None = None
) -> dict[str, Any]:
    """Correct an activity's sport/sub_sport when the source recorded the wrong one. Durable
    across a full database rebuild."""
    response = await _call_api(
        "PATCH",
        f"/api/v1/activities/{activity_id}/sport",
        json=_drop_none({"sport": sport, "sub_sport": sub_sport}),
    )
    return dict(response.json())


@mcp.tool()
async def set_activity_race(activity_id: str, is_race: bool) -> dict[str, Any]:
    """Mark or unmark an activity as a race."""
    response = await _call_api(
        "PATCH", f"/api/v1/activities/{activity_id}/race", json={"is_race": is_race}
    )
    return dict(response.json())


@mcp.tool()
async def set_activity_name(activity_id: str, name: str) -> dict[str, Any]:
    """Give an activity a custom title."""
    response = await _call_api(
        "PATCH", f"/api/v1/activities/{activity_id}/name", json={"name": name}
    )
    return dict(response.json())


@mcp.tool()
async def set_activity_fueling(
    activity_id: str,
    carbohydrates_g: float | None = None,
    sodium_mg: float | None = None,
) -> dict[str, Any]:
    """Record carbohydrate/sodium intake for an activity. Both fields are set together, so pass
    the current value of whichever you're not changing."""
    response = await _call_api(
        "PATCH",
        f"/api/v1/activities/{activity_id}/fueling",
        json={"carbohydrates_g": carbohydrates_g, "sodium_mg": sodium_mg},
    )
    return dict(response.json())


# The only non-/mcp paths the MCP sub-app is allowed to answer: the OAuth authorization server's
# own endpoints (served by the SDK). Everything else outside /mcp stays a plain 404.
_OAUTH_PATHS = frozenset({"/authorize", "/token", "/register", "/revoke"})
_OAUTH_WELL_KNOWN_PREFIX = "/.well-known/oauth-"


def _require_api_key_asgi(inner_app: ASGIApp) -> ASGIApp:
    """Fronts the MCP mount. Two credentials reach `/mcp`: an OAuth bearer token (validated by the
    SDK itself, see auth/oauth.py) or the shared `X-API-Key` header this wrapper has always
    accepted -- a valid one is rewritten into an `Authorization: Bearer` header so the SDK's own
    auth middleware, which only understands bearer tokens, lets it through and Claude Code's
    header-based registration keeps working unchanged. `Mount`-ed sub-apps never reach FastAPI's
    own dependency injection, hence a raw ASGI wrapper. See ADR 0007 decisions 5 and 10.
    """

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await inner_app(scope, receive, send)
            return
        path = scope["path"]
        if path in _OAUTH_PATHS or path.startswith(_OAUTH_WELL_KNOWN_PREFIX):
            await inner_app(scope, receive, send)
            return
        # This wrapper sits behind `app.mount("/", mcp_asgi_app)` (main.py) -- Starlette's Mount
        # catches every request FastAPI's own routers didn't match, not just ones actually meant
        # for the MCP tool surface. Without this check, ANY unmatched path (a typo'd frontend
        # request, a route that failed to register on a stale/reloading process, a future bug)
        # gets intercepted here and turned into a misleading "invalid API key" 401 instead of a
        # normal 404 -- which is exactly indistinguishable from a real auth failure to a client,
        # and was confirmed to cause a very confusing real incident: a JWT-authenticated frontend
        # session got silently logged out because one specific endpoint fell through to this
        # X-API-Key-only check, which JWT bearer tokens can never satisfy. Only /mcp and the
        # OAuth endpoints above should ever reach the auth logic below.
        if not path.startswith("/mcp"):
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return
        provided = dict(scope["headers"]).get(b"x-api-key", b"")
        if provided:
            api_key = get_settings().api_key
            if not api_key or not secrets.compare_digest(provided.decode(), api_key):
                await JSONResponse({"detail": "invalid or missing API key"}, status_code=401)(
                    scope, receive, send
                )
                return
            scope = {
                **scope,
                "headers": [
                    *((k, v) for k, v in scope["headers"] if k != b"authorization"),
                    (b"authorization", b"Bearer " + provided),
                ],
            }
        await inner_app(scope, receive, send)

    return wrapped


def build_mcp_asgi_app() -> ASGIApp:
    """Builds the mountable, auth-wrapped MCP ASGI app. Call once, before app startup --
    `streamable_http_app()` lazily creates the session manager `mcp_lifespan` below depends on.
    """
    return _require_api_key_asgi(mcp.streamable_http_app())


@asynccontextmanager
async def mcp_lifespan(_app: object) -> AsyncIterator[None]:
    """Enters the MCP session manager's own run() context. Must be composed into the parent
    FastAPI app's lifespan -- `Mount` doesn't propagate ASGI lifespan events into a mounted
    sub-app automatically (confirmed directly; see ADR 0007 decision 3). Must run after
    `build_mcp_asgi_app()` has been called at least once (api/main.py sequences these
    correctly: build+mount at import time, this lifespan entered at app startup).
    """
    async with mcp.session_manager.run():
        yield
