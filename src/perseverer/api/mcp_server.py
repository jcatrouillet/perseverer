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
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from perseverer.config import get_settings

mcp = FastMCP(
    "Perseverer",
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
    response = await _call_api(
        "GET", f"/api/v1/activities/{activity_id}/stream", params=params
    )
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


def _require_api_key_asgi(inner_app: ASGIApp) -> ASGIApp:
    """Wraps the MCP mount with the same shared-secret gate every REST route has via
    `Depends(require_api_key)` -- `Mount`-ed sub-apps never reach FastAPI's own dependency
    injection, so this is a raw ASGI equivalent. See ADR 0007 decision 5.
    """

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
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
        # X-API-Key-only check, which JWT bearer tokens can never satisfy. Only requests actually
        # under /mcp should ever reach the auth check below.
        if not scope["path"].startswith("/mcp"):
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return
        settings = get_settings()
        headers = dict(scope["headers"])
        provided = headers.get(b"x-api-key", b"").decode()
        if not settings.api_key:
            await JSONResponse({"detail": "API key not configured"}, status_code=503)(
                scope, receive, send
            )
            return
        if not secrets.compare_digest(provided, settings.api_key):
            await JSONResponse({"detail": "invalid or missing API key"}, status_code=401)(
                scope, receive, send
            )
            return
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
