"""MCP (Model Context Protocol) server exposing the read API + notes as tools -- the write
path CLAUDE.md's mission statement calls for ("a REST/JSON API an AI agent can write notes
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
async def get_activity_stream(activity_id: str) -> dict[str, Any]:
    """A coarse (~200-point) time-series shape for one activity (heart rate, pace, etc) --
    always the "low" resolution tier regardless of the activity's actual sample rate, since
    full per-second data doesn't belong in an agent's context window. See ADR 0007 decision 7.
    """
    response = await _call_api(
        "GET", f"/api/v1/activities/{activity_id}/stream", params={"tier": "low"}
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
