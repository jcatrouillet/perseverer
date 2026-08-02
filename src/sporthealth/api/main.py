"""FastAPI application entrypoint.

Phase 0 scope only: /healthz and /version. No auth, no data endpoints yet — those arrive in
Phase 3 (§7 of the project spec) once the schema and ingestion pipeline exist.
"""

from fastapi import FastAPI

from sporthealth import __version__
from sporthealth.config import get_settings

app = FastAPI(title="Sport Health Data Platform API", version=__version__)


@app.get("/api/v1/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/version")
def version() -> dict[str, str]:
    settings = get_settings()
    return {"version": __version__, "environment": settings.environment}
