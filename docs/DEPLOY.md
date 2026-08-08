# Deploy runbook: Windows dev → Synology DS1019+ production

This is a living document — Phase 0 lays out the shape; Phase 3 fills in the reverse-proxy
specifics once auth and public exposure actually exist; later phases add the backup/restore
and observability pieces as they're built.

## Environments

| | Dev | Production |
|---|---|---|
| Host | Windows 10, Podman Desktop | Synology DS1019+, DSM 7.x Container Manager (Docker) |
| CPU | whatever your dev machine has | Intel Celeron J3455 (Goldmont, **no AVX/AVX2**, 4 cores, 1.5GHz) |
| RAM budget | not constrained | ~1.5GB for the whole stack (8GB total, shared with DSM) |
| Build location | here | **never** — see below |
| Ingress | `localhost:<port>` | DSM reverse proxy only, TLS-terminated |

## Golden rule: never build on the NAS

A Vite build or a Python wheel compile on a J3455 is measured in double-digit minutes. All
images are built on Windows (manually, via Podman) or in GitHub Actions (CI), tagged, and
pushed to **GHCR** (`ghcr.io/<owner>/my-sport-health-data-{api,worker,frontend}`). The NAS
only ever pulls, using Docker (DSM Container Manager) — the two engines never need to
interoperate directly, only agree on the OCI image format, which they do.

## Dev loop (Windows, Podman Desktop)

**One-time machine setup — this is required, not optional:** the Podman machine must be
created with **User-Mode Networking**, or published container ports never reach Windows
`localhost` at all (containers run fine and are reachable via the VM's own IP, but
`localhost:<port>` from Windows just hangs/refuses — this cost significant debugging time to
track down, see the "what actually broke" section of
`docs/adr/0001-phase-0-foundations.md` decision 8). If Podman Desktop's machine wasn't created
with this from the start:

```bash
podman machine stop
podman machine rm podman-machine-default
podman machine init podman-machine-default --user-mode-networking
podman machine start
```

If the machine's first start after `init` fails to connect (`ssh: rejected: connect failed`),
it's a known WSL2 cgroup race on first boot — `podman machine stop`, then `wsl --shutdown`,
then `podman machine start` again clears it.

```bash
cp .env.example .env       # adjust DEV_* ports if they collide with something else
podman compose up --build
curl http://localhost:8008/api/v1/healthz
```

`compose.yaml`/`compose.override.yml`/`compose.nas.yml` are plain Compose Specification files
with no Docker-only extensions, so `podman compose` works the same way `docker compose` would
— it auto-merges `compose.yaml` + `compose.override.yml`, no `-f` flags needed. The override
publishes the API on `DEV_API_PUBLISHED_PORT` (default 8008 — deliberately not 8000, which
collided with an unrelated local process) and the frontend on
`DEV_FRONTEND_PUBLISHED_PORT` (default 5173). There is no live-reload wired up yet (Phase 0
scope is a health check, not a dev loop optimized for iteration) — rebuild with
`podman compose up --build` after code changes. Frontend work (Phase 5+) runs `npm run dev`
directly on the host instead of through the container, for HMR
(`cd frontend && npm install && npm run dev`). It uses the committed
`frontend/public/config.js` dev default (`http://localhost:8008`) unchanged, and needs
`SPORTHEALTH_CORS_ALLOWED_ORIGINS` in `.env` to include the Vite dev server's origin (default
`http://localhost:5173`; Vite falls back to `5174`, `5175`, ... if that port is taken, so add
whichever it actually reports) or the browser blocks every request at the CORS preflight step.

If you ever need the Docker CLI locally (e.g. to sanity-check an image before it reaches the
NAS), Podman Desktop can also emulate the `docker` command; not required for the dev loop
above.

**Known issue: the podman `api` container's named volume corrupts `sporthealth.db` on
Windows.** Confirmed via `PRAGMA integrity_check`/`page_count`: the file gets silently
truncated within seconds of the `api` container starting, even from a hash-verified clean
copy with no stale `-wal`/`-shm` files — `db/engine.py` only sets standard WAL-mode pragmas,
so this points at Podman Desktop's Windows volume backend mishandling SQLite WAL's mmap/
locking, not application code. The host file itself is never affected, only the container's
copy. For day-to-day API verification, run the API directly on the host instead — it serves
the same port the frontend's `config.js` already points at, so the already-running frontend
container (or `npm run dev`) works against it with zero changes:
```bash
uv run uvicorn sporthealth.api.main:app --host 0.0.0.0 --port 8008
```
Reserve `podman compose up --build` (api container included) for occasionally confirming the
container still builds/runs — not for iterative dev work. Unconfirmed whether this reproduces
on the NAS (native Linux/Docker storage, not the same virtualized volume backend) — treat as
Windows-dev-specific until shown otherwise.

## NAS deploy

The NAS runs Docker (DSM Container Manager), not Podman — this step is unaffected by the dev
engine choice.

```bash
# one-time: authenticate the NAS's docker CLI/Container Manager to pull from GHCR
docker compose -f compose.yaml -f compose.nas.yml pull
docker compose -f compose.yaml -f compose.nas.yml up -d
```

`compose.nas.yml`:
- Publishes **no ports to the host** — the DSM reverse proxy is the sole ingress.
- Sets `user: ${NAS_UID}:${NAS_GID}` on `api`/`worker` so they can write to the bind-mounted
  data directory without running as root.
- Caps `worker` at `cpus: 1.0` (ingestion must never starve the API or DSM) and each service's
  `mem_limit` so the whole stack stays inside the ~1.5GB budget.

### One-time host setup

1. Create the data directory on a DSM shared folder, e.g. `/volume1/docker/sporthealth/data`.
2. `chown` it to the UID/GID the containers run as (`NAS_UID`/`NAS_GID` in `.env` — default
   1000:1000, confirm against whatever DSM assigns your Docker user):
   ```bash
   sudo chown -R 1000:1000 /volume1/docker/sporthealth/data
   ```
   Synology's shared-folder permissions and the container's UID must agree, or writes
   (SQLite, raw archive, Parquet) will fail silently into a read-only-feeling mount.
3. Copy `.env.example` to `.env` on the NAS and fill in `NAS_DATA_DIR`, `NAS_UID`, `NAS_GID`,
   `GHCR_IMAGE_PREFIX`, `IMAGE_TAG`.

## Reverse proxy, TLS, and the double-NAT/split-horizon-DNS constraint (Phase 3+)

You already run DSM's built-in reverse proxy with a Let's Encrypt certificate issued via
DNS-01 — this stack reuses that certificate and proxy rather than adding a second ACME client
or terminating TLS in the app containers. The app containers bind to the compose network only
and are never published directly to the host.

Your network is double-NAT (ISP router + UniFi) and internal access relies on split-horizon
DNS, not hairpin NAT — **the public hostname does not necessarily resolve the same way inside
and outside the house.** Consequences:
- The frontend's API base URL is runtime-configurable, never baked into the Vite build — see
  `docs/adr/0008-phase-5-frontend.md` decision 6. `frontend/public/config.js` sets
  `window.__SPORTHEALTH_CONFIG__.apiBaseUrl`; the built nginx image regenerates that file at
  **container start** (not build time) from a `SPORTHEALTH_API_BASE_URL` env var, via a script
  in `docker/frontend-entrypoint.d/` that runs through nginx's own stock
  `/docker-entrypoint.d/` startup mechanism. Set `SPORTHEALTH_API_BASE_URL` to whichever
  hostname/URL resolves correctly for wherever the browser actually is — this is a per-deployment
  value, not a build-time constant, exactly because of the split-horizon DNS behavior above.
- `X-Forwarded-*` headers are trusted **only** from the known reverse-proxy IP (configured
  explicitly) — trusting them from anywhere else makes rate limiting and audit logs trivially
  spoofable, and source-IP trust is meaningless once everything arrives from the proxy anyway.

**Resolved (Phase 5):** the UI is reachable more broadly, not gated behind Tailscale — it has
real per-athlete authentication instead (password login issuing a JWT, or a standing per-athlete
API key; see ADR 0008). `sync athlete set-password` / `sync athlete create-key` provision an
athlete's credentials.

TODO (still open): document the exact DSM reverse-proxy rule (hostname → `frontend`/`api`
service + port), the header-forwarding configuration, and the certificate path — this needs the
user's actual DSM configuration, not something decidable from this repo alone. Also still open:
there is currently no migration step for the containerized deploy's data volume in this runbook
at all (`docker compose ... up -d` alone does not run `alembic upgrade head` against it) — a gap
this phase found but did not fix, since it predates Phase 5 and isn't specific to it.

## One-time backfills

- **Phase 6 `activity.local_date` offset fix** (ADR 0009 decision 8): any database populated
  before this fix has activity rows whose `local_date` is the raw UTC calendar date instead of
  the offset-adjusted one — wrong for any athlete not on UTC. Run
  `scripts/backfill_local_date_offset.py` once against that database (stop the API container
  first to avoid a concurrent-write race, since it opens the SQLite file directly) — it recomputes
  `local_date` from the already-correct stored `utc_offset_s` (no FIT re-parsing) and refreshes
  every rollup table for every date/period it touched. Safe to re-run (idempotent: a
  database already on the offset-adjusted convention has nothing left to change). Not needed for
  a fresh install — new ingests already use the fixed convention.

## Backups (Phase 9)

TODO: `VACUUM INTO` snapshot schedule, rsync of the raw archive + Parquet trees, and an
automated restore-from-backup test wired into CI. Not built yet — recorded here so the shape
of this section doesn't get forgotten.
