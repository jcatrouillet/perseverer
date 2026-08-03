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

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin sporthealth
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/config /app/config

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER sporthealth
EXPOSE 8000

# 2 workers, not cpu_count() — 8GB RAM total is shared with DSM (§4b).
CMD ["uvicorn", "sporthealth.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
