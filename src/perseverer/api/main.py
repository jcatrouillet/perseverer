"""FastAPI application entrypoint.

Phase 3 added the read API + notes write path (see
docs/adr/0006-phase-3-read-api-and-rollups.md): activities, streams, health observations,
sleep, the rollup-backed calendar, and notes. Every data route requires the `X-API-Key` header
(see api/dependencies.py::require_api_key) except /healthz and /version, which stay
unauthenticated so the Dockerfile's existing healthcheck keeps working unmodified.

Phase 4 mounts an MCP (Model Context Protocol) server at `/mcp`, exposing that same API as
tools for an AI agent -- see docs/adr/0007-phase-4-mcp-server.md. It's mounted into this same
app/container rather than a separate service (a deliberate deviation from ADR 0001's Phase-0
guess), gated by the same shared API key via a raw ASGI wrapper (`Mount` bypasses FastAPI's own
`Depends`), and requires composing its session-manager lifespan into this app's own lifespan --
`streamable_http_app()` must be called (building `mcp_asgi_app` below) before `mcp_lifespan` is
entered.

`GET /share/{token}` (share.py) and `GET /share/calendar/{token}.ics` (calendar_feed.py) are the
other deliberately unauthenticated routes besides /healthz/version/auth/login -- an athlete-issued
link/feed viewable by anyone who has it, with no X-API-Key/JWT at all. See each module's own
docstring.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from perseverer import __version__
from perseverer.api.mcp_server import build_mcp_asgi_app, mcp_lifespan
from perseverer.api.routers import (
    activities,
    auth,
    blood_tests,
    calendar,
    calendar_feed,
    fitness,
    gear,
    goals,
    health,
    insights,
    notes,
    oauth,
    performance,
    planned_races,
    planned_workouts,
    share,
    sleep,
    weather_forecast,
)
from perseverer.api.routers import settings as settings_router
from perseverer.config import get_settings

mcp_asgi_app = build_mcp_asgi_app()

app = FastAPI(title="Perseverer API", version=__version__, lifespan=mcp_lifespan)

_settings = get_settings()
if _settings.cors_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins_list,
        # PATCH added for the manual activity-correction endpoints (sport/race overrides,
        # ADR 0012/this session) -- missing originally, which silently broke both from any real
        # browser tab (curl bypasses CORS preflight entirely, which is why this went unnoticed).
        # PUT added for the settings/hr-zones endpoint, same class of bug if omitted. DELETE
        # added for the bouldering manual-route endpoint (ADR 0012 bouldering-view redesign) --
        # same bug again, caught this time by exercising the delete affordance in a real browser
        # tab rather than trusting curl/pytest alone.
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        # Authorization added in Phase 5 for JWT bearer-token login (ADR 0008) -- X-API-Key
        # and Content-Type predate it (ADR 0006 decision 8).
        allow_headers=["X-API-Key", "Authorization", "Content-Type"],
        allow_credentials=False,
    )

app.include_router(auth.router, prefix="/api/v1")
app.include_router(activities.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(blood_tests.router, prefix="/api/v1")
app.include_router(sleep.router, prefix="/api/v1")
app.include_router(calendar.router, prefix="/api/v1")
app.include_router(fitness.router, prefix="/api/v1")
app.include_router(gear.router, prefix="/api/v1")
app.include_router(performance.router, prefix="/api/v1")
app.include_router(insights.router, prefix="/api/v1")
app.include_router(notes.router, prefix="/api/v1")
app.include_router(goals.router, prefix="/api/v1")
app.include_router(planned_workouts.router, prefix="/api/v1")
app.include_router(planned_races.router, prefix="/api/v1")
app.include_router(weather_forecast.router, prefix="/api/v1")
app.include_router(settings_router.router, prefix="/api/v1")
app.include_router(share.management_router, prefix="/api/v1")
# No prefix, no auth -- the one deliberately public surface in this app besides
# /healthz//version/auth/login. See share.py's own module docstring for why (and how the
# reverse proxy/nginx get a same-origin /share/{token} URL to this route at all).
app.include_router(share.router)
app.include_router(oauth.router)  # /oauth/login -- the MCP OAuth consent page (public by design)
# GET /share/calendar/{token}.ics -- a second, structurally distinct public route (one standing
# per-athlete secret + text/calendar, not a one-off HTML share) reusing the same /share/ nginx
# prefix rule. See calendar_feed.py's own module docstring.
app.include_router(calendar_feed.router, prefix="/share")


@app.get("/api/v1/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/version")
def version() -> dict[str, str]:
    settings = get_settings()
    return {"version": __version__, "environment": settings.environment}


# Mounted last so the more specific /api/v1/... routes above are matched first; the MCP
# sub-app's own internal route only resolves at /mcp regardless.
app.mount("/", mcp_asgi_app)
