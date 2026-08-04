"""FastAPI application entrypoint.

Phase 3 adds the read API + notes write path (see
docs/adr/0006-phase-3-read-api-and-rollups.md): activities, streams, health observations,
sleep, the rollup-backed calendar, and notes. Every data route requires the `X-API-Key` header
(see api/dependencies.py::require_api_key) except /healthz and /version, which stay
unauthenticated so the Dockerfile's existing healthcheck keeps working unmodified.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sporthealth import __version__
from sporthealth.api.routers import activities, calendar, health, notes, sleep
from sporthealth.config import get_settings

app = FastAPI(title="Sport Health Data Platform API", version=__version__)

_settings = get_settings()
if _settings.cors_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins_list,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Key", "Content-Type"],
        allow_credentials=False,
    )

app.include_router(activities.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(sleep.router, prefix="/api/v1")
app.include_router(calendar.router, prefix="/api/v1")
app.include_router(notes.router, prefix="/api/v1")


@app.get("/api/v1/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/version")
def version() -> dict[str, str]:
    settings = get_settings()
    return {"version": __version__, "environment": settings.environment}
