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
3. **GHCR auth** — not needed as of this writing: the three packages
   (`perseverer-{api,worker,frontend}`) are public, so `podman pull` works with no credentials.
   Only needed again if they're ever switched back to private:
   ```bash
   podman login ghcr.io -u <github-username>
   ```
4. **Install the units and start them:**
   ```bash
   podman quadlet install quadlet/perseverer-api.container \
     quadlet/perseverer-worker.container quadlet/perseverer-frontend.container
   systemctl --user enable --now perseverer-api perseverer-worker perseverer-frontend
   ```
5. **Run migrations** — starting the units alone does not create or update the schema against
   the bind-mounted database. `alembic.ini` and `alembic/` are baked into the api image
   specifically so this can run against the live container, no separate host-side alembic
   invocation needed:
   ```bash
   podman exec perseverer-api alembic upgrade head
   ```
6. **Auto-update on new pushes.** Each unit sets `AutoUpdate=registry` (checks GHCR for a newer
   digest under the same `:latest` tag and restarts in place) — this only actually runs on a
   schedule once the stock timer is enabled:
   ```bash
   systemctl --user enable --now podman-auto-update.timer
   ```
7. **Survive logout / start on boot**, since this is a rootless *user* session with no one
   permanently logged in:
   ```bash
   sudo loginctl enable-linger prez
   ```

### Provisioning a second athlete

Every table except `athlete`/`metric_definition` already carries `athlete_id`, and every REST
route resolves it from the caller's own credential — a second athlete works with **zero code
changes** once their `athlete` row and credentials exist. Run these once, in order, against the
live `api` container (`podman exec perseverer-api ...`), same as `alembic upgrade head` above:

1. **Create the athlete row** — the one step no other command does; `sync athlete
   set-password`/`create-key` only `UPDATE` an existing row:
   ```bash
   podman exec perseverer-api sync athlete create --display-name Erwan
   # prints the new athlete's id -- needed by every command below
   ```
2. **Set login credentials** (password or a standing API key):
   ```bash
   podman exec -it perseverer-api sync athlete set-password --athlete-id <new-id>
   ```
3. **Log into their own Garmin account.** Each athlete gets their own token-store directory
   (`<data_dir>/garmin_tokens/<athlete_id>/`, previously one shared global directory) — this
   MFA-capable interactive login has to run at a real terminal, same as the original athlete's:
   ```bash
   podman exec -it perseverer-api sync auth login --athlete-id <new-id>
   ```
4. **Set their own Eufy credentials**, if they have their own scale (stored in
   `athlete_eufy_config`, one row per athlete — see `adapters/eufy.py::resolve_eufy_credentials`):
   ```bash
   podman exec -it perseverer-api sync athlete set-eufy-credentials --athlete-id <new-id>
   ```

That's it — no restart, no config change. The worker's daily jobs (`run_daily_sync`,
`run_daily_workout_push`) already loop over every row in `athlete`, so the new athlete's Garmin/
Eufy sync and scheduled-workout push start on the very next scheduled run. They log into the
frontend with the credentials from step 2 and see only their own data.

**One-time migration note for the original athlete**, needed only once, the first time this app
is upgraded past the per-athlete tokenstore change above: the pre-existing single-athlete
deployment's Garmin token store lived directly at `<data_dir>/garmin_tokens/`. Move it into its
own namespaced subdirectory so the original athlete's daily sync keeps working:
```bash
podman exec perseverer-api sh -c \
  'mkdir -p /data/garmin_tokens/<DEFAULT_ATHLETE_ID> && \
   find /data/garmin_tokens -maxdepth 1 -type f \
     -exec mv {} /data/garmin_tokens/<DEFAULT_ATHLETE_ID>/ \;'
```
(substitute the real `DEFAULT_ATHLETE_ID`, see `db/seed.py`). Eufy credentials need no such
migration — the original athlete's env-var-based `PERSEVERER_EUFY_*` settings keep working
unchanged as a fallback for exactly that one athlete id (see `resolve_eufy_credentials`); only a
*second* athlete needs an `athlete_eufy_config` row.

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
  The same script/mechanism regenerates `cartoApiKey` from `PERSEVERER_CARTO_API_KEY` (the
  CARTO basemap vector-tile API key, `frontend/src/mapBasemap.ts`) — unlike `apiBaseUrl` it isn't
  actually a per-deployment/split-horizon value, it's just following the same runtime-config
  mechanism so bercy's key can be rotated without a rebuild.
- `X-Forwarded-*` headers are trusted **only** from the known reverse-proxy IP (configured
  explicitly) — trusting them from anywhere else makes rate limiting and audit logs trivially
  spoofable, and source-IP trust is meaningless once everything arrives from the proxy anyway.

**Resolved (Phase 5):** the UI is reachable more broadly, not gated behind Tailscale — it has
real per-athlete authentication instead (password login issuing a JWT, or a standing per-athlete
API key; see ADR 0008). `sync athlete set-password` / `sync athlete create-key` provision an
athlete's credentials.

### Single-origin routing: one port, not two

The frontend and API used to need two separate outer reverse-proxy rules (and two external
ports) purely because nothing forwarded API traffic that arrived on the frontend's own origin.
That's no longer true: `docker/nginx.conf` (the frontend container's own nginx) now proxies
`/api/` (every REST route already carries its own `/api/v1` prefix, so no rewrite is needed) and
`/mcp` (the MCP server, with `proxy_buffering off`/long timeouts for its long-lived Streamable
HTTP sessions) straight through to the API container, exactly the same way it already did for
`/share/` since Phase 7. Everything else still falls through to the SPA's `index.html`.

Practically, this means `PERSEVERER_API_BASE_URL` can now be set to the **same origin** the
frontend itself is served from (e.g. `https://perseverer.catrouillet.net`, no second port) — the
browser's own `/api/v1/...` and `/mcp` requests land on that one origin and nginx routes them to
the right container internally. The old two-port setup (api on its own origin/port) still works
if you'd rather keep it that way; nothing about the API container itself changed, only the
frontend's own nginx gained two more `location` blocks. `PERSEVERER_PUBLIC_BASE_URL` (the
`/share/{token}` link origin, see `quadlet/perseverer.env.example`) can now safely be left unset
under single-origin routing too — its whole reason for existing was the api/frontend port
mismatch, and an unset value already falls back to the incoming request's own base URL, which is
now correct either way.

**Migrated (confirmed live)** — bercy runs single-origin routing as of this writing. What that
migration was, for reference (a manual Synology NAS step, not something a code change can do):
1. Deploy the new frontend image (it needs no other changes than the `nginx.conf` update above).
2. Set `PERSEVERER_API_BASE_URL=https://perseverer.catrouillet.net` (the frontend's own existing
   origin, not `:444`) in `~/.config/containers/systemd/perseverer.env` on bercy, then
   `systemctl --user restart perseverer-frontend` to regenerate `config.js`.
3. Confirm the app works end to end (calendar loads, an activity opens, `/share/{token}` links
   still resolve) while the old `:444`/`:81` DSM rule is still in place — the new same-origin
   path works alongside the old dedicated port with no conflict, so this step is safe to verify
   before removing anything.
4. Delete the `api` rule in DSM's Reverse Proxy (Control Panel → Login Portal → Advanced, or
   Application Portal depending on DSM version) and drop `81,444` from the UniFi port-forward
   rule below.

### The actual reverse-proxy configuration (confirmed live, not guessed)

DSM's Reverse Proxy (Control Panel → Login Portal → Advanced, or Application Portal depending on
DSM version) runs on `nas.catrouillet.net` (`192.168.1.98`), aliased as `perseverer.catrouillet.net`.
One rule now (the `api` rule above was removed once single-origin routing was verified working),
HTTPS source → plain HTTP destination (bercy never terminates TLS itself):

| Rule | Source | Destination |
|---|---|---|
| frontend (+ api, via `docker/nginx.conf`'s `/api/`/`/mcp` proxy) | `https://perseverer.catrouillet.net:443` (+ `:80`) | `http://bercy.catrouillet.net:8080` |

Verified live: `https://perseverer.catrouillet.net/api/v1/healthz` and `https://perseverer.
catrouillet.net:444/api/v1/healthz` — the former returns `{"status":"ok"}`, the latter now
fails to connect at all (the DSM rule and its cert binding are gone).

**Gotcha that cost real debugging time:** each HTTPS reverse-proxy rule needs its own explicit
certificate assignment in **Control Panel → Security → Certificate → Settings** (a separate tab
from the certificate *list* itself, mapping services to certs). A rule with nothing bound
doesn't error — DSM just silently drops the TLS handshake, which is indistinguishable from a
network-level timeout/routing failure from the client side. If a new reverse-proxy rule "hangs"
from outside the LAN with the port-forwarding and firewall both already confirmed fine, check
this first.

**Router (UniFi):** a single port-forward rule, WAN ports `80,443` (TCP) → `192.168.1.98` (the
reverse-proxy device — not bercy directly). `81,444` were dropped once the api DSM rule was
removed. The WAN IP Address field showing a private (`192.168.0.x`) address with a warning icon
looked like a second NAT layer needing its own separate forwarding rule, but isn't in this case
— confirmed live that all fiber traffic already reaches this gateway directly.

**Split-horizon DNS** (the actual fix for "works on my phone, not on my LAN"): a UniFi Local DNS
Record (Settings → Networks → Advanced → Local DNS Record) maps `perseverer.catrouillet.net` →
`192.168.1.98` for LAN queries only. Critically, this must point at the **reverse-proxy device's**
LAN IP, not bercy's — bercy has no TLS listener at all (plain HTTP only, on 8000/8080), so a LAN
client resolving straight to bercy over `https://` gets an immediate connection failure once
split-horizon is wired, since there's nothing there to answer HTTPS at all. Without this override,
`frontend/public/config.js`'s `apiBaseUrl` (necessarily the public hostname, since it must also
work from outside the LAN) is simply unreachable from inside the house — same hairpin-NAT
limitation as testing directly against the public IP, just discovered via "no data loads" instead
of an obvious connection error, since the *page* still loads fine (nginx serves it locally
regardless), only the API calls it makes afterward fail. This applies identically under
single-origin routing — nothing about split-horizon DNS changes, there's just one fewer port for
it to matter for.

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
- **Perseverer rename VDOT/pace-band metric-key migration**: any database populated before the
  sporthealth -> perseverer rename has its VDOT and pace-band `activity_metric` rows stored under
  the old hardcoded key prefix (`sporthealth.performance.vdot`,
  `sporthealth.performance.pace_band.*`) — the rename updated the Python string constants
  (`VDOT_METRIC_KEY` etc.) everywhere in code, but never touched already-written rows, so every
  read via the new key silently returned nothing until this ran. Discovered live: bercy's own
  Training Bands/Pace Trends charts showed all-zero data post-migration, which looked exactly
  like "no data" rather than "wrong key" until traced back. Fix: `uv run sync backfill-vdot &&
  uv run sync backfill-pace-bands` (or the same two commands via `podman exec perseverer-api`
  in production) — both already do a full delete-and-recompute under the *current* key, so this
  is the same repair either way; only the stale rows under the dead old-key prefix need a manual
  `DELETE FROM activity_metric WHERE metric_key LIKE 'sporthealth.%'` afterward (harmless to
  skip — nothing reads that prefix anymore — but leaves 9700+ orphaned rows behind). Not needed
  for a fresh install or any database created after the rename.

## Backups (Phase 9, ADR 0014)

`src/perseverer/backup.py` snapshots the SQLite database via `VACUUM INTO` (a live, consistent
snapshot — no need to stop the app) and rsyncs it plus the raw archive and Parquet trees to a
second host over SSH. Scheduled daily in the worker container (`PERSEVERER_BACKUP_SCHEDULE_HOUR`/
`MINUTE`, default 03:30 UTC — its own cron job, separate from the Garmin sync's schedule), and
runnable by hand: `podman exec perseverer-worker sync backup create`.

**Configuration** (`quadlet/perseverer.env.example`): `PERSEVERER_BACKUP_HOST`,
`PERSEVERER_BACKUP_USER`, `PERSEVERER_BACKUP_PATH` (all three required together — unset means
`create_backup()` logs and skips, same graceful-degradation contract as the Eufy credentials),
`PERSEVERER_BACKUP_SSH_KEY_PATH` (optional, only needed for a non-default key),
`PERSEVERER_BACKUP_KEEP_LOCAL_SNAPSHOTS` (default 7 — bounds *local* disk usage on bercy; the
remote copy is the real backup and is deliberately left to accumulate full history).

**One-time host setup** (this app has no way to provision credentials on a host it doesn't
control, so this step is manual):
```bash
# On bercy, as the user the worker container's rootless Podman runs under:
mkdir -p /home/prez/perseverer/backup-ssh
ssh-keygen -t ed25519 -f /home/prez/perseverer/backup-ssh/id_ed25519 -N ""
ssh-copy-id -i /home/prez/perseverer/backup-ssh/id_ed25519.pub <backup_user>@<backup_host>
# The container's uid 1000 needs to read these under rootless Podman's user-namespace remapping
# -- same "podman unshare chown" pattern the /data bind mount already uses.
podman unshare chown -R 1000:1000 /home/prez/perseverer/backup-ssh
chmod 700 /home/prez/perseverer/backup-ssh
chmod 600 /home/prez/perseverer/backup-ssh/id_ed25519
```
The Quadlet worker unit mounts this directory read-only at the container's default `~/.ssh`
lookup path, so plain `ssh`/`rsync` pick up the key with no extra flags once
`PERSEVERER_BACKUP_HOST`/`USER`/`PATH` are filled in.

**Restore** is deliberately CLI-only, never a Settings-page button — unlike `rebuild` (additive,
replays the raw archive, never destructive), a restore overwrites whatever is currently at this
host's data directory with an older snapshot. Human-initiated, one-shot, matching
`sync auth login`'s own precedent for dangerous actions:
```bash
podman exec -it perseverer-worker sync backup restore <path-or-user@host:path>
```

**Automated restore-from-backup test**: wired into CI (`.github/workflows/ci.yml`'s
`backup-restore` job) — imports a real fixture, backs it up to a local temp directory (rsync
treats a local path and a `user@host:path` spec identically, so this exercises the exact same
code with no LAN/SSH host for a CI runner to reach), wipes the data directory entirely, restores,
and asserts the activity count and raw-archive file listing both match exactly. This is the
literal Phase 9 acceptance criterion.

## Scheduled workouts (ADR 0015)

`worker/main.py::run_daily_workout_push` runs on its own daily schedule
(`PERSEVERER_WORKOUT_PUSH_SCHEDULE_HOUR`/`MINUTE`, default 04:45 UTC, right after the Garmin
sync's own 04:15) and automatically pushes any `planned_workout` whose date is within
`PERSEVERER_PLANNED_WORKOUT_PUSH_WINDOW_DAYS` (default 7) days and hasn't been pushed yet — no
one-time host setup needed, it reuses the same Garmin token store
(`sync auth login`/`data/garmin_tokens/`) the daily sync already depends on. A manual push
(`POST /planned-workouts/{date}/push`, exposed as a button in the calendar UI) bypasses the
window for wanting a specific workout on the watch immediately.

**Live verification not yet done** (writes to a real Garmin account, needs explicit
confirmation first): schedule one real running workout with a pace range, an HR-range interval
block, and cadence; push it; confirm in the actual Garmin Connect app/website that it appears
correctly structured and scheduled on the right date. See
`docs/adr/0015-scheduled-workouts.md`'s own Verification section for the one thing that can't be
checked any other way — whether a cadence target riding alongside a pace target on the same step
actually reaches and functions on a real device.

## Weekly / monthly email reports (email_reports.py)

Opt-in, off by default. Two `APScheduler` jobs in the worker (`run_weekly_email_report`,
`run_monthly_email_report`) send an athlete a training digest: **weekly** on Sunday 18:00 (the
week that just ended, plus the coming week's planned workouts), **monthly** on the month's last
day 18:00 (that month's totals). Schedule times resolve against `PERSEVERER_SCHEDULE_TIMEZONE`
like every other worker job; adjust with `PERSEVERER_EMAIL_REPORT_HOUR`/`_MINUTE` /
`_WEEKLY_DAY_OF_WEEK`.

**Configuration** (`quadlet/perseverer.env.example`): one shared SMTP relay for the whole
deployment, read by both the `worker` (scheduled sends) and `api` (the "send test email"
button) containers from the same env file:

```
PERSEVERER_SMTP_HOST=ssl0.ovh.net
PERSEVERER_SMTP_PORT=587
PERSEVERER_SMTP_USERNAME=<OVH mailbox login>
PERSEVERER_SMTP_PASSWORD=<its password>
PERSEVERER_SMTP_FROM=<From: address, normally the same mailbox>
PERSEVERER_SMTP_SECURITY=starttls
```

All six optional together — the jobs and the test button log-and-skip when any of
host/username/password/from is unset, same graceful-degradation contract as the Eufy/backup
blocks. **Port 587** is the mail-submission port and uses **STARTTLS** (`security=starttls`);
implicit SSL/TLS is **port 465** (`security=ssl`). OVH's `ssl0.ovh.net` serves both — if
STARTTLS on 587 misbehaves, switch to `PERSEVERER_SMTP_PORT=465` +
`PERSEVERER_SMTP_SECURITY=ssl`, no code change.

**Per athlete**: each athlete then (1) sets their address on Settings → Profile → Email, and
(2) ticks "Weekly summary" and/or "Monthly summary" on Settings → External tools → Email
reports. "Send test email" there sends the current weekly report immediately — the way to
confirm SMTP + the Profile email are right without waiting for Sunday.
