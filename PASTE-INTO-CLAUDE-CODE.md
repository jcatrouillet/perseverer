# Project kickoff: self-hosted fitness & health data platform

## 1. Your role

You are the lead engineer on a personal, self-hosted **fitness and health data platform**
running on my Synology NAS. I own the data; the goal is to never depend on a vendor cloud
again.

The system must:

1. Capture **every** data point my Garmin devices produce — activities, sleep, HRV, body
   battery, stress, SpO2, respiration, RHR, training status/readiness, VO2max, race
   predictions, body composition, steps, floors, intensity minutes, and anything I haven't
   thought of.
2. Store it in a **lightweight, portable, single-file-ish database** on the NAS.
3. Expose a **clean REST/JSON API**, Strava-shaped where that's a good fit, with simple
   token auth for writes so that **an AI agent can add notes and comments trivially**.
4. Serve a **fast web frontend** that works well on desktop and mobile.
5. Keep working when a source breaks, and stay extensible when new activity types or new
   health metrics appear (new watch, firmware update).

**Do not write application code yet.** Work through the phased plan in §9, starting with
Phase 0.

**Before you begin, ask me these** — they change your design and I'd rather answer than have
you guess:

- Any Python/JS version constraints from my existing homelab setup

## 2. Critical context — read this before designing anything

Your training data is likely out of date on both vendors. These are the facts as of
**August 2026**.

### Garmin

- **March 2026:** Garmin changed its authentication flow and added Cloudflare **TLS
  fingerprinting** that blocks non-browser HTTP clients (`requests`, `httpx`, `curl`,
  Node `fetch`).
- **`garth` is deprecated.** Final release 28 March 2026. Do not build on it. Existing saved
  OAuth1 tokens may work until expiry; new logins do not.
- **`python-garminconnect` ≥ 0.3.5** (4 June 2026) rebuilt login on **`curl_cffi`**
  (curl-impersonate bindings, spoofs Chrome/Android JA3 + HTTP/2 fingerprints) with several
  fallback SSO strategies and MFA callback support. This is currently the best library.
  It will break again.
- Garmin's SSO endpoints **429 rate-limit per account**, not per IP. Once tripped you cannot
  recover by changing network or user agent.
- The **official Connect Developer Program requires a legal entity**; personal-use
  applications are rejected. Not an option.
- Garmin Connect's **"Export Your Data"** GDPR archive is the only fully sanctioned, complete
  route. Manual and slow, but ground truth for backfill.
- Garmin watches store FIT files **on-device**: `/GARMIN/ACTIVITY/`, `/GARMIN/MONITOR/`,
  `/GARMIN/SLEEP/`, `/GARMIN/METRICS/`. Copying these over USB is the one path that cannot be
  broken by a vendor change. Verify this on my device model during Phase 1.

**`garmin_connect` is the primary ingestion path.** That makes the following hard
requirements, not nice-to-haves:

- **MFA re-auth is an interactive event that will happen unattended.** Tokens last roughly a
  year. Build a `sync auth login` CLI command that prompts for the MFA code and writes to the
  persisted token store, plus a **token-expiry countdown** surfaced in `/api/v1/healthz` and
  the settings page. Warn me at 30 days out, not on the day it dies.
- **One login per token lifetime, not one per run.** The scheduler resumes from the stored
  token and must *never* fall back to a credentialed login automatically. If the token is
  invalid, the run fails loudly and fires the staleness webhook. An automatic re-login loop
  is how accounts get 429-locked per-account with no recovery path.
- **Assume this adapter dies without warning, and understand that we have no live fallback.**
  I cannot plug the watch into a machine that reaches the NAS, so there is no USB FIT-copy path.
  When `garmin_connect` breaks, the *only* recovery route is a Garmin "Export Your Data" archive,
  which is a manual request that takes days to arrive. That has three consequences, all
  mandatory:
  1. **Always download the original FIT file for every activity**, not just the JSON summary.
     The FIT file is the device-native, vendor-independent artifact; JSON is a derived view of it.
     Everything archived as FIT survives Garmin's disappearance. This is non-negotiable even
     though it makes the sync slower.
  2. **Track export freshness as a first-class health signal.** Store `last_full_export_at`,
     surface "days since last full Garmin export" in settings and `/healthz`, and nag me on the
     dashboard every 90 days to request a fresh archive. The importer must accept overlapping
     archives idempotently so re-importing is always safe.
  3. **A broken adapter is a data-loss clock, not an inconvenience.** If `garmin_connect` has
     failed for more than 7 days, escalate the staleness webhook to a louder alert — at that
     point I need to be manually requesting an export, not waiting.

### Strava

**Do not build a Strava API adapter. Bulk export only.**

- The API is not closed — it is **paywalled**. Since 1 June 2026, Standard-tier access
  requires an active Strava subscription. I don't hold one, so the API is out of scope. No
  `strava_api` module, no OAuth flow, no `stravalib` dependency.
- The **bulk export ZIP** (Settings → Download or Delete Your Account → Request Archive) is
  my own data for personal use, unrestricted, containing original FIT/GPX/TCX files plus
  `activities.csv`. That's the entire Strava story: a one-off historical backfill for
  anything predating my Garmin history, re-runnable on demand.
- Strava is **lowest merge precedence, gap-fill only**. If an activity exists in both sources,
  Garmin wins every field.
- Strava's API terms prohibit using API-obtained data in AI models. That governs API access,
  not my own data export, so it doesn't bind us. **Still tag every field with its source** —
  if I ever add the API adapter, the quarantine must be enforceable in code rather than
  requiring a redesign.

**Design consequence:** ingestion is the fragile part of this system and the database is the
durable part. Architect so that *any* ingestion path can die without data loss and without
touching the schema, API, or frontend.

## 3. Non-negotiable principles

1. **Raw first, always.** Every byte fetched from any source is archived verbatim before
   parsing — original FIT/TCX/GPX bytes, and the raw JSON of every HTTP response, with request
   URL, timestamp, status, and SHA-256. Parsing is a *pure function over the archive*. If I
   later discover a field we ignored, I must be able to re-derive the whole database from the
   archive **without re-contacting any vendor**. This is the most important rule in the project.
2. **Never drop an unknown field.** Unmapped FIT fields (including developer fields and unknown
   message/field numbers) and unmapped JSON keys are auto-registered into a metric registry and
   stored, not discarded. Log them so I can promote them to first-class later.
3. **Idempotent, re-runnable ingestion.** Running any sync twice is a no-op. Content hashing
   plus upserts, never blind inserts.
4. **Provenance per field, not per record.** Every stored value knows its source and when.
5. **Never destructive.** No migration or sync deletes raw data. Soft-delete only.
6. **SI units in storage** (metres, seconds, m/s, kg, °C, W, bpm), converted at the presentation
   layer. Timestamps stored as UTC plus local UTC offset plus IANA timezone name.
7. **Additive schema evolution.** Adding a new activity type or health metric must require
   **zero migrations and zero code changes** — only a registry row.

## 4. Recommended stack (challenge this in your ADR if you disagree)

**Backend** — Python 3.12+, `uv`, FastAPI + Pydantic v2, SQLAlchemy 2.0 Core + Alembic.

**Database** — **SQLite** in WAL mode, single file on the NAS. Right call for "lightweight and
portable": one file to back up, zero server, comfortably handles a decade of one person's data.

- Do **not** put per-second stream samples in SQLite as rows — tens of millions of rows for
  marginal benefit. Store each activity's streams as a **Parquet file on disk**, referenced by
  path from `activity_stream`.
- Attach **DuckDB** read-only for analytical queries spanning SQLite metadata and Parquet
  streams in one statement.
- Backups: `VACUUM INTO` snapshots on a schedule plus rsync of the Parquet/raw archive. Verify
  restores automatically.

**FIT parsing** — official `garmin-fit-sdk` as primary (correct profile handling, developer
fields), `fitdecode` as a cross-check in tests.

**Garmin client** — `python-garminconnect` ≥ 0.3.5, isolated behind our own adapter interface
so it can be swapped in an afternoon.

**Frontend** — React 19 + Vite + TypeScript (strict) + TanStack Query + TanStack Router +
Tailwind. PWA.

- Charts: **uPlot** for dense activity streams (dramatically faster than alternatives at 10k+
  points), ECharts or Recharts for aggregate/dashboard views.
- Map: **MapLibre GL JS** with a **self-hosted Protomaps `.pmtiles` basemap on the NAS**. No
  API key, no bill, works offline, and my home location and route data never leave the NAS.

## 4b. Target environment — this constrains real decisions

**Development:** Windows, Docker Desktop with the WSL2 backend. **Production:** Synology
DS1019+, DSM 7.x Container Manager, **Intel Celeron J3455** (Apollo Lake / Goldmont, 4 cores,
1.5 GHz base, 8 GB RAM ceiling). Build locally first, then deploy.

This is a low-power 2017-era CPU and the platform must be designed for it, not merely made to
run on it:

- **x86_64, but Goldmont has no AVX or AVX2** — only up to SSE4.2. Any wheel or binary compiled
  for `x86-64-v3` will die with an illegal-instruction crash on the NAS. **Cap all native
  builds at `x86-64-v2`**, prefer manylinux wheels that runtime-dispatch (numpy, pyarrow, and
  DuckDB all do), and add a **smoke test that runs the full import path inside a container with
  AVX masked off**, so we catch this in CI rather than on the NAS at 4am. Do not add
  dependencies that hard-require AVX2.
- **Never build on the NAS.** The frontend build and any wheel compilation happen on Windows or
  in CI. Produce multi-stage Docker images whose final layer is runtime-only, push them to a
  registry (or export/import a tarball), and have the NAS pull. A Vite build on a J3455 is
  measured in double-digit minutes.
- **8 GB RAM total, shared with DSM.** Budget the whole stack at ~1.5 GB. This is another reason
  SQLite is right and Postgres is not. Cap SQLite page cache and the uvicorn worker count
  explicitly (2 workers, not `cpu_count()`), and stream large imports rather than loading a full
  export archive into memory.
- **Precomputed rollups are mandatory, not an optimisation.** This CPU cannot aggregate a decade
  of activities per request. Every dashboard, calendar and recap view reads from
  `*_rollup` tables, refreshed on ingest. If a view needs a scan at request time, that's a bug.
- **Serve pre-compressed static assets** (`.br` and `.gz` generated at build time, served with
  `Content-Encoding`). On-the-fly compression is a bad use of a Celeron.
- **Do the heavy work at ingest, off the request path.** FIT parsing, Parquet writing, polyline
  simplification, stream downsampling into low/medium/high tiers, and map heatmap tiles are all
  computed once by the `worker` container and stored. The API should mostly be reading
  pre-shaped bytes off disk.
- **Nice the worker.** Ingestion must never starve the API or DSM itself. Set CPU limits in
  compose and keep the nightly sync single-threaded.

**Windows-to-Linux portability**, because we develop on one and deploy on the other:

- Commit a `.gitattributes` enforcing LF for all source, and configure ruff/prettier accordingly.
- `pathlib` everywhere; never a hardcoded separator. No assumptions about case-insensitive
  filenames — Windows will happily let `Activity.FIT` and `activity.fit` collide, the NAS
  will not.
- **The `fit_folder` watcher must poll, not rely on inotify.** Files arriving on a Synology
  volume via SMB, rsync or Drive do not reliably generate inotify events visible inside a
  container. Poll on an interval with content hashing (dev on Windows would hide this bug
  completely — `watchdog` works fine there).
- Bind-mount paths differ between Docker Desktop and DSM. Keep every host path in `.env`, with
  separate `compose.override.yml` files for dev and NAS.
- Watch UID/GID: DSM containers need explicit `user:` mapping to write to a shared folder.
  Document the exact `chown` step in the deploy runbook.

**Deployment topology.** The stack sits behind a reverse proxy that terminates TLS; the app
containers bind to the compose network only and are never published directly to the host. DSM's
built-in reverse proxy is the likely front end — I already run a Let's Encrypt certificate issued
by DNS-01 challenge on this NAS, so reuse it rather than adding a second ACME client. My network
is double-NAT (ISP router plus UniFi), and internal access relies on split-horizon DNS rather
than hairpin NAT, so **do not assume the public hostname resolves the same way inside and outside
the house** — make the base URL configurable and never hardcode it in the frontend build.
Document the port-forward, proxy rule, header forwarding and certificate path in `docs/DEPLOY.md`.

Add a `docs/DEPLOY.md` in Phase 0 covering the Windows→NAS handoff, and verify the whole stack
actually starts on the DS1019+ at the end of Phase 3 rather than discovering platform problems
in Phase 9.

## 5. Data model

### Scale — design to these numbers

**~2,270 activities** (that's my Strava count; Garmin will differ, see below), over roughly a
decade. Expect on the order of 7 million activity stream samples across ~10 channels.

Rough storage budget once everything is archived — sanity-check your design against it and tell
me if you land more than 2× off:

| | Estimate |
|---|---|
| Raw FIT files (gzipped, content-addressed) | ~0.5 GB |
| Raw Garmin JSON responses (gzipped) | ~0.1–0.3 GB |
| Activity streams as Parquet | ~0.2 GB |
| Intraday health streams as Parquet | ~0.2 GB |
| SQLite metadata + daily observations | ~0.3 GB |
| **Total** | **under 2 GB** |

**This is a small dataset.** It comfortably validates SQLite and rules out any temptation toward
Postgres, TimescaleDB, InfluxDB or ClickHouse — do not propose them. It also means the real
performance constraint is the J3455, not data volume: correctness and query shape matter far
more than storage cleverness.

**Expect the Garmin activity count to differ from 2,270, in both directions.** Garmin will have
activities Strava never received (strength, yoga, indoor, non-GPS, short walks); Strava will
have manual entries and uploads from other apps that Garmin never saw. **This discrepancy is a
feature, not an error** — it's the whole reason for the merge engine. Never silently reconcile
to one number: produce a source-by-source count report and surface unmatched activities in the
UI for me to review.

### Intraday health data does not go in SQLite

Garmin records HR, stress, body battery and respiration at 1–3 minute resolution all day. Over a
decade that's roughly 10 million rows — enough to make SQLite unpleasant on this CPU for no
benefit. Split health storage in two:

- `health_observation` (SQLite) — **daily and summary values only**: RHR, HRV status, sleep
  score, training readiness, VO2max, weight, daily totals. Hundreds of thousands of rows, fully
  indexed, cheap to query.
- `health_stream` (Parquet, one file per metric per month) — **intraday series**. Row per
  sample: timestamp, value. Queried via DuckDB when a chart needs a day's detail, never scanned
  at request time for aggregates.

The same rule as activity streams: SQLite holds what you filter and join on, Parquet holds what
you plot.

### Multi-tenancy: build the scoping now, not the features

Today this is a single-athlete system. Family members may want accounts later, and retrofitting
tenancy into a mature schema is miserable. So:

- An `athlete` table exists from day one, seeded with exactly one row (me).
- **Every data table carries an `athlete_id` FK** — activities, health, notes, rollups, api_keys,
  ingest_runs, credentials. Every index that matters is composite on `(athlete_id, ...)`.
- **Scoping is enforced in the repository/query layer, not in individual endpoint handlers.**
  A query that can be written without an athlete filter is a bug waiting to leak someone's data.
  Add a test that fails if any data table lacks the column or any repository method omits the
  filter.
- Garmin credentials, token stores and sync schedules are **per athlete**.
- **Do not build** account management UI, invitations, sharing, permissions models, or a shared
  social feed. Those are speculative. Build the column and the scoping; that's the whole ask.

### Schema sketch

Design the full schema and present it as an ERD plus DDL for review before implementing.
Improve this sketch, don't just accept it:

**Bronze (immutable archive)**
- `raw_object` — id, source, kind, external_id, request_url, http_status, fetched_at, sha256,
  byte_size, storage_path (gzipped, content-addressed)

**Core**
- `athlete` — id, display_name, timezone, unit_preference, created_at (seeded with one row)
- `source` — garmin_connect, garmin_export, fit_folder, strava_export, manual
- `device`
- `activity` — ULID pk, start_time_utc, utc_offset_s, tz_name, sport, sub_sport, name,
  description, duration_s, moving_duration_s, distance_m, elevation_gain_m, calories,
  device_id, primary_source, created_at, updated_at, deleted_at
- `activity_source_link` — activity_id, source, external_id, raw_object_id, ingested_at
  (an activity can link to several sources; this is the merge audit trail)
- `activity_metric` — activity_id, metric_key, value_num, value_text, unit, source
  (open-ended summary metrics; this is what makes arbitrary future activity types free)
- `activity_stream` — activity_id, parquet_path, n_samples, channels JSON, sample_rate_hint
- `lap`, `split`, `segment_effort`
- `route_geom` — activity_id, encoded_polyline, simplified_polyline, bbox, start_lat, start_lng,
  end_lat, end_lng (indexed — powers the map start-point view)

**Health (open-ended by construction)**
- `health_observation` — id, metric_key, observed_at_utc, local_date, interval_start,
  interval_end, aggregation (instant|interval|daily), value_num, value_text, unit, source,
  device_id, raw_object_id. **Daily and summary values only** — see the intraday rule above.
- `health_stream` — metric_key, year_month, parquet_path, n_samples, source. Intraday series.
- `sleep_session` + `sleep_stage` — sleep is structured enough to earn dedicated tables
- `metric_definition` — metric_key, display_name, unit_si, category, value_type, chart_hints
  JSON, first_seen_at, first_seen_source, is_promoted.
  **Unknown metrics auto-insert here on first sight.** This table is how new watches are
  supported for free, and it's what the frontend and the agent introspect to know what exists.

**Derived (materialised, rebuildable)**
- `daily_rollup`, `weekly_rollup`, `monthly_rollup`, `yearly_rollup`
- `training_load` — date, ctl, atl, tsb, ramp_rate, method
- `personal_record`

**User content (never overwritten by sync)**
- `note` — id, subject_type (activity|day|week|month|year), subject_id, author, body_md,
  created_at, updated_at, created_by_key_id
- `field_override` — table, record_id, field, value, set_at (my edits always beat sync)
- `tag`, `activity_tag`

**Ops**
- `api_key` — id, name, token_hash (argon2id), scopes, created_at, last_used_at, revoked_at
- `ingest_run` — id, source, started_at, finished_at, status, watermark_from, watermark_to,
  items_seen, items_new, errors JSON

### Merge policy

Two records are the same activity when `|Δstart_time| ≤ 180s` **and** sport families match
**and** `|Δduration| ≤ max(60s, 5%)`. Thresholds configurable. Log every merge decision with
its inputs so I can audit false merges.

Field precedence: `field_override` (my edits) **>** garmin_export **>** garmin_connect **>**
fit_folder **>** strava_export **>** manual. Never delete a losing source record — the merged
activity is a *view*, and I must be able to see "Strava says 10.02 km, Garmin says 10.04 km."

## 6. Ingestion

Define one interface and implement it several times:

```python
class SourceAdapter(Protocol):
    name: str

    def health_check(self) -> AdapterHealth: ...
    def authenticate(self) -> None: ...
    def list_changed(self, since: datetime) -> Iterable[ObjectRef]: ...
    def fetch_raw(self, ref: ObjectRef) -> RawPayload: ...  # archives, returns raw_object_id
    def parse(self, raw_object_id: str) -> CanonicalBatch: ...  # pure, offline, re-runnable
```

Adapters:

1. **`garmin_connect`** — **the primary path.** `python-garminconnect` ≥ 0.3.5, daily
   incremental, unattended. This is where ongoing data comes from. It must be *timid*: see the
   rate-limit and MFA requirements in §2. Everything it fetches is archived raw before parsing,
   so a future break costs availability, never history.
2. **`fit_folder`** — watches a directory on the NAS and imports anything dropped in. I have no
   USB path to the watch, so this is **not a live capture path**; it is the **universal offline
   importer** and it earns its place three times over: it's how the Garmin export archive and
   the Strava export ZIP actually get ingested, it's the manual repair tool when something goes
   wrong, and it's how every other adapter gets tested offline against real files without
   touching the network. **Build it first, in Phase 1.**
3. **`garmin_export`** — imports the official Garmin "Export Your Data" ZIP. The completeness
   guarantee, and the recovery path if `garmin_connect` dies. Handles initial backfill.
4. **`strava_export`** — bulk archive ZIP importer. One-off historical gap-fill only, lowest
   merge precedence. No API adapter — see §2.
5. **`manual`** — via the write API.

### Historical backfill — do this via export, not scraping

A decade of history is roughly **2,270 activities**, and a full detail fetch per activity costs
several requests (summary, streams/FIT download, splits, HR zones, weather). That's on the order
of **10,000+ requests**. Against endpoints that 429-lock *per account* with no IP-based recovery,
scraping the backfill is the single riskiest thing we could do in this project — it risks locking
out the account we depend on daily, before the system has any data in it at all.

**Therefore:** the initial backfill comes from the **Garmin "Export Your Data" archive**, imported
offline with zero network calls. `garmin_connect` handles only the incremental daily delta from
that point forward. Build the importer in Phase 2 *before* the live adapter, and don't let me talk
you into the other order.

If a targeted gap-fill from `garmin_connect` is ever needed for a specific date range, it must be
resumable from a checkpoint, throttled to a configurable rate (default no faster than one request
every 3 seconds), hard-capped per hour, and abort-on-first-429 rather than retrying.

### Daily sync

- APScheduler in the `worker` container, default 04:15 local, jittered.
- **Rolling re-fetch window, default 10 days.** Garmin retroactively revises sleep, body
  battery, training status and HRV baselines; a pure high-watermark sync silently loses those
  corrections. Configurable.
- Every run writes an `ingest_run` row, exposed at `/api/v1/ingest/runs` and surfaced in the
  frontend as a traffic light.
- **Staleness alerting:** if any adapter hasn't succeeded in N days, fire a configurable
  outbound webhook with a JSON body. (I run a WhatsApp gateway on this network — a generic
  webhook is all you need to build.)
- Token store persisted to a named Docker volume, never rebuilt on container restart.

## 7. API

`/api/v1`, REST, JSON, OpenAPI 3.1 auto-generated with real descriptions and examples.

```
GET  /athlete                     GET  /athlete/stats
GET  /activities                  ?after= &before= &sport= &cursor= &per_page= &q=
GET  /activities/{id}
GET  /activities/{id}/streams     ?keys=time,latlng,heartrate,altitude,cadence,power,temp
                                  &resolution=low|medium|high
GET  /activities/{id}/laps        /splits   /zones   /sources
POST /activities                  PATCH /activities/{id}
GET|POST /activities/{id}/notes   PATCH|DELETE /notes/{id}
GET|PUT  /activities/{id}/tags
GET  /health/observations         ?metrics= &from= &to= &aggregation=
GET  /health/sleep                /health/hrv   /health/body-composition
GET  /metrics                     # the metric_definition registry — self-describing
GET  /fitness                     # CTL / ATL / TSB series
GET  /recaps/{week|month|year}/{key}
GET  /map/starts                  ?bbox= &sport=   → GeoJSON FeatureCollection
GET  /map/heatmap                 ?bbox= &zoom=
GET  /search
GET  /ingest/runs                 POST /ingest/runs   # trigger a sync
GET  /healthz  /version  /openapi.json
```

**Auth. This service will be reachable from the public internet via a reverse proxy, so treat
it as internet-facing from Phase 3 — not as a LAN toy that gets hardened later.**

Two credential types, one authorization model:

- **Humans (browser):** password login → short-lived session cookie (`Secure`, `HttpOnly`,
  `SameSite=Lax`), refreshed on activity. Password hashed with argon2id. **Forced credential
  setup on first run** — no default account, no default password, refuse to start with an unset
  admin secret. Support TOTP 2FA; it's cheap and this is health data on a public endpoint.
- **Agents (API):** Bearer API keys — `Authorization: Bearer <token>`, `X-API-Key` also accepted.
  Generated by CLI, argon2id-hashed at rest, shown once, scoped to one athlete, individually
  revocable, with `last_used_at` tracked. An agent should still be able to post a note with one
  `curl`.

Scopes: `read`, `write:annotations`, `write:activities`, `admin`. Admin-scoped endpoints
(`POST /ingest/runs`, key management, athlete management) require a session *or* an `admin` key,
never an ambient LAN check.

**There is no unauthenticated read path.** Drop any notion of an RFC1918 bypass — a reverse proxy
makes source-IP trust meaningless anyway, since everything arrives from the proxy.

Non-negotiable for public exposure:

- **Rate limiting on every endpoint**, tighter on auth endpoints. Progressive backoff plus
  temporary lockout on repeated auth failures. Log every failed attempt with the
  `X-Forwarded-For` chain.
- Constant-time token comparison. Never log a token or a session ID, even at debug level.
- Security headers: HSTS, a real CSP (MapLibre and the tile path will need explicit allowances —
  do not fall back to `unsafe-inline`), `X-Content-Type-Options`, `Referrer-Policy: no-referrer`,
  `X-Frame-Options: DENY`.
- Strict CORS allowlist. No wildcard.
- Trust `X-Forwarded-*` **only** from the known proxy IP, configured explicitly. Getting this
  wrong makes rate limiting and audit logs trivially spoofable.
- Request size limits and a hard cap on `per_page` — an unbounded `per_page` is a free DoS on a
  Celeron.
- The API container does **not** terminate TLS and does **not** bind to `0.0.0.0` on the host;
  it listens on the compose network only, and the proxy is the sole ingress.
- An `audit_log` table for auth events, key creation/revocation, and admin actions.

**One thing I want you to raise with me in the Phase 3 ADR, not silently decide:** this exposes a
decade of health data and my home location to the public internet, secured by our own code.
Present the option of putting the *human* UI behind Tailscale and exposing *only* the API-key
surface publicly for agent access. Give me the tradeoff honestly; I'll make the call.

**Agent ergonomics — a first-class requirement, not a nice-to-have:**

- **Ship an MCP server in the same repo** wrapping the REST API (`mcp/` package, stdio
  transport). This is the difference between "an agent *can* use it" and "an agent *will* use
  it." Tools: `search_activities`, `get_activity`, `get_streams`, `get_health_series`,
  `list_metrics`, `add_note`, `get_recap`.
- Every response carries explicit `unit` fields. Never return a bare number whose unit is
  implied by the key name.
- `Idempotency-Key` header honoured on all POSTs.
- Consistent error envelope: `{"error": {"code": "...", "message": "...", "details": {...}}}`.
- Cursor pagination, `ETag` + `If-None-Match`, `?fields=` sparse fieldsets.
- Server-side stream downsampling (LTTB, preserving peaks) so a mobile client never pulls 50k
  points.

## 8. Frontend

Eight views. Cherry-pick the specific thing each reference product does well.

1. **Feed** *(Strava)* — reverse-chronological cards, static map thumbnail, headline stats,
   inline note composer. My activities only.
2. **Activity detail** — the centrepiece.
   - **Route playback** *(Strava)*: MapLibre plus a scrubber and play/pause. Animated marker,
     trailing path, variable speed (1×/4×/16×/whole-run-in-30s). The scrubber position drives a
     synchronised crosshair across every chart below.
   - **Chart stack** *(Garmin)*: pace, HR, cadence, elevation, power, temperature, ground
     contact time, vertical oscillation/ratio, stride length, respiration. Charts are
     **generated from the metric registry**, not hardcoded — a new stream channel from a future
     watch appears automatically.
   - Lap table, splits, HR/pace zone distribution, weather at start time.
   - **Analysis panel** *(Smashrun)*: **rules-only through Phase 8.** A deterministic insight
     engine emitting typed `Insight` objects — negative/positive split, fade %, aerobic
     decoupling, cadence drift, "3rd fastest 10k this year", "longest run since March",
     heat-adjusted pace, streak status, effort vs. recent baseline. Each insight carries a
     `kind`, a `tone`, the numbers behind it, and a plain-language template. Rules-only is not a
     downgrade: it's reproducible, instant, free, and testable, and Smashrun's charm is mostly
     good thresholds rather than good prose. **Design the `Insight` type so a narrative layer
     can consume it in Phase 9** — the LLM should summarise structured insights, not re-derive
     them from raw streams.
3. **Calendar / list** *(intervals.icu)* — week-per-row calendar, sport-coloured blocks, weekly
   totals in a gutter column. Toggle to a dense sortable table.
4. **Fitness & Form** *(intervals.icu)* — CTL/ATL/TSB, weekly volume bars, threshold and VO2max
   estimates over time, PB progression curves.
5. **Health dashboard** *(Garmin)* — sleep stage stacked bars, HRV trend with baseline band,
   RHR, body battery, stress, SpO2, respiration, weight/body comp, training readiness and
   status. Plus a **correlation explorer**: pick any two metrics from the registry and
   scatter/lag-plot them.
6. **Map explorer** — all start points clustered on one map, plus an all-routes heatmap. Filter
   by sport and date range, click through to the activity. **Privacy zones are mandatory here,
   not optional** — this endpoint is publicly reachable and a decade of start points draws a
   bright arrow at my front door. Configurable home/work radii, applied **server-side** by
   trimming route geometry and fuzzing start points before the data leaves the API. Never
   implement this as client-side masking of data you already sent.
7. **Recaps** — week / month / year. Totals, sport split, elevation, PBs set, streaks,
   consistency, year-over-year deltas, and a "best of" reel. Exportable as an image.
8. **Settings** — API key management, per-adapter sync health, Garmin token expiry countdown,
   metric registry browser, manual re-import triggers, backup status.

**Performance:** route-level code splitting, virtualised lists, server-side downsampling,
prefetch on hover/intent, `ETag` caching, service worker for offline reads of recent activities.
Budget: **interactive in under 1.5 s on a mid-range phone over LAN — measured against the
DS1019+, not against my dev machine.** Benchmark on the NAS before declaring any view done; a
J3455 is roughly an order of magnitude slower than the dev box, so a view that feels instant in
development can be unusable in production. Mobile gets bottom tab navigation and touch-drag
chart scrubbing.

## 9. Phased delivery — do not skip ahead

**At the start of each phase:** write a short plan and an ADR in `docs/adr/`, then stop for my
approval. **At the end of each phase:** tests pass, the acceptance criterion is demonstrably
met, and you've updated `CLAUDE.md` and `docs/DATA_DICTIONARY.md`.

| Phase | Scope | Acceptance criterion |
|---|---|---|
| **0** | Repo skeleton, `CLAUDE.md`, `docs/DEPLOY.md`, docker-compose (+ dev/NAS overrides), CI (ruff, mypy strict, pytest, tsc, AVX-masked import smoke test), config via env + TOML | `docker compose up` gives a healthy `/healthz` on Windows |
| **1** | Raw archive, FIT parser, `fit_folder` adapter, SQLite schema, Alembic migrations, merge engine core | Drop 100 real FIT files: all parse, unknown fields land in `metric_definition`, re-running imports zero duplicates, and the DB is fully rebuildable from the archive after deleting it |
| **2** | `garmin_export` ZIP importer **first**, then **`garmin_connect` as the primary adapter** for incremental delta only (always downloading original FIT files), auth/MFA CLI, token-expiry warning, export-freshness tracking, scheduler, `ingest_run` observability, staleness webhook | Full historical backfill loaded from the export archive with **zero network calls**; a **source-by-source count report** reconciles against my ~2,270 Strava activities and lists unmatched ones rather than silently picking a number; **every synced activity has its original FIT archived**; a daily incremental run completes unattended and is idempotent; a simulated 429 aborts rather than retry-looping; re-importing an overlapping export archive is a clean no-op |
| **3** | Read API + OpenAPI + session/API-key auth + **public-exposure hardening**, athlete scoping, **first deployment to the DS1019+ behind the reverse proxy** | Every §7 GET returns real data; OpenAPI validates; **no endpoint is reachable unauthenticated**; rate limiting and auth lockout demonstrated; the athlete-scoping test passes; **the full stack runs on the NAS behind TLS and a full-history `/activities` query returns in under 500ms there** |
| **4** | Write endpoints, notes/tags/overrides, **MCP server** | I can add a note to an activity with one `curl`, and Claude Code can do it via MCP |
| **5** | Frontend shell, feed, activity detail with map playback + chart stack | Playback is smooth at 60fps on my phone for a 2-hour run |
| **6** | Calendar view, Fitness & Form, Health dashboard | Weekly/monthly aggregates reconcile exactly against Garmin Connect's own numbers |
| **7** | Map explorer, recaps, PWA/offline | Full-history start-point map renders in under 1s |
| **8** | `strava_export` importer, merge conflict UI, **rules-based insight engine** | An activity present in both sources shows one merged record with both sources inspectable; insights are deterministic and unit-tested against fixture activities |
| **9** | LLM narrative layer over the `Insight` objects (cached in DB, regenerated on demand), backup + restore automation, hardening | Automated restore-from-backup test passes in CI; narratives are cached and the app is fully functional with the LLM disabled |

## 10. Ways of working

- Write `CLAUDE.md` in Phase 0: architecture summary, the invariants from §3, commands, and the
  "raw first, never drop a field" rule stated prominently.
- **Ask me questions rather than guessing** — especially on unit semantics, timezone edge cases,
  and merge thresholds.
- Tests are not optional. Property-based tests (Hypothesis) for the merge engine and unit
  conversions. Golden-file tests for FIT parsing using anonymised fixtures.
- Every external HTTP call is recorded in `raw_object`. No exceptions, including failures.
- Prefer boring, well-maintained dependencies. Each new dependency needs a one-line
  justification in the ADR.
- Secrets in a gitignored `.env`, with a committed `.env.example`.
- When a vendor library breaks — and it will — the fix must be confined to one adapter file. If
  you ever find yourself changing the schema because Garmin changed a JSON key, the design is
  wrong: stop and tell me.

Start with Phase 0. Ask me the setup questions from §1 first.
