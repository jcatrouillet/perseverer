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

```bash
cp .env.example .env       # adjust DEV_* ports if they collide with something else
podman compose up --build
curl http://localhost:8000/api/v1/healthz
```

`compose.yaml`/`compose.override.yml`/`compose.nas.yml` are plain Compose Specification files
with no Docker-only extensions, so `podman compose` works the same way `docker compose` would
— it auto-merges `compose.yaml` + `compose.override.yml`, no `-f` flags needed. The override
publishes the API on `DEV_API_PUBLISHED_PORT` (default 8000) and the frontend on
`DEV_FRONTEND_PUBLISHED_PORT` (default 5173). There is no live-reload wired up yet (Phase 0
scope is a health check, not a dev loop optimized for iteration) — rebuild with
`podman compose up --build` after code changes. Frontend work in Phase 5+ will likely run
`npm run dev` directly on the host instead of through the container, for HMR.

If you ever need the Docker CLI locally (e.g. to sanity-check an image before it reaches the
NAS), Podman Desktop can also emulate the `docker` command; not required for the dev loop
above.

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
and outside the house.** Consequences that Phase 3 must respect:
- The frontend's API base URL must be runtime-configurable, never baked into the Vite build.
- `X-Forwarded-*` headers are trusted **only** from the known reverse-proxy IP (configured
  explicitly) — trusting them from anywhere else makes rate limiting and audit logs trivially
  spoofable, and source-IP trust is meaningless once everything arrives from the proxy anyway.

TODO (Phase 3, when auth + public exposure are actually built): document the exact DSM
reverse-proxy rule (hostname → `frontend`/`api` service + port), the header-forwarding
configuration, and the certificate path. Also raise, and record the decision on, whether the
human-facing UI should sit behind Tailscale while only the API-key surface is public.

## Backups (Phase 9)

TODO: `VACUUM INTO` snapshot schedule, rsync of the raw archive + Parquet trees, and an
automated restore-from-backup test wired into CI. Not built yet — recorded here so the shape
of this section doesn't get forgotten.
