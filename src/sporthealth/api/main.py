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
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sporthealth import __version__
from sporthealth.api.mcp_server import build_mcp_asgi_app, mcp_lifespan
from sporthealth.api.routers import activities, auth, calendar, fitness, health, notes, sleep
from sporthealth.config import get_settings

mcp_asgi_app = build_mcp_asgi_app()

app = FastAPI(title="Sport Health Data Platform API", version=__version__, lifespan=mcp_lifespan)

_settings = get_settings()
if _settings.cors_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins_list,
        allow_methods=["GET", "POST"],
        # Authorization added in Phase 5 for JWT bearer-token login (ADR 0008) -- X-API-Key
        # and Content-Type predate it (ADR 0006 decision 8).
        allow_headers=["X-API-Key", "Authorization", "Content-Type"],
        allow_credentials=False,
    )

app.include_router(auth.router, prefix="/api/v1")
app.include_router(activities.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(sleep.router, prefix="/api/v1")
app.include_router(calendar.router, prefix="/api/v1")
app.include_router(fitness.router, prefix="/api/v1")
app.include_router(notes.router, prefix="/api/v1")


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
