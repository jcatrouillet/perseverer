# Deployment and operations

Two environments:

| | Dev host | Production server |
|---|---|---|
| Runtime | uv + Vite on the host, or Podman/Docker Compose | Rootless Podman with systemd Quadlet units |
| Images | Built locally when needed | Pulled from GHCR, never built on the server |
| Ingress | `localhost` (API on 8008, frontend on 5173) | Your existing TLS reverse proxy in front of the frontend container |
| Config | `.env` (from `.env.example`) | `~/.config/containers/systemd/perseverer.env` (from `quadlet/perseverer.env.example`) |

See [ARCHITECTURE.md](ARCHITECTURE.md) for what each container does.

---

## Dev host

### Host workflow (recommended for day-to-day work)

```bash
cp .env.example .env
uv sync
uv run alembic upgrade head        # creates ./data/perseverer.db and the default athlete
uv run sync athlete set-password   # your login
uv run uvicorn perseverer.api.main:app --host 0.0.0.0 --port 8008
```

```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173, hot reload
```

The committed `frontend/public/config.js` points the dev frontend at `http://localhost:8008`.
`PERSEVERER_CORS_ALLOWED_ORIGINS` in `.env` must include the Vite origin (default
`http://localhost:5173`; Vite moves to 5174, 5175... if the port is taken).

Run a single uvicorn worker in development: with `--workers 2` on Windows, a reload can leave an
orphaned worker serving old code.

### Full stack in containers

```bash
podman compose up --build          # or: docker compose up --build
curl http://localhost:8008/api/v1/healthz
```

`compose.override.yml` (merged automatically) publishes the API on `DEV_API_PUBLISHED_PORT`
(8008) and the frontend on `DEV_FRONTEND_PUBLISHED_PORT` (5173). Rebuild after code changes.

Notes for Podman Desktop on Windows:

- Create the Podman machine with user-mode networking, or published ports never reach Windows
  `localhost`:
  ```bash
  podman machine stop
  podman machine rm podman-machine-default
  podman machine init podman-machine-default --user-mode-networking
  podman machine start
  ```
  If the first start fails with `ssh: rejected: connect failed`, run `podman machine stop`,
  `wsl --shutdown`, then `podman machine start`.
- The Windows volume backend can corrupt a SQLite database in WAL mode inside the `api`
  container. Use the host workflow above for real data and keep Compose for checking that the
  images build and start.

### Checks before committing

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run alembic check               # schema and migrations agree
cd frontend && npm run typecheck && npm run format:check && npm test && npm run build
uv run pip-audit && (cd frontend && npm audit --audit-level=high)
```

CI runs the same checks on every push.

---

## Images

GitHub Actions builds `perseverer-api`, `perseverer-worker` and `perseverer-frontend` on every
push to `main` that passes CI and pushes them to GHCR as `:latest`. The production server only
pulls. To use your own registry, change the `Image=` lines in `quadlet/*.container`.

---

## Production server

The units run rootless under a regular user, managed by `systemctl --user`. Paths below use `~` for
that user's home directory (`%h` in the unit files).

### One-time setup

1. **Data directory.** The units bind-mount `~/perseverer/data` (a plain directory, so the
   archive stays browsable and easy to back up). The container user (uid 1000) must own it inside
   rootless Podman's user namespace:
   ```bash
   mkdir -p ~/perseverer/data
   podman unshare chown -R 1000:1000 ~/perseverer/data
   ```
2. **Environment file.** Copy `quadlet/perseverer.env.example` to
   `~/.config/containers/systemd/perseverer.env` and fill it in. Required:
   `PERSEVERER_API_KEY`, `PERSEVERER_JWT_SECRET`, `PERSEVERER_API_BASE_URL` (the public origin, e.g.
   `https://perseverer.example.com`), `PERSEVERER_PUBLIC_BASE_URL` (same origin),
   `PERSEVERER_CORS_ALLOWED_ORIGINS`, `PERSEVERER_SCHEDULE_TIMEZONE`. Optional blocks: CARTO map key,
   staleness webhook, Eufy, backups, SMTP. All three containers read the same file and ignore the
   keys they don't use. Never commit it.
3. **Install and start the units:**
   ```bash
   podman quadlet install quadlet/perseverer-api.container \
     quadlet/perseverer-worker.container quadlet/perseverer-frontend.container
   systemctl --user enable --now perseverer-api perseverer-worker perseverer-frontend
   ```
4. **Create the schema** (Alembic is baked into the api image):
   ```bash
   podman exec perseverer-api alembic upgrade head
   ```
5. **Create your login:**
   ```bash
   podman exec -it perseverer-api sync athlete set-password
   ```
6. **Automatic updates.** Each unit has `AutoUpdate=registry`; enable the timer that applies it:
   ```bash
   systemctl --user enable --now podman-auto-update.timer
   ```
7. **Keep running after logout and start at boot:**
   ```bash
   sudo loginctl enable-linger "$USER"
   ```

### Updating

```bash
podman auto-update                         # or wait for the timer
podman exec perseverer-api alembic upgrade head
```

Run the migration after every update that ships one; migrations are additive and safe to run
while the app is up.

### Reverse proxy

The frontend container listens on 8080 and its nginx forwards `/api/`, `/mcp`, `/share/`,
`/oauth/`, `/authorize`, `/token`, `/register`, `/revoke` and `/.well-known/` to the api container,
so the whole app is one origin. Point your TLS-terminating reverse proxy at port 8080 of the
production server; the api's own port (8000) does not need to be exposed.

If the public hostname resolves differently inside your network (split-horizon DNS, no hairpin
NAT), make the internal record point at the reverse proxy rather than at the production server,
which only speaks plain HTTP. `PERSEVERER_API_BASE_URL` is applied when the frontend container
starts, so changing it needs only `systemctl --user restart perseverer-frontend`.

---

## Connecting data sources

| Source | How |
|---|---|
| Garmin Connect | Settings → External tools → Garmin (no MFA), or `podman exec -it perseverer-api sync auth login` (MFA-capable). Only the resulting tokens are stored; the daily sync uses them. |
| Garmin full history | Settings → External tools → upload the "Export your data" `.zip`, or `sync import garmin-export <path>` |
| Strava | Settings → External tools → upload the export `.zip`, or `sync import strava-export <path>` |
| Apple Health | `sync import apple-health <path-to-export.xml>` |
| Eufy scale | Settings → External tools → Eufy, or `sync athlete set-eufy-credentials` |
| Kaya | Settings → External tools → Kaya, or `sync auth kaya-login`; then imported daily |
| FIT files | `sync import fit-folder <path>` or `sync watch fit-folder <path>` |

Run `uv run sync --help` (or `podman exec perseverer-api sync --help`) for every command.

---

## Operations

### Rebuild from the archive

`sync rebuild` (or Settings → Rebuild) wipes every derived table and replays the raw archive,
re-applying all athlete corrections. Use it after a parser improvement to re-derive history; no
vendor is contacted.

### Backups and restore

The worker snapshots SQLite with `VACUUM INTO` and rsyncs it with the raw archive and Parquet
files to a backup host over SSH every day (`PERSEVERER_BACKUP_HOST`, `_USER`, `_PATH`; default
03:30). Local snapshots are pruned to `PERSEVERER_BACKUP_KEEP_LOCAL_SNAPSHOTS`; the remote copy
keeps everything.

One-time SSH key setup on the production server:

```bash
mkdir -p ~/perseverer/backup-ssh
ssh-keygen -t ed25519 -f ~/perseverer/backup-ssh/id_ed25519 -N ""
ssh-copy-id -i ~/perseverer/backup-ssh/id_ed25519.pub <backup_user>@<backup_host>
podman unshare chown -R 1000:1000 ~/perseverer/backup-ssh
chmod 700 ~/perseverer/backup-ssh && chmod 600 ~/perseverer/backup-ssh/id_ed25519
```

The worker unit mounts that directory read-only as the container's `~/.ssh`. Run a backup by hand
with `podman exec perseverer-worker sync backup create`.

Restore is deliberately command-line only, because it overwrites the live data directory:

```bash
podman exec -it perseverer-worker sync backup restore <path-or-user@host:path>
```

CI runs a full backup → wipe → restore round trip on every push.

### Email reports

Set the SMTP block in the env file (`PERSEVERER_SMTP_HOST`, `_PORT`, `_USERNAME`, `_PASSWORD`,
`_FROM`, `_SECURITY`). Port 587 uses `starttls`; port 465 uses `ssl`. Each athlete then sets an
email address in Settings → Profile and enables the weekly and/or monthly summary in Settings →
External tools, where "Send test email" sends the current weekly report immediately.

### Staleness alerts

The daily job warns when the Garmin sync has not succeeded for
`PERSEVERER_GARMIN_STALE_ESCALATE_DAYS` or the last full export is older than
`PERSEVERER_EXPORT_FRESHNESS_DAYS`. Set `PERSEVERER_STALENESS_WEBHOOK_URL` to receive them as a
JSON POST.

### More athletes

Every table is scoped by athlete, so a second athlete needs no code or config change:

```bash
podman exec perseverer-api sync athlete create --display-name "Alex"     # prints the new id
podman exec -it perseverer-api sync athlete set-password --athlete-id <id>
podman exec -it perseverer-api sync auth login --athlete-id <id>          # their Garmin
podman exec -it perseverer-api sync athlete set-eufy-credentials --athlete-id <id>
```

The worker's daily jobs pick them up on the next run.

### MCP clients

- **Claude Code or any client that can send a header:** use `https://<your-host>/mcp` with the
  header `X-API-Key: <api key>` (Settings → API key creates a personal one).
- **claude.ai custom connector (OAuth):** add a custom connector with the URL
  `https://<your-host>/mcp` and leave the OAuth fields empty (dynamic client registration). Sign
  in with your Perseverer username and password on the page that opens. Requires
  `PERSEVERER_PUBLIC_BASE_URL` and `PERSEVERER_JWT_SECRET`. Only the default athlete can
  authorize. To revoke a connector, delete its rows from `oauth_token`.

A browser that loaded the app before the OAuth paths existed may need one normal visit to the
app first, so the updated service worker stops intercepting `/authorize`.
