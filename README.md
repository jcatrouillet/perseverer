# Perseverer

Self-hosted fitness & health data platform — own the Garmin/Strava data, stop depending on a
vendor cloud. Runs on `bercy` (an Intel NUC6i55SYH, Ubuntu Server, rootless Podman) behind an
existing reverse proxy.

See [`AGENTS.md`](AGENTS.md) for the architecture summary and non-negotiable invariants,
[`docs/DEPLOY.md`](docs/DEPLOY.md) for the Windows → NAS deploy runbook, and
[`docs/adr/`](docs/adr/) for the decision record, one ADR per phase.

## Quickstart (dev, Windows + Podman Desktop)

```bash
cp .env.example .env
podman compose up --build
curl http://localhost:8008/api/v1/healthz
```
