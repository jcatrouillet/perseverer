# ADR 0014: Phase 9 — backup + restore automation, hardening (narrative layer deferred)

## Status

Two of Phase 9's three original threads (backup/restore automation, hardening) are shipped; the
third (an LLM narrative layer over the rules-based `insight` table) is deliberately deferred —
see decision 1. The phase's own original acceptance criterion ("narratives are cached and the
app is fully functional with the LLM disabled") is trivially true this round since there's no
LLM integration at all yet; the criterion that actually matters here is "automated
restore-from-backup test passes in CI."

**Backup/restore**: `src/perseverer/backup.py` (`create_backup`/`restore_backup`) snapshots the
SQLite database via `VACUUM INTO` and rsyncs it plus the raw archive and Parquet trees to a
second host over SSH, scheduled daily in the worker container alongside the existing Garmin
sync. Verified locally: a real round trip (real athlete row, real raw-archive bytes, real
Parquet bytes) through `create_backup` → wipe → `restore_backup` reproduces the exact original
content, and `VACUUM INTO`'s parameterized-path form (`VACUUM INTO ?`) works correctly against
Python's `sqlite3` DBAPI (confirmed directly — not assumed, since this specific syntax's
parameter-binding support isn't something to take on faith).

**Verified live against real containers, not just unit tests**: per the user's own instruction
("change quadlet units on dev first"), the equivalent hardening
(`read_only`/`tmpfs`/`security_opt: no-new-privileges`) was added to `compose.yaml` — dev's own
deploy path (Windows/Podman Desktop) — and the full stack was actually built and run via `podman
compose up --build` before any Quadlet change touched bercy. This caught one real bug:
`create_backup` against a destination whose root directory had never been created (a fresh
`PERSEVERER_BACKUP_PATH` that's never received a backup — exactly the real first-run case) failed
outright, because rsync only auto-creates the *last* missing path component of a destination, not
every missing parent — confirmed via the actual error (`rsync: [Receiver] mkdir ".../raw" failed:
No such file or directory`), not guessed at. Fixed with `_ensure_destination_exists` (decision 9)
and re-verified live end to end inside the running worker container: a real backup to a
two-levels-missing destination now succeeds, and a real restore from it correctly reconstructs
the database and raw archive content. `tests/test_backup.py`'s own rsync stand-in was tightened
to reproduce this exact one-level-only limitation (it previously used `mkdir(parents=True)`,
which is *more* forgiving than real rsync and would have let this bug pass silently) — this is
what makes the added regression test (`test_create_backup_succeeds_when_the_destination_root_
does_not_exist_at_all`) meaningful rather than just re-testing the mock's own behavior.

rsync itself isn't installed in this project's own Windows/Git Bash dev environment outside a
container, so the *unit* tests still mock it out; the real binary's correctness is what both the
live compose verification above and the new `backup-restore` CI job (`ubuntu-latest`, which ships
rsync preinstalled) exercise for real — 15/15 backup unit tests passing locally, CI job not yet
observed green as of writing this ADR (first push pending).

**Hardening**: `pip-audit`/`npm audit --audit-level=high` both ran clean (zero findings) before
being wired into CI as unconditional failures — see decision 6 for why that ordering mattered.
Login brute-force lockout (`auth/lockout.py`) is DB-backed and verified with 6 unit tests plus 3
new `/auth/login` integration tests (locks out at exactly `MAX_FAILED_ATTEMPTS`, doesn't lock out
one attempt below it, and is correctly scoped per-username). Container hardening
(`ReadOnly=true`/`ReadOnlyTmpfs=true`/`NoNewPrivileges=true`) shipped for the `api` and `worker`
Quadlet units, verified first via the compose-based dev equivalent described above: both
containers booted cleanly, `alembic upgrade head` ran a real migration against the bind-mounted
`/data` successfully, the worker's scheduler logged both the Garmin-sync and backup jobs, and
`GET /healthz` returned 200 — all under `read_only: true`, confirming every real write path
these two containers have genuinely does live under `/data`. The `frontend` container is
explicitly out of scope this round, see decision 6. `ReadOnlyTmpfs=`'s actual default was
verified against Podman's current documentation, not assumed, after finding a real historical
Quadlet bug that inverted it — the *Quadlet* directive itself (as opposed to the
directly-equivalent Compose settings verified live above) has not been exercised against bercy's
own Podman version as of writing.

Full backend suite: 873 tests / ruff / mypy clean throughout this phase's work.

## Decisions

### 1. LLM narrative layer deferred, not built this round

The brief's Phase 9 scope included "LLM narrative layer over the Insight objects (cached in DB,
regenerated on demand)." Working through the design surfaced a real product/billing question:
the user's Claude Pro subscription (claude.ai) and the Anthropic API are separate products with
separate billing — a Pro subscription includes no API credits, and there's no supported way to
route backend/cron calls through a Pro login. Presented with that, plus the realistic cost at
this workload (a few dozen short Haiku prompts/day, well under $1/month at $1/$5-per-million-
token pricing) and the alternative of a local model on bercy's GPU-less NUC (workable but a new
always-on service with materially worse quality/latency), the user chose to defer the whole
feature rather than commit to either path right now.

The reference design (kept for whenever this is picked back up, not built):
- `insight_narrative` (per-insight one-liner) and `insight_digest` (per-window paragraph
  rolling up that window's insights into prose) tables, both keyed on `(athlete_id, kind,
  window, subject_key)` — **not** `insight.id`. `refresh_insights` does a full delete-and-
  reinsert into `insight` on every run (ADR 0012), so even an unchanged insight gets a brand-new
  autoincrement id every refresh; a narrative cache keyed on that id would never find a match
  after the very next refresh, forcing an LLM call for literally everything, every time — the
  opposite of "cached, regenerated on demand." The `(kind, window, subject_key)` triple is the
  same natural identity `insight` already uses for its own `uq_insight_identity` constraint, and
  is stable across refreshes even though the row's surface id isn't.
- A `source_hash` column (hash of the insight's own `title`/`detail`, or of a window's whole
  insight set for the digest) is what makes "regenerated on demand" actually cheap: only call
  the LLM when the hash differs from what's cached, which most days is nothing.
- Wiring: a `POST /settings/narratives/regenerate` action (cloning `POST /settings/rebuild`'s
  `BackgroundTasks` pattern) for an explicit "regenerate now," plus a call from the worker's
  daily schedule right after the existing `refresh_insights` call so narratives self-heal
  without a manual click for the common case. `PERSEVERER_ANTHROPIC_API_KEY` unset → every call
  skips gracefully (returns `None`, never raises), same contract as `eufy_email` et al.

### 2. Backup mechanism: `VACUUM INTO` + rsync, local-path/remote-host symmetry by design

`create_backup`'s `destination` and `restore_backup`'s `source` are both a plain string —
`user@host:path` or a bare local path — because rsync treats the two identically. This
symmetry is deliberate, not incidental: it's what lets the CI restore-from-backup test call the
*exact same functions* against a local temp directory instead of a real SSH host, so what's
under test is genuinely "does a snapshot contain everything needed to restore," not "can a CI
runner reach a LAN host it was never going to have network access to anyway."

The remote destination accumulates full history (every rsync'd snapshot, never pruned there);
only the *local* copy under `<data_dir>/backups/` is pruned to the last
`PERSEVERER_BACKUP_KEEP_LOCAL_SNAPSHOTS` (default 7) — local snapshots exist only to be rsync'd
from, so there's no reason to let them grow bercy's own disk usage unbounded, while the remote
side is the actual disaster-recovery asset and should keep as much history as it can hold.

### 3. Restore stays CLI-only, mirroring `sync auth login`'s precedent, not `rebuild`'s

`rebuild` is additive and non-destructive by construction — it only ever replays the raw
archive, so `POST /settings/rebuild` was a safe thing to expose as a Settings-page button
(ADR 0013). A restore is the opposite: it overwrites whatever is currently at the host's data
directory with an older snapshot, which is exactly the kind of rare, genuinely destructive,
ops-only action this project already has a precedent for keeping human-initiated and CLI/SSH-
only — `adapters/garmin_connect.py::login_with_credentials`'s own credentialed-login path. `sync
backup restore` prompts for confirmation by default (`--yes` for scripted/CI use) and is never
wired into the web UI.

### 4. Login lockout: DB-backed, keyed on username, and timing-parity with the existing
constant-time password check

`/auth/login` already ran `verify_password` against a dummy hash even for an unknown username,
specifically so a nonexistent username couldn't be distinguished from a wrong password by
response latency (ADR 0008). Adding a lockout check without the same discipline would reopen
that exact side-channel — a locked-out response that skips the password check entirely would be
measurably faster than a normal wrong-password response. `is_locked_out()` runs unconditionally
before the credential check, and every code path (locked out, wrong password, right password)
does the same set of operations before deciding its response.

DB-backed, not in-memory: `api.Dockerfile` runs uvicorn with 2 workers, which don't share
process memory. An in-memory attempt counter would let an attacker halve their effective
lockout by spreading requests across both workers; every worker already shares the same SQLite
file, so that's the one piece of state they actually have in common. Keyed on `username`, not
source IP: this app trusts `X-Forwarded-For` only from a known reverse-proxy IP (ADR 0006
decision 8), so every request already arrives from that same proxy IP regardless of the real
client — an IP-based counter would either have nothing meaningful to key on, or would have to
trust a client-supplied header, which defeats the point.

### 5. Dependency vulnerability scanning: zero findings first, hard-fail from day one

Both `pip-audit` and `npm audit --audit-level=high` were run manually before being wired into
CI at all, specifically to check whether an unconditional hard-fail would immediately red the
build on some pre-existing, possibly-unfixable transitive advisory. Both came back clean, so
both ship as plain, unconditional CI steps with no allowlist mechanism — one doesn't exist yet
because there's nothing to allowlist. If a real, currently-unfixable advisory is ever found,
`pip-audit --ignore-vuln <id>` (with a one-line reason in the CI step) is the intended escape
hatch, not disabling the check.

### 6. Container hardening: `api` and `worker` only, `frontend` explicitly deferred

`ReadOnly=true` + `NoNewPrivileges=true` shipped for `api` and `worker` after confirming every
real write path each container has: `/data` (the one bind mount, everything else in the
Dockerfiles' own `USER perseverer` design already funnels through it), plus two smaller fixes
made in service of this — the bulk-upload endpoint already streamed to `settings.data_dir /
"tmp"` rather than the OS `/tmp` (no change needed there), but `api/duckdb_conn.py`'s DuckDB
connection had never set an explicit `temp_directory`, defaulting to the (now read-only) current
working directory for a query large enough to spill to disk; it's now pointed at `/data` too.

`frontend` was **not** hardened this round. Its nginx-based entrypoint mechanism
(`docker/frontend-entrypoint.d/20-generate-config.sh`) rewrites `config.js` in place under
`/usr/share/nginx/html` at every container start — the one thing that makes the API base URL
genuinely runtime-configurable rather than baked into the image (ADR 0008 decision 6, load-
bearing given this deployment's split-horizon DNS). That path isn't one of Podman's
auto-tmpfs'd locations (`/dev`, `/run`, `/tmp`, `/var/tmp`), and it sits in the *same* directory
as the actual built static assets, so a blanket tmpfs mount over that whole directory to make
just this one file writable would also hide the real JS/CSS bundle underneath it. Making this
container read-only correctly would need an nginx.conf change (serving `config.js` from a
separate, genuinely writable location via an `alias`) that hasn't been made or verified live —
left for a future pass rather than risking the one thing this container is deliberately designed
to do differently at every deploy.

### 7. `ReadOnlyTmpfs=` verified against Podman's real docs, not assumed — a real historical gotcha

Plain `podman run --read-only` defaults `--read-only-tmpfs=true` (auto-mounting a writable
tmpfs at `/dev`, `/run`, `/tmp`, `/var/tmp`), but Quadlet's own `ReadOnly=` directive had a real,
documented bug (`containers/podman#20439`) where it silently forced `--read-only-tmpfs=false`
instead of inheriting that same default — meaning a Quadlet unit with only `ReadOnly=true` could
end up with a *fully* read-only `/tmp` on an affected Podman version, not the usual writable-
tmpfs behavior. Checked directly against Podman's current documentation (not recalled from
training data, per CLAUDE.md's own standing instruction to verify fast-moving vendor behavior):
`ReadOnlyTmpfs=` now defaults to `true` in current Podman/Quadlet, so the historical bug is
fixed — but both Quadlet units set `ReadOnlyTmpfs=true` explicitly anyway, so this unit's own
intent doesn't quietly depend on whichever default a future Podman version ships. Not yet
verified live against bercy's actual Podman version.

### 8. A real bug found by testing on dev first, per the user's own instruction: rsync doesn't
`mkdir -p` a destination

Asked to verify the Quadlet hardening against dev (Compose) before touching bercy, rather than
just reading the diff and shipping it. That surfaced a genuine `create_backup` bug that no unit
test had caught: rsync only auto-creates the *last* missing path component of its destination.
`_rsync(..., f"{destination}/raw", ...)` works fine once `destination` itself already exists (the
steady-state case, e.g. this isn't the first backup ever taken), but a fresh
`PERSEVERER_BACKUP_PATH` that's never received a backup is missing *two* levels at once
(`destination` itself, then `destination/raw`), and rsync refuses that outright —
`rsync: [Receiver] mkdir ".../raw" failed: No such file or directory (2)`, exit code 11.

The existing unit tests never caught this because `tests/test_backup.py`'s rsync stand-in used
`dest_path.mkdir(parents=True, exist_ok=True)` — strictly more forgiving than the real binary,
so it silently absorbed exactly the gap a real backup destination would trip over. Fixed two
ways: `_ensure_destination_exists()` now runs once per `create_backup` call, before any `_rsync`
call, creating the destination root via a plain `Path.mkdir(parents=True)` for a local path or a
`ssh ... mkdir -p` round-trip for a `user@host:path` spec (`_is_remote()` tells the two apart);
and the test double was tightened to match rsync's real one-level-only behavior, so this class of
bug can't quietly regress again. Re-verified live end to end inside the running (hardened,
`read_only: true`) worker container: a backup to a destination missing two full path levels now
succeeds, and a restore from it reconstructs the original database and raw-archive content
correctly.

## Deliberately out of scope (stretch items, not committed)

- **The LLM narrative layer itself** — see decision 1. The reference design is documented there
  precisely so picking it back up doesn't require re-deriving the `insight.id`-instability
  problem or the caching approach from scratch.
- **Frontend container hardening** — see decision 6. Needs an nginx.conf change to relocate
  `config.js`'s serve path before `ReadOnly=true` can be applied safely there.
- **IP-based rate limiting / a WAF-style layer in front of the reverse proxy** — the
  username-keyed DB lockout (decision 4) covers the credential-stuffing threat this app
  actually has (a single-athlete login endpoint); a broader network-layer rate limit was judged
  out of scope for this pass.
