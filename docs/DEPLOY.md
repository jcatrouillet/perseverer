# Deploy runbook: Windows dev → bercy production

This is a living document — Phase 0 lays out the shape; Phase 3 fills in the reverse-proxy
specifics once auth and public exposure actually exist; later phases add the backup/restore
and observability pieces as they're built.

**Production moved off the original Synology DS1019+ target onto `bercy`, an Intel NUC6i55SYH**
(Ubuntu Server 26.04 LTS, rootless Podman, systemd Quadlet units — see `quadlet/`) — the
Synology-specific runbook this document used to describe (DSM Container Manager, Docker,
`compose.nas.yml`, the Celeron J3455's no-AVX/AVX2 constraint) no longer applies to production.
It's kept only as prior art in git history if the NAS is ever pressed back into service for
something else.

## Environments

| | Dev | Production |
|---|---|---|
| Host | Windows 10, Podman Desktop | `bercy`: Intel NUC6i55SYH, Ubuntu Server 26.04 LTS, rootless Podman |
| CPU | whatever your dev machine has | Intel Core i5-6260U (Skylake, **has AVX/AVX2**, 4 threads, 1.8GHz) |
| RAM budget | not constrained | 32GB total — no meaningful budget pressure, unlike the old NAS |
| Build location | here | **never** — see below |
| Ingress | `localhost:<port>` | published to the host (`8000`/`8080`), fronted by your existing reverse proxy pointed at bercy's LAN IP |
| Orchestration | Compose (`compose.yaml` + `compose.override.yml`) | systemd Quadlet units (`quadlet/`), no Compose |

Since production hardware now has AVX2, the x86-64-v2 CFLAGS cap in the Dockerfiles and the
AVX-masked (Westmere) CI smoke test are no longer load-bearing for deployment — they're left in
place for now since they cost nothing and don't hurt, but are candidates to simplify away in a
future pass if the NAS is fully retired. Not done as part of this change to keep this rewrite
scoped to the deploy mechanism itself.

## Golden rule: never build on bercy

A Vite build or a Python wheel compile has no hard reason to avoid bercy's i5 the way it did the
J3455, but the "build on Windows/CI, only ever pull on the deploy host" split is kept anyway —
it's what keeps the deploy host's job simple (pull, verify a digest, restart) and reproducible
regardless of which machine ends up hosting production next. All images are built on Windows
(manually, via Podman) or in GitHub Actions (CI), tagged, and pushed to **GHCR**
(`ghcr.io/jcatrouillet/perseverer-{api,worker,frontend}`). bercy only ever pulls.

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

`compose.yaml`/`compose.override.yml` are plain Compose Specification files with no Docker-only
extensions, so `podman compose` works the same way `docker compose` would — it auto-merges the
two, no `-f` flags needed. The override
publishes the API on `DEV_API_PUBLISHED_PORT` (default 8008 — deliberately not 8000, which
collided with an unrelated local process) and the frontend on
`DEV_FRONTEND_PUBLISHED_PORT` (default 5173). There is no live-reload wired up yet (Phase 0
scope is a health check, not a dev loop optimized for iteration) — rebuild with
`podman compose up --build` after code changes. Frontend work (Phase 5+) runs `npm run dev`
directly on the host instead of through the container, for HMR
(`cd frontend && npm install && npm run dev`). It uses the committed
`frontend/public/config.js` dev default (`http://localhost:8008`) unchanged, and needs
`PERSEVERER_CORS_ALLOWED_ORIGINS` in `.env` to include the Vite dev server's origin (default
`http://localhost:5173`; Vite falls back to `5174`, `5175`, ... if that port is taken, so add
whichever it actually reports) or the browser blocks every request at the CORS preflight step.

If you ever need the Docker CLI locally (e.g. to sanity-check an image before it reaches
bercy), Podman Desktop can also emulate the `docker` command; not required for the dev loop
above.

**Known issue: the podman `api` container's named volume corrupts `perseverer.db` on
Windows.** Confirmed via `PRAGMA integrity_check`/`page_count`: the file gets silently
truncated within seconds of the `api` container starting, even from a hash-verified clean
copy with no stale `-wal`/`-shm` files — `db/engine.py` only sets standard WAL-mode pragmas,
so this points at Podman Desktop's Windows volume backend mishandling SQLite WAL's mmap/
locking, not application code. The host file itself is never affected, only the container's
copy. For day-to-day API verification, run the API directly on the host instead — it serves
the same port the frontend's `config.js` already points at, so the already-running frontend
container (or `npm run dev`) works against it with zero changes:
```bash
uv run uvicorn perseverer.api.main:app --host 0.0.0.0 --port 8008
```
Reserve `podman compose up --build` (api container included) for occasionally confirming the
container still builds/runs — not for iterative dev work. Unconfirmed whether this reproduces
on bercy (native Linux storage, not the same virtualized volume backend) — treat as
Windows-dev-specific until shown otherwise.

## bercy deploy (rootless Podman + Quadlet, no Compose)

`quadlet/*.container` are systemd Quadlet unit files — Podman's native systemd integration,
not Compose. Each `.container` file is a plain INI unit (`[Unit]`/`[Container]`/`[Service]`/
`[Install]`) that systemd's own `podman-user-generator` turns into a regular `systemctl --user`
service on `daemon-reload`. This runs entirely rootless: `prez`'s own user session, no root
daemon, no privileged install. See `quadlet/perseverer-api.container` for the design notes
(bind mount vs. named volume, `AutoUpdate=registry`, the shared env file) — `worker`/`frontend`
follow the same shape.

### One-time host setup

1. **Data directory + ownership.** The Quadlet units bind-mount `/home/prez/perseverer/data`
   (not a Podman-managed named volume — this project's whole ethos is an inspectable,
   directly-browsable archive, not something opaque under Podman's storage tree). The
   Dockerfiles' own `USER perseverer` (uid 1000 *inside* the container) needs the host directory
   owned to match under rootless Podman's user-namespace remapping — `podman unshare` runs a
   command inside that namespace so the uid arithmetic (subuid range, not literally "1000") is
   handled for you:
   ```bash
   mkdir -p /home/prez/perseverer/data
   podman unshare chown -R 1000:1000 /home/prez/perseverer/data
   ```
2. **Env file.** Copy `quadlet/perseverer.env.example` to
   `~/.config/containers/systemd/perseverer.env` on bercy and fill in the secrets
   (`PERSEVERER_API_KEY`, `PERSEVERER_JWT_SECRET`, `PERSEVERER_CORS_ALLOWED_ORIGINS`,
   `PERSEVERER_API_BASE_URL`, Garmin/Eufy settings). Never commit this file. All three
   containers read it via `EnvironmentFile=` — each just ignores the keys it doesn't use.
3. **GHCR auth**, if the packages are private:
   ```bash
   podman login ghcr.io -u <github-username>
   ```
4. **Install the units and start them:**
   ```bash
   podman quadlet install quadlet/perseverer-api.container \
     quadlet/perseverer-worker.container quadlet/perseverer-frontend.container
   systemctl --user enable --now perseverer-api perseverer-worker perseverer-frontend
   ```
5. **Auto-update on new pushes.** Each unit sets `AutoUpdate=registry` (checks GHCR for a newer
   digest under the same `:latest` tag and restarts in place) — this only actually runs on a
   schedule once the stock timer is enabled:
   ```bash
   systemctl --user enable --now podman-auto-update.timer
   ```
6. **Survive logout / start on boot**, since this is a rootless *user* session with no one
   permanently logged in:
   ```bash
   sudo loginctl enable-linger prez
   ```

### Redeploying after a new image push

```bash
podman auto-update                      # or just wait for the timer
# or, to force a specific unit right now regardless of digest:
systemctl --user restart perseverer-api
```

## Reverse proxy, TLS, and the double-NAT/split-horizon-DNS constraint (Phase 3+)

You already run a reverse proxy with a Let's Encrypt certificate issued via DNS-01 (DSM's
built-in one, on the same Synology box that used to also run the app containers) — this stack
reuses that certificate and proxy rather than adding a second ACME client or terminating TLS in
the app containers. What changed with the move to bercy: the proxy no longer reaches a
container directly over a shared Docker network the way it could when both lived on the same
DSM host — bercy's Quadlet units publish `8000` (api) and `8080` (frontend) to the host
directly (see "bercy deploy" above), so point the proxy's upstream at bercy's LAN IP on those
ports instead of whatever container reference it used to target.

Your network is double-NAT (ISP router + UniFi) and internal access relies on split-horizon
DNS, not hairpin NAT — **the public hostname does not necessarily resolve the same way inside
and outside the house.** Consequences:
- The frontend's API base URL is runtime-configurable, never baked into the Vite build — see
  `docs/adr/0008-phase-5-frontend.md` decision 6. `frontend/public/config.js` sets
  `window.__PERSEVERER_CONFIG__.apiBaseUrl`; the built nginx image regenerates that file at
  **container start** (not build time) from a `PERSEVERER_API_BASE_URL` env var, via a script
  in `docker/frontend-entrypoint.d/` that runs through nginx's own stock
  `/docker-entrypoint.d/` startup mechanism. Set `PERSEVERER_API_BASE_URL` to whichever
  hostname/URL resolves correctly for wherever the browser actually is — this is a per-deployment
  value, not a build-time constant, exactly because of the split-horizon DNS behavior above.
- `X-Forwarded-*` headers are trusted **only** from the known reverse-proxy IP (configured
  explicitly) — trusting them from anywhere else makes rate limiting and audit logs trivially
  spoofable, and source-IP trust is meaningless once everything arrives from the proxy anyway.

**Resolved (Phase 5):** the UI is reachable more broadly, not gated behind Tailscale — it has
real per-athlete authentication instead (password login issuing a JWT, or a standing per-athlete
API key; see ADR 0008). `sync athlete set-password` / `sync athlete create-key` provision an
athlete's credentials.

TODO (still open): document the exact reverse-proxy rule now pointing at bercy (hostname →
`bercy:8080`/`bercy:8000`), the header-forwarding configuration, and the certificate path — this
needs the user's actual proxy configuration, not something decidable from this repo alone. Also
still open: there is currently no migration step for the containerized deploy's data volume in
this runbook at all (starting the Quadlet units alone does not run `alembic upgrade head`
against the bind-mounted database) — a gap this phase found but did not fix, since it predates
Phase 5 and isn't specific to it.

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
