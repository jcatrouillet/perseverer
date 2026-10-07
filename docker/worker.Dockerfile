# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

# See api.Dockerfile — same x86-64-v2 cap rationale.
ENV CFLAGS="-march=x86-64-v2 -mtune=generic" \
    CXXFLAGS="-march=x86-64-v2 -mtune=generic" \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

# Built at /app, matching the runtime stage's WORKDIR exactly — see api.Dockerfile for why
# (uv bakes the venv's absolute path into .venv/bin/ script shebangs).
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY config ./config
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
# rsync + ssh client: backup.py shells out to both for the daily backup job.
# Neither ships in the slim base image. No server-side sshd needed here -- this container only
# ever connects *out* to the backup host, never accepts inbound connections.
RUN apt-get update && apt-get install -y --no-install-recommends rsync openssh-client \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin perseverer
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/config /app/config

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    # See api.Dockerfile's own comment -- same ReadOnly=true Quadlet unit, same reasoning.
    PYTHONDONTWRITEBYTECODE=1

USER perseverer

# Daily garmin_connect/Eufy sync + daily backup, each on its own cron schedule -- see
# perseverer/worker/main.py.
CMD ["python", "-m", "perseverer.worker.main"]
