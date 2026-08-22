# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

# The DS1019+ (Celeron J3455 / Goldmont) has no AVX/AVX2 — only up to SSE4.2. This only
# matters if a dependency has no manylinux wheel for this platform and pip/uv falls back to
# a source build; numpy/pyarrow/duckdb ship wheels that runtime-dispatch and ignore these
# flags. See docs/adr/0001-phase-0-foundations.md and the CI avx-smoke job.
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
# just installed. `INSTALL` fetches over the network on first use, and the NAS container has
# no reason to have outbound internet and images are never built there (see CLAUDE.md) — this
# must happen here, not on first request in production. See
# docs/adr/0006-phase-3-read-api-and-rollups.md decision 4.
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

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PERSEVERER_DUCKDB_EXTENSION_DIR=/app/.duckdb_extensions

USER perseverer
EXPOSE 8000

# 2 workers, not cpu_count() — 8GB RAM total is shared with DSM (§4b).
CMD ["uvicorn", "perseverer.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
