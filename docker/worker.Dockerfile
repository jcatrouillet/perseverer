# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

# See api.Dockerfile — same x86-64-v2 cap rationale.
ENV CFLAGS="-march=x86-64-v2 -mtune=generic" \
    CXXFLAGS="-march=x86-64-v2 -mtune=generic" \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY config ./config
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin sporthealth
WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/src /app/src
COPY --from=builder /build/config /app/config

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER sporthealth

# No scheduled jobs yet (Phase 0 placeholder) — see sporthealth/worker/main.py.
CMD ["python", "-m", "sporthealth.worker.main"]
