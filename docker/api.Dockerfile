# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

# x86-64-v2 (SSE4.2, no AVX/AVX2) keeps the image runnable on older CPUs. This only matters
# if a dependency has no manylinux wheel for this platform and pip/uv falls back to a source build;
# numpy/pyarrow/duckdb ship wheels that runtime-dispatch and ignore these flags. See
# docs/ARCHITECTURE.md and the CI avx-smoke job.
ENV CFLAGS="-march=x86-64-v2 -mtune=generic" \
    CXXFLAGS="-march=x86-64-v2 -mtune=generic" \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

# Built at /app, matching the runtime stage's WORKDIR exactly: uv bakes the venv's absolute
# path into the shebang lines under .venv/bin/, so building at a different path than where
# it's later copied (e.g. /build vs /app) leaves those scripts pointing at a path that
# doesn't exist in the runtime image.
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY config ./config
RUN uv sync --frozen --no-dev --no-editable

# Bakes DuckDB's "sqlite" extension in at build time, using the same duckdb version `uv sync`
# just installed. `INSTALL` fetches over the network on first use, and the production container
# has no reason to have outbound internet and images are never built there (see AGENTS.md) — this
# must happen here, not on first request in production. See
# docs/ARCHITECTURE.md.
RUN /app/.venv/bin/python -c "\
import duckdb; \
duckdb.connect(':memory:', config={'extension_directory': '/app/.duckdb_extensions'}).execute('INSTALL sqlite')"

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin perseverer
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/config /app/config
COPY --from=builder /app/.duckdb_extensions /app/.duckdb_extensions
# Migrations were never runnable against a containerized deploy's data volume until now --
# alembic itself was already a runtime dependency (uv sync pulled it in), but alembic.ini and
# the migration scripts themselves were never copied into the image, so there was nothing to
# point it at. Lets a real deploy run `podman exec perseverer-api alembic upgrade head` (or the
# --no-dev worker image via the same COPY, if it's ever asked to run one) instead of needing a
# separate host-side alembic invocation against a bind-mounted volume.
COPY alembic.ini ./
COPY alembic ./alembic

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    # Hardening: the Quadlet unit runs this with ReadOnly=true, so a .pyc
    # write attempt under /app on first import would just silently fail anyway (CPython catches
    # that and proceeds uncached, never fatal) -- this skips the attempt outright instead of
    # relying on that fallback.
    PYTHONDONTWRITEBYTECODE=1 \
    PERSEVERER_DUCKDB_EXTENSION_DIR=/app/.duckdb_extensions

USER perseverer
EXPOSE 8000

# 2 workers, not cpu_count(): a small, predictable footprint for a single-household app.
# Shared state (lockout, OAuth, MCP) lives in SQLite, so any worker count is safe.
CMD ["uvicorn", "perseverer.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
