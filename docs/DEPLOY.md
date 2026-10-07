# Deployment and operations

All configuration lives in **one file, `perseverer.env`**, created from
[`perseverer.env.example`](../perseverer.env.example) at the repository root. Fill in its
"Required" section (public URL, two secrets, time zone), adjust the infrastructure section if
the defaults don't suit you, and every component picks it up:

| | Dev host | Production server |
|---|---|---|
| Runtime | uv + Vite on the host, or Docker/Podman Compose | Rootless Podman with systemd Quadlet units |
| Reads `perseverer.env` | The app (`uv run ...`), the Vite dev server, Compose (`--env-file`) | `scripts/install-production.sh` renders the units from it and installs it next to them |
| Images | Built locally when needed | Pulled from `PERSEVERER_IMAGE_PREFIX`, never built on the server |
| Ingress | `localhost` | Your existing TLS reverse proxy in front of the frontend port |

See [ARCHITECTURE.md](ARCHITECTURE.md) for what each container does.

---

## Dev host

### Host workflow (recommended for day-to-day work)

```bash
cp perseverer.env.example perseverer.env   # set PERSEVERER_API_KEY and PERSEVERER_JWT_SECRET
uv sync
uv run alembic upgrade head        # creates ./data/perseverer.db and the default athlete
uv run sync athlete set-password   # your login
uv run uvicorn perseverer.api.main:app --port 8000
```

```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173, hot reload
```

The Vite dev server serves `/config.js` from `perseverer.env`, pointing the app at
`http://localhost:<PERSEVERER_HOST_API_PORT>` (8000 by default; set `PERSEVERER_HOST_API_PORT` if
you run uvicorn on another port). `PERSEVERER_CORS_ALLOWED_ORIGINS` must include the Vite origin
(`http://localhost:5173` in the template; Vite moves to 5174, 5175... if the port is taken).

Run a single uvicorn worker in development: with `--workers 2` on Windows, a reload can leave an
orphaned worker serving old code.

### Full stack in containers

```bash
docker compose --env-file perseverer.env up --build    # or: podman compose ...
curl http://localhost:8000/api/v1/healthz
```

Every service reads `perseverer.env`; `--env-file` also feeds it to Compose itself, which
publishes the API on `PERSEVERER_HOST_API_PORT` and the frontend on
`PERSEVERER_HOST_FRONTEND_PORT` (open <http://localhost:8080>). Rebuild after code changes.

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
push to `main` that passes CI and pushes them to GHCR as `:latest` (and `:sha-<commit>`). The
production server only pulls `PERSEVERER_IMAGE_PREFIX/perseverer-*:PERSEVERER_IMAGE_TAG`; the
template's default points at this repository's published images. If you build your own, push
them to your registry and change the prefix.

---

## Production server

The services run rootless under a regular user, managed by `systemctl --user`. You need Podman
(with Quadlet, 4.4+) and a copy of this repository on the server.

### Install

```bash
git clone <this repository> perseverer && cd perseverer
cp perseverer.env.example perseverer.env
$EDITOR perseverer.env                     # Required section + anything you want to change
scripts/install-production.sh              # renders the units, installs, starts, migrates
podman exec -it perseverer-api sync athlete set-password   # your login
sudo loginctl enable-linger "$USER"        # keep running after logout, start at boot
```

`install-production.sh`:

1. checks that the required values are set;
2. creates the data and backup-key directories if they don't exist, owned by the container user;
3. renders `quadlet/*.container` with the image, data directory, ports and time zone from
   `perseverer.env` and installs them, with a copy of `perseverer.env`, into
   `~/.config/containers/systemd/` (all three containers read that copy);
4. restarts the services, enables `podman-auto-update.timer`, waits for the API and runs the
   database migrations.

### Updating

- **New images:** each unit has `AutoUpdate=registry`, so the timer pulls new images and restarts
  the services. Apply migrations afterwards:
  ```bash
  podman exec perseverer-api alembic upgrade head
  ```
- **Configuration change or new repository version:** edit `perseverer.env` (or `git pull`) and
  re-run `scripts/install-production.sh`. It is safe to re-run at any time.

Migrations are additive and safe to run while the app is up.

### Reverse proxy

The frontend container listens on `PERSEVERER_HOST_FRONTEND_PORT` (8080) and its nginx forwards
`/api/`, `/mcp`, `/share/`, `/oauth/`, `/authorize`, `/token`, `/register`, `/revoke` and
`/.well-known/` to the API on `PERSEVERER_HOST_API_PORT`, so the whole app is one origin and the
web app needs no API URL. Point your TLS-terminating reverse proxy at the frontend port; the API
port does not need to be exposed.

If the public hostname resolves differently inside your network (split-horizon DNS, no hairpin
NAT), make the internal record point at the reverse proxy rather than at the production server,
which only speaks plain HTTP.

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
files to a backup host over SSH every day (the backup block of `perseverer.env`:
`PERSEVERER_BACKUP_HOST`, `_USER`, `_PATH`; default 03:30). Local snapshots are pruned to `PERSEVERER_BACKUP_KEEP_LOCAL_SNAPSHOTS`; the remote copy
keeps everything.

One-time SSH key setup on the production server:

```bash
# the directory is PERSEVERER_HOST_BACKUP_SSH_DIR (default ~/perseverer/backup-ssh)
mkdir -p ~/perseverer/backup-ssh
ssh-keygen -t ed25519 -f ~/perseverer/backup-ssh/id_ed25519 -N ""
ssh-copy-id -i ~/perseverer/backup-ssh/id_ed25519.pub <backup_user>@<backup_host>
chmod 700 ~/perseverer/backup-ssh && chmod 600 ~/perseverer/backup-ssh/id_ed25519
podman unshare chown -R 1000:1000 ~/perseverer/backup-ssh   # last: hands it to the container user
```

The worker mounts that directory (`PERSEVERER_HOST_BACKUP_SSH_DIR`, created by the install
script) read-only as the container's `~/.ssh`. Run a backup by hand
with `podman exec perseverer-worker sync backup create`.

Restore is deliberately command-line only, because it overwrites the live data directory:

```bash
podman exec -it perseverer-worker sync backup restore <path-or-user@host:path>
```

CI runs a full backup → wipe → restore round trip on every push.

### Email reports

Fill in the SMTP block of `perseverer.env` (`PERSEVERER_SMTP_HOST`, `_PORT`, `_USERNAME`,
`_PASSWORD`, `_FROM`, `_SECURITY`) and re-run `scripts/install-production.sh`. Port 587 uses `starttls`; port 465 uses `ssl`. Each athlete then sets an
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
