# API Reference

The source of truth for integrating with the Perseverer REST API — every
endpoint, parameter (required/optional, default), and response shape. Generated from the live
OpenAPI schema (`GET /openapi.json`) and cross-checked against router source for the details
OpenAPI doesn't capture (auth semantics, error codes, enumerable string fields). Grows every
phase, alongside `CLAUDE.md` and `DATA_DICTIONARY.md`, per the project's "ways of working" rule.

A polished, navigable HTML version of this same reference (search, click-through schema links,
copyable curl examples) is served by the frontend container at `/api-docs.html`
(`frontend/public/api-docs.html`) — this file is the plain-text/version-controlled twin of that
page, kept in sync by hand when endpoints change.

Base path: every endpoint below is prefixed `/api/v1`. Base URL is deployment-specific
(`PERSEVERER_API_BASE_URL`) — there is no public multi-tenant host.

## Authentication

Every route requires a credential **except** `GET /healthz`, `GET /version`, and
`POST /auth/login` itself (login *is* the credential check). Present exactly one of:

| Method | Header | Resolves to |
|---|---|---|
| Shared API key | `X-API-Key: <PERSEVERER_API_KEY>` | The deployment's default athlete. Simplest option for scripts, the MCP server, single-athlete deployments. |
| Per-athlete API key | `X-API-Key: <key from sync athlete create-key>` | The specific athlete the key was minted for (matched via SHA-256 hash against `athlete.api_key_hash`). |
| Session token | `Authorization: Bearer <JWT from POST /auth/login>` | The athlete who logged in. What the web frontend uses. Expires after `PERSEVERER_JWT_EXPIRY_DAYS` (default **30 days**); no refresh endpoint — log in again once expired. |

There is exactly one athlete per deployment credential — every endpoint is implicitly scoped to
the resolved athlete, so there is never an `athlete_id` parameter to pass yourself.

```bash
# Shared or per-athlete key
curl -H "X-API-Key: YOUR_API_KEY" \
  https://your-perseverer-host/api/v1/activities?limit=10

# Session token
curl -X POST https://your-perseverer-host/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "jerome", "password": "YOUR_PASSWORD"}'
# => {"access_token": "eyJhbGciOi...", "expires_at": "2026-09-18T12:00:00Z"}

curl -H "Authorization: Bearer eyJhbGciOi..." \
  https://your-perseverer-host/api/v1/activities?limit=10
```

**Failure modes:**

| Status | When |
|---|---|
| `401` | No credential presented (and the server has at least one auth method configured), or the credential is invalid, malformed, or expired. |
| `503` | The server has **neither** `PERSEVERER_API_KEY` nor `PERSEVERER_JWT_SECRET` configured — auth fails closed, never open. A deployment misconfiguration, not something a client request can fix. |

## Errors

Errors return a JSON body with a `detail` field.

**422 · Validation error** — standard FastAPI shape, identical across every endpoint, so
documented once here rather than repeated per endpoint below:

```json
{
  "detail": [
    {
      "loc": ["query", "limit"],
      "msg": "Input should be a valid integer",
      "type": "int_parsing",
      "input": "abc"
    }
  ]
}
```

`loc` is a path to the offending field (`["query", name]`, `["path", name]`, or `["body", ...]`);
`type` is a stable machine-readable error code; `ctx` may add constraint details (e.g.
`{"ge": 1}`).

| Status | Meaning |
|---|---|
| `200` | Success. |
| `201` | Resource created (only `POST /notes`). |
| `400` | Well-formed but semantically invalid for this operation (bad `tier`/`channels` combination, unsupported goal `period_type`, splitting an activity's only source). `detail` is a plain string. |
| `401` | Missing or invalid credential — see Authentication. |
| `404` | The path references an id that doesn't exist, isn't yours, or is soft-deleted. `detail` is a plain string, e.g. `"activity not found"`. |
| `422` | Request validation failure — see above. |
| `503` | Server-side auth misconfiguration — see Authentication. |

## Conventions

**Units.** Every numeric value is **SI**: metres, seconds, m/s, kilograms, °C, watts, bpm. No
unit-conversion query parameter — convert at display time client-side.

**Timestamps & dates.** Two distinct shapes, not interchangeable:

| Field pattern | Type | Meaning |
|---|---|---|
| `*_utc` | `string (date-time)` | An instant, always UTC, ISO-8601 (`2026-08-15T06:32:11Z`). |
| `local_date` | `string (date)` | A calendar date with no time component (`YYYY-MM-DD`), in whatever timezone the activity/observation actually happened in — not UTC-shifted. |
| `utc_offset_s` | `integer` | Seconds east of UTC in effect at `start_time_utc`. |

`start_date`/`end_date` query parameters are always `local_date`-typed and both bounds are
**inclusive**.

**Pagination.** The two endpoints that can return unbounded result sets (`GET /activities`,
`GET /health/observations`) use the same envelope:

| Field | Type | |
|---|---|---|
| `items` | array | This page's results. |
| `total` | integer | Total matching rows across every page. |
| `limit` | integer | Page size applied. |
| `offset` | integer | Offset applied. |

Every other list-returning endpoint returns a plain unbounded JSON array — all backed by
precomputed rollups or otherwise-bounded queries.

**`"available": false` instead of a fabricated value.** Activity weather, activity location,
and goal progress describe data that may genuinely not exist yet. They return `200` with
`"available": false` and every other field `null`/omitted, never a `404` or a guessed value.
Always check `available` before reading the rest of the object.

**Multi-value query parameters** are repeated, not comma-joined: `?metric_key=a&metric_key=b`.
The one exception is `ids` on `GET /activities/routes`, a single comma-separated string.

**Nullable fields.** In every table below, `nullable` in the type column means the field may
legitimately be `null` — most commonly because a source device never recorded that signal (e.g.
`avg_hr_bpm` on a pool swim with no HR strap), not an error.

---

## Auth

### `POST /auth/login`

Exchanges a username and password for a session token. Not gated by an API key — this endpoint
is itself the credential check. Uses constant-time password comparison, including a dummy-hash
comparison for unknown usernames, so a failed login never reveals via timing whether the
username exists.

**Request body** (`LoginRequest`):

| Field | Type | Required |
|---|---|---|
| `username` | string | required |
| `password` | string | required |

**Responses:**

| Status | Body |
|---|---|
| `200` | `LoginResponse`: `access_token` (string, JWT), `expires_at` (string, date-time) |
| `401` | `detail: "invalid username or password"` |
| `503` | `detail: "JWT signing not configured"` (`PERSEVERER_JWT_SECRET` unset) |

---

## Activities

### `GET /activities`

List activity summaries, most recent first, paginated.

| Param | In | Required | Type | Default | Description |
|---|---|---|---|---|---|
| `start_date` | query | optional | string (date) | — | Only activities with `local_date` ≥ this. |
| `end_date` | query | optional | string (date) | — | Only activities with `local_date` ≤ this. |
| `sport` | query | optional | string | — | Exact match, e.g. `running`. |
| `limit` | query | optional | integer | `50` | 1–500. |
| `offset` | query | optional | integer | `0` | ≥ 0. |

**Response `200`:** `Page<ActivitySummary>` (see [Schema Reference](#schema-reference)).

### `GET /activities/years`

Distinct years (descending) with ≥1 activity — a cheap aggregate powering the calendar's year
selector without paginating through every activity's `local_date`.

**Response `200`:** `array<integer>`, e.g. `[2026, 2025, 2024]`.

### `GET /activities/map`

Every GPS-bearing activity's start point, in one unbounded response, for the map explorer.
Activities without GPS are simply absent — never returned with null coordinates.

| Param | In | Required | Type | Default |
|---|---|---|---|---|
| `start_date` | query | optional | string (date) | — |
| `end_date` | query | optional | string (date) | — |
| `sport` | query | optional | string | — |

**Response `200`:** `array<ActivityMapPointOut>`.

### `GET /activities/routes`

Batch simplified-polyline lookup for a set of activity ids — one request for a page of
thumbnail-map cards. Activities with no GPS route are simply omitted.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `ids` | query | **required** | string | Comma-separated activity ids (the one exception to the multi-value convention above). |

**Response `200`:** `array<ActivityRouteOut>`.

### `GET /activities/{activity_id}`

Full detail for one activity: laps, splits, route bounding box, attributed device, every stored
per-activity metric.

| Param | In | Required | Type |
|---|---|---|---|
| `activity_id` | path | **required** | string (ULID) |

**Responses:** `200` → `ActivityDetail`. `404` → `detail: "activity not found"`.

### `GET /activities/{activity_id}/context`

A deliberately honest same-sport comparison — not a reproduction of any vendor's proprietary
performance-condition model. `percentile_rank` is the share of the athlete's own same-sport
activities within ±15% distance that this one beat or tied, by effective pace; `null` when
there's nothing comparable yet. `recent` covers the 90 days up to and including this activity's
own date, not "today" — browsing an old activity shows its own contemporaries.

**Responses:** `200` → `ActivityContextOut`. `404`.

### `GET /activities/{activity_id}/insights`

Point-in-time insights for one activity (e.g. "your fastest 10 km to date"), computed fresh at
request time — unlike `GET /insights`, which reads a precomputed table. Always bounded to
activities at or before this one's own `local_date`: browsing an old activity never leaks
knowledge of activities that hadn't happened yet.

**Responses:** `200` → `array<InsightOut>`. `404`.

### `GET /activities/{activity_id}/weather`

Temperature/humidity range, feels-like temperature, wind speed/direction, and a WMO weather
code for the activity's time window, from Open-Meteo's historical archive, cached forever once
fetched. `available: false` when there's no GPS start point or the fetch came back empty.

**Responses:** `200` → `ActivityWeatherOut`. `404`.

### `GET /activities/{activity_id}/location`

Reverse-geocoded city/town/park name for the activity's GPS start point (Nominatim, cached
forever once resolved). `available: false` when there's no GPS start point, nothing cached yet,
or the lookup came back empty. A cache miss triggers a background fetch and returns immediately
rather than blocking on Nominatim's 1 req/s throttle — the name appears on the next fetch.
`sync backfill-locations` pre-warms the cache for the existing archive.

**Responses:** `200` → `ActivityLocationOut`. `404`.

### `GET /activities/{activity_id}/workout`

The pre-planned workout structure attached to this activity (Garmin Connect's Workout Builder),
if any — `null` for the large majority of activities with no such plan. Steps are returned raw
(unflattened repeat blocks), matching the FIT file's own encoding.

**Responses:** `200` → `ActivityWorkoutOut` or bare `null`. `404`.

### `GET /activities/{activity_id}/sources`

Every raw source this activity's data came from, and the merge decisions that linked them —
makes cross-source merges (e.g. the same run from both a Garmin watch and Strava's auto-upload)
inspectable. Sibling read half of the split endpoint below.

**Responses:** `200` → `ActivitySourcesOut`. `404`.

### `POST /activities/{activity_id}/sources/{link_id}/split`

Undoes a wrong merge: re-derives one source's data into a brand-new activity from that source's
own already-archived raw bytes. Never destructive — the original activity keeps every other
source it had.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `activity_id` | path | **required** | string | |
| `link_id` | path | **required** | integer | From `ActivitySourceOut.link_id`, see `GET .../sources`. |

**Responses:** `200` → `ActivitySplitOut`. `400` → `link_id` is the activity's only source,
nothing to split away from. `404` → activity not found, or source link not found on this
activity.

### `PATCH /activities/{activity_id}/sport`

Corrects an activity's sport/sub_sport when the raw source itself recorded the wrong one (e.g. a
hike logged under a watch's "Run" profile) with no second raw source to auto-detect the mismatch.
Durable, rebuild-safe override — not a direct column edit.

**Request body** (`ActivitySportOverrideIn`):

| Field | Type | Required |
|---|---|---|
| `sport` | string | required |
| `sub_sport` | string, nullable | optional (omit to leave unset) |

**Responses:** `200` → `ActivitySportOverrideOut`. `404`.

### `PATCH /activities/{activity_id}/race`

Marks/unmarks an activity as a race — corrects Garmin's own `eventTypeId` heuristic for the rare
case a real race was never flagged inside the Garmin Connect app. Durable, rebuild-safe override.

**Request body** (`ActivityRaceOverrideIn`): `is_race` (boolean, required).

**Responses:** `200` → `ActivityRaceOverrideOut`. `404`.

### `PATCH /activities/{activity_id}/name`

Renames an activity — Garmin Connect's own name isn't reliably a real athlete-given title, so
there's no safe automatic rule for cleaning up a generic one. Durable, rebuild-safe override.

**Request body** (`ActivityNameOverrideIn`): `name` (string, required).

**Responses:** `200` → `ActivityNameOverrideOut`. `404`.

### `PATCH /activities/{activity_id}/fueling`

Records the athlete's own carbohydrate/sodium intake during the activity. Unlike the three
corrections above, there's no vendor source for this at all — neither the FIT profile nor the
Garmin Connect API carry it (confirmed by introspecting both directly) — so this is the
athlete's only input, not a fix to something derived. Durable, rebuild-safe override, same
mechanism as sport/race/name. Both fields are set together: send the current value of whichever
one you're not changing, or it's cleared back to null.

**Request body** (`ActivityFuelingIn`):

| Field | Type | Required |
|---|---|---|
| `carbohydrates_g` | number, nullable | optional (omit or null to clear) |
| `sodium_mg` | number, nullable | optional (omit or null to clear) |

**Responses:** `200` → `ActivityFuelingOut`. `404`. `422` → a negative value.

### `GET /activities/{activity_id}/stream`

Downsampled per-second sensor stream for charting, bucket-averaged server-side (DuckDB) to one
of three point-count tiers.

| Param | In | Required | Type | Default | Description |
|---|---|---|---|---|---|
| `activity_id` | path | **required** | string | — | |
| `tier` | query | optional | string | `medium` | `low` (~200 pts), `medium` (~1000 pts), `high` (~20000 pts). |
| `channels` | query | optional | array\<string\> | — | Which channels to return. Omit for every available channel. Available names: `heart_rate`, `power`, `temperature`, `speed_mps`, `altitude_m`, `respiration_rate`, `distance_m`, `cadence`, `lat`, `lon` — an activity only has the channels its device actually recorded. |

**Responses:** `200` → `StreamResponse`. `400` → a requested channel isn't available for this
activity, or another downsample-argument mismatch. `404` → activity not found, or no stream data
for this activity.

---

## Health

### `GET /health/observations`

Raw health observations for one or more metrics over a date range — the low-level building
block behind the health dashboard.

| Param | In | Required | Type | Default | Description |
|---|---|---|---|---|---|
| `metric_key` | query | **required** | array\<string\> | — | Repeat per key: `?metric_key=a&metric_key=b`. |
| `start_date` | query | **required** | string (date) | — | |
| `end_date` | query | **required** | string (date) | — | |
| `limit` | query | optional | integer | `500` | |
| `offset` | query | optional | integer | `0` | |

**Response `200`:** `Page<HealthObservationOut>`.

### `GET /health/dashboard`

Curated per-day metrics for the health dashboard — merges each logical metric's several raw
vendor `metric_key` namespaces (e.g. Garmin's several export report kinds, or a Eufy alias) into
one series per human-meaningful `logical_metric` (`steps`, `resting_hr_bpm`, `weight_kg`,
`sleep_score`, `body_fat_pct`, …).

| Param | In | Required | Type |
|---|---|---|---|
| `start_date` | query | **required** | string (date) |
| `end_date` | query | **required** | string (date) |

**Response `200`:** `HealthDashboardOut`.

---

## Sleep

### `GET /sleep`

Sleep sessions in a date range, each with its stage breakdown (`light`/`deep`/`rem`/`awake`)
when the source device reported one.

| Param | In | Required | Type |
|---|---|---|---|
| `start_date` | query | **required** | string (date) |
| `end_date` | query | **required** | string (date) |

**Response `200`:** `array<SleepSessionOut>`.

---

## Calendar

### `GET /calendar`

Per-day rollups (activity totals + health-metric rollups) for the calendar grid — read from
precomputed rollup tables, never a live aggregate over raw history.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `start_date` | query | **required** | string (date) | |
| `end_date` | query | **required** | string (date) | |
| `metric_keys` | query | optional | array\<string\> | Restrict which `health_metrics` entries come back per day. Omit for every rolled-up key. |

**Response `200`:** `CalendarResponse`.

### `GET /calendar/weeks`

Weekly rollups (Monday-start) for a date range. `period_type` in the response is always
`"week"`.

**Response `200`:** `PeriodCalendarResponse`.

### `GET /calendar/months`

Monthly rollups for a date range. `period_type` in the response is always `"month"`.

**Response `200`:** `PeriodCalendarResponse`.

(Both take the same `start_date`/`end_date` required query parameters as `GET /calendar`.)

---

## Fitness

### `GET /fitness`

Daily CTL/ATL/TSB fitness-and-form series — an independently computed Coggan/Banister EWMA
(CTL = 42-day, ATL = 7-day) over daily training load, shown alongside (not reconciled against)
any vendor's own training-status metric.

| Param | In | Required | Type |
|---|---|---|---|
| `start_date` | query | **required** | string (date) |
| `end_date` | query | **required** | string (date) |

**Response `200`:** `array<FitnessDailyRollupOut>`.

---

## Insights

### `GET /insights`

Athlete-wide, deterministic rules-based insights (not LLM-generated) — personal bests, streaks,
notable efforts, training-load flags, health-metric callouts — refreshed on every ingest run.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `kind` | query | optional | string | Filter to one kind: `effort`, `health`, `load`, `pb`, `streak`. |
| `window` | query | optional | string | Filter to one window: `current`, `30d`, `90d`, `180d`, `365d`, `all_time`. |

**Response `200`:** `array<InsightOut>`.

### `GET /insights/pace-bands`

Total time-in-pace-band across the athlete's whole running history, one row per fixed 30-second/km
band (fast to slow), from every running activity's own per-second (well, per-sample — real device
cadence, not a synthesized fixed grid) speed stream — a run's *instantaneous* pace at each moment,
not its whole-activity average. Precomputed at ingest time and stored per-activity (see
`pace_bands.py`); this endpoint is a plain bounded `SUM`/`GROUP BY` over those already-stored
rows, never a live stream scan. Always returns every band, even ones with `seconds: 0`, so a
client can render every column without checking for missing entries.

**Response `200`:** `array<PaceBandOut>`.

### `GET /insights/pace-bands/by-activity`

The same precomputed per-activity time-in-band rows `GET /insights/pace-bands` sums athlete-wide,
here grouped by activity instead — one row per running activity that has at least one non-zero
band, each listing its own full `bands` breakdown. Powers a composition-over-time view (what
share of *this one run* was spent at each pace) as opposed to the athlete-wide aggregate above.
Still a plain bounded query over already-stored rows, never a live stream scan.

**Response `200`:** `array<ActivityPaceBandsOut>`.

---

## Notes

### `POST /notes`

Attaches a free-text note to an activity or a calendar day — the write path an AI agent (or the
UI) uses to record observations. The one write endpoint most of this API is deliberately
read-only around.

**Request body** (`NoteCreate`):

| Field | Type | Required | Description |
|---|---|---|---|
| `entity_type` | `"activity"` \| `"day"` | required | |
| `entity_id` | string | required | An activity id when `entity_type` is `activity`; a `local_date` (`YYYY-MM-DD`) string when `entity_type` is `day`. |
| `body` | string | required | |
| `author` | string, nullable | optional | |

**Responses:** `201` → `NoteOut`. `404` → `detail: "activity not found"` — only checked when
`entity_type` is `activity`; a `day` entity_id is never validated against anything, since a day
is just a date, not a row that can be missing.

### `GET /notes`

Lists notes attached to one entity.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `entity_type` | query | **required** | string | `activity` or `day`. |
| `entity_id` | query | **required** | string | |

**Response `200`:** `array<NoteOut>`.

---

## Goals

### `GET /goals`

Progress toward a distance goal for one period: cumulative distance so far vs. the flat
pace-to-target line, plus a day-by-day series for a progress chart. `available: false` when no
goal is configured for this exact `period_type`/`period_start`/sport combination.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `period_type` | query | **required** | string | `year` or `month`. |
| `period_start` | query | **required** | string | `"YYYY"` for `year`, `"YYYY-MM"` for `month`. |

**Response `200`:** `GoalProgressOut`.

### `PUT /goals`

Creates or replaces the distance goal for one period. Upsert keyed on `(period_type,
period_start, sport)` — calling this again for the same period and sport replaces the existing
goal rather than creating a second one.

**Request body** (`GoalIn`):

| Field | Type | Required | Description |
|---|---|---|---|
| `period_type` | string | required | `year` or `month`. |
| `period_start` | string | required | `"YYYY"` or `"YYYY-MM"`, matching `period_type`. |
| `sport` | string, nullable | optional | Omit (or `null`) for a goal covering every sport combined. |
| `target_distance_m` | number | required | |

**Responses:** `200` → `GoalOut`. `400` → `period_type` isn't `year`/`month`, or `period_start`
doesn't parse for the given `period_type`. `detail` is a plain string.

### `DELETE /goals/{goal_id}`

Deletes a goal by id.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `goal_id` | path | **required** | integer | From `GoalOut.id`. |

**Responses:** `200` (no response body of interest). `404` → `detail: "goal not found"`.

---

## Settings

### `GET /settings/hr-zones`

The athlete's HR zone configuration. `max_hr_bpm`/`threshold_hr_bpm`/`resting_hr_bpm` are the
three configured inputs; `zone1_high_bpm`…`zone4_high_bpm` are derived boundaries, `null` until
enough inputs are set to derive them.

**Response `200`:** `HrZoneConfigOut`.

### `PUT /settings/hr-zones`

Replaces the athlete's HR zone configuration. **A full replacement, not a partial patch** — any
field you omit is stored as `null`, not left at its previous value. To change one field, send
the current values for the other two alongside it.

**Request body** (`HrZoneConfigIn`): `max_hr_bpm`, `threshold_hr_bpm`, `resting_hr_bpm` — all
`number, nullable`, all optional.

**Response `200`:** `HrZoneConfigOut`.

---

## System

### `GET /healthz`

Liveness check. Unauthenticated. Always `200` once the process is up: `{"status": "ok"}`.

### `GET /version`

Server version and environment. Unauthenticated: `{"version": "0.1.0", "environment": "production"}`.

---

## MCP server

Every read endpoint above, plus `create_note`/`list_notes`, is also exposed as an MCP (Model
Context Protocol) tool for AI agents, mounted at `/mcp` on the same host and gated by the same
`X-API-Key` credential described in Authentication. Each tool is a thin wrapper that calls its
REST counterpart in-process — the shapes documented above apply there too.

---

## Schema Reference

Response object shapes referenced by name above. `required` means the field is always present in
the response (it may still be `null` if also `nullable`); `optional` means the field may be
omitted from the JSON entirely.

### ActivitySummary

Returned in `GET /activities` (paginated).

| Field | Type | | Description |
|---|---|---|---|
| `id` | string | required | ULID primary key. |
| `start_time_utc` | string (date-time) | required | Activity start instant, UTC. |
| `utc_offset_s` | integer | required | Local UTC offset in effect at start, seconds. |
| `local_date` | string (date), nullable | required | Calendar date the activity started on, local time. |
| `sport` | string | required | e.g. `running`, `cycling`, `walking`, `hiking`, `strength_training`. |
| `sub_sport` | string, nullable | required | Finer-grained classification, e.g. `trail_running`. |
| `name` | string, nullable | required | |
| `is_race` | boolean, nullable | required | |
| `duration_s` | number, nullable | required | Total elapsed duration. |
| `moving_duration_s` | number, nullable | required | Duration excluding detected stops/pauses. |
| `distance_m` | number, nullable | required | |
| `elevation_gain_m` | number, nullable | required | |
| `calories` | number, nullable | required | |
| `avg_hr_bpm` | number, nullable | required | |
| `max_hr_bpm` | number, nullable | required | |
| `training_load` | number, nullable | required | Garmin's own per-activity training load, when reported. |
| `workout_rpe` | number, nullable | required | Rate of perceived exertion, when logged. |
| `weight_kg` | number, nullable | required | Athlete body weight at time of activity, if on-device. |
| `vdot` | number, nullable | required | This project's own VDOT estimate, when computable (running only). |
| `workout_name` | string, nullable | required | Name of the attached pre-planned workout, if any. |
| `primary_source` | string | required | `fit_folder`, `garmin_export`, `garmin_connect`, or `strava_export`. |
| `stream_available` | boolean | required | Whether a per-second sensor stream exists for `GET .../stream`. |

### ActivityDetail

Everything in `ActivitySummary`, plus:

| Field | Type | | Description |
|---|---|---|---|
| `device` | `DeviceOut`, nullable | required | `null` when no attributed recording device. |
| `laps` | array\<`LapOut`\> | required | Empty array if the source recorded no laps. |
| `splits` | array\<`SplitOut`\> | required | |
| `route` | `RouteOut`, nullable | required | `null` when the activity has no GPS. |
| `metrics` | array\<`ActivityMetricOut`\> | required | Every stored per-activity field beyond the curated columns — the raw-first catalog in list form. |
| `estimated_sweat_loss_ml` | number, nullable | required | Computed estimate, not a device measurement. |
| `carbohydrates_g` | number, nullable | required | Athlete-logged fueling intake — no vendor source, `null` until set via `PATCH .../fueling`. |
| `sodium_mg` | number, nullable | required | Athlete-logged fueling intake — no vendor source, `null` until set via `PATCH .../fueling`. |

Returned by `GET /activities/{id}`.

### DeviceOut

`manufacturer`, `product`, `serial_number` — all `string, nullable`, required.

### LapOut

| Field | Type |
|---|---|
| `lap_index` | integer (0-based) |
| `start_time_utc` | string (date-time) |
| `duration_s`, `moving_duration_s`, `distance_m`, `avg_hr`, `max_hr`, `avg_speed_mps` | number, nullable |

### SplitOut

| Field | Type |
|---|---|
| `split_index` | integer (0-based) |
| `split_type` | string, nullable — e.g. `distance` for an auto-lap km/mile split |
| `start_time_utc`, `end_time_utc` | string (date-time), nullable |
| `duration_s`, `distance_m` | number, nullable |

### RouteOut

`encoded_polyline` (string, nullable — Google-encoded polyline of the full GPS track) plus
`min_lat`/`min_lng`/`max_lat`/`max_lng` (bounding box) and `start_lat`/`start_lng`/`end_lat`/
`end_lng` — all `number, nullable`, required.

### ActivityMetricOut

| Field | Type | Description |
|---|---|---|
| `metric_key` | string | Dotted namespace, e.g. `garmin.daily_summary.steps` or `fit.session.avg_temperature`. |
| `value_num` | number, nullable | Present when the metric is numeric. |
| `value_text` | string, nullable | Present when the metric is text-valued instead. |
| `unit` | string, nullable | |
| `source` | string | Which adapter/vendor this specific field's value came from. |

### Page\<T\>

The pagination envelope: `items` (array\<T\>), `total` (integer), `limit` (integer), `offset`
(integer) — all required. Used by `Page<ActivitySummary>` and `Page<HealthObservationOut>`.

### ActivityMapPointOut

`id`, `local_date` (nullable), `sport`, `name` (nullable), `distance_m` (nullable), `start_lat`,
`start_lng` — the last two are always present since this schema only populates for activities
with a GPS start point.

### ActivityRouteOut

`id` (string), `simplified_polyline` (string, nullable — a lower-resolution polyline than
`ActivityDetail.route`'s, sized for a thumbnail map).

### ActivityContextOut

| Field | Type | Description |
|---|---|---|
| `percentile_rank` | number, nullable | 0–100. `null` when there's nothing comparable yet. |
| `comparable_count` | integer | How many other activities the percentile was computed against. |
| `recent` | array\<`ActivityContextRecentOut`\> | Same-sport activities in the 90 days up to and including this activity's own date. |
| `fastest` | array\<`ActivityContextRecentOut`\> | This activity's all-time-fastest same-sport, similar-distance peers. |

### ActivityContextRecentOut

`id`, `local_date` (nullable), `distance_m`, `duration_s` — required; `avg_hr_bpm` (number,
nullable) — optional.

### ActivityWeatherOut

`available` (boolean, required) — `false` whenever there's no GPS start point to query against,
or the Open-Meteo fetch/parse came back empty; every other field is `null` in that case rather
than omitted. `temperature_min_c`/`temperature_max_c`/`humidity_min_pct`/`humidity_max_pct`
(number, nullable) are ranges across the activity's own duration; `weather_code` (integer,
nullable — WMO weather interpretation code, see https://open-meteo.com/en/docs) is a single
representative code at the hour closest to the activity's start. `feels_like_c`,
`wind_speed_mps` (metres/second), and `wind_direction_deg` (degrees, meteorological convention —
the direction the wind is blowing *from*) are likewise single values at that same closest hour,
not ranges, and each is independently nullable since Open-Meteo's historical archive doesn't
always carry every field for every hour.

### ActivityLocationOut

`available` (boolean, required). `location_name` (string, nullable, optional) — omitted when
`available` is `false`.

### ActivityWorkoutOut

`name`, `description` (string, nullable) plus `steps` (array\<`ActivityWorkoutStepOut`\> — raw
step list, including repeat-block markers, not pre-flattened).

### ActivityWorkoutStepOut

| Field | Type | Description |
|---|---|---|
| `step_index` | integer | |
| `duration_type` | string, nullable | e.g. `time`, `distance`, `repeat_until_steps_cmplt`. |
| `duration_time_s`, `duration_distance_m` | number, nullable | |
| `target_type` | string, nullable | e.g. `speed`, `heart_rate`, `open`. |
| `target_low_mps`, `target_high_mps` | number, nullable | |
| `intensity` | string, nullable | e.g. `active`, `rest`, `warmup`, `cooldown`. |
| `repeat_from_step` | integer, nullable | For a repeat-block step: the `step_index` it loops back to. |
| `repeat_count` | integer, nullable | |

### ActivitySourcesOut

`sources` (array\<`ActivitySourceOut`\>), `merge_decisions` (array\<`ActivityMergeDecisionOut`\>
— why each additional source was linked in).

### ActivitySourceOut

| Field | Type | Description |
|---|---|---|
| `link_id` | integer | Pass into `POST .../sources/{link_id}/split` to undo this merge. |
| `source` | string | `fit_folder`, `garmin_export`, `garmin_connect`, `strava_export`, or `eufy`. |
| `external_id` | string | That source's own identifier for this record. |
| `ingested_at` | string (date-time) | |
| `can_split` | boolean | `false` when this is the activity's only source. |

### ActivityMergeDecisionOut

`candidate_ref` (string), `reasons` (array\<string\> — human-readable match reasons),
`decided_at` (string, date-time).

### ActivitySplitOut

`new_activity_id` (string) — the freshly created activity the split source now belongs to.

### ActivitySportOverrideOut / ActivityRaceOverrideOut / ActivityNameOverrideOut / ActivityFuelingOut

Echo the corrected field(s): `{sport, sub_sport}`, `{is_race}`, `{name}`, `{carbohydrates_g,
sodium_mg}` respectively.

### StreamResponse

| Field | Type | Description |
|---|---|---|
| `activity_id` | string | |
| `tier` | string | The tier actually applied (echoes the `tier` query parameter). |
| `channels` | array\<string\> | Which channels are present in `series`. |
| `timestamps` | array\<string (date-time)\> | One UTC instant per sample, shared by every channel. |
| `series` | object | Map of channel name → array of numbers, one array per entry in `channels`, each the same length as `timestamps`. |

### HealthObservationOut

| Field | Type | Description |
|---|---|---|
| `metric_key` | string | Dotted vendor namespace, e.g. `garmin.daily_summary.steps`, `eufy.scale.weight`. |
| `observed_at_utc` | string (date-time) | |
| `local_date` | string (date) | |
| `aggregation` | string | e.g. `daily`, `instant`. |
| `value_num`, `value_text` | number/string, nullable | |
| `unit` | string, nullable | |
| `source` | string | Adapter this observation came from. |

### HealthDashboardOut / HealthDashboardMetricOut / HealthDashboardDayOut

`HealthDashboardOut.metrics` is `array<HealthDashboardMetricOut>` (one entry per curated logical
metric). Each has `logical_metric` (string), `last_observed` (string date-time, nullable), and
`daily` (`array<HealthDashboardDayOut>`). Each day: `local_date`, `value_sum`/`value_avg`/
`value_min`/`value_max`/`value_last` (number, nullable), `n_observations` (integer),
`source_metric_key` (string — which underlying raw `metric_key` this day's value was drawn from;
can differ day to day if the source device changed).

### SleepSessionOut / SleepStageOut

`SleepSessionOut`: `local_date`, `start_time_utc`, `end_time_utc`, `total_sleep_s` (nullable),
`sleep_score` (nullable), `source`, `stages` (`array<SleepStageOut>`). `SleepStageOut.stage` is
`light`, `deep`, `rem`, or `awake`, with its own `start_time_utc`/`end_time_utc`.

### CalendarResponse / DayRollupOut / HealthMetricRollupOut

`CalendarResponse.days` is `array<DayRollupOut>`. Each day: `local_date`, `activity_count`,
`activity_duration_s`/`activity_moving_duration_s`/`activity_distance_m`/
`activity_elevation_gain_m`/`activity_calories` (number, nullable), `sleep_total_s`/
`sleep_score` (number, nullable), `health_metrics` (`array<HealthMetricRollupOut>`, restricted
to `metric_keys` if passed). `HealthMetricRollupOut`: `metric_key`, `value_sum`/`value_avg`/
`value_min`/`value_max`/`value_last` (nullable), `n_observations`.

### PeriodCalendarResponse / PeriodRollupOut / PeriodHealthMetricRollupOut

`PeriodCalendarResponse.periods` is `array<PeriodRollupOut>`. Each period: `period_type`
(`week`/`month`), `period_start`, `period_end`, `activity_count`, `activity_duration_s`/
`activity_moving_duration_s`/`activity_distance_m`/`activity_elevation_gain_m`/
`activity_calories` (nullable), `activity_days_count` (distinct days with ≥1 activity),
`sleep_total_s`/`sleep_score` (nullable — period-average), `health_metrics`
(`array<PeriodHealthMetricRollupOut>`, same shape as `HealthMetricRollupOut`).

### FitnessDailyRollupOut

`local_date`, `training_load` (that day's own EWMA input, 0 on rest days), `ctl` (Chronic
Training Load, 42-day EWMA — "fitness"), `atl` (Acute Training Load, 7-day EWMA — "fatigue"),
`tsb` (Training Stress Balance, `ctl − atl` — "form").

### InsightOut

| Field | Type | Description |
|---|---|---|
| `kind` | string | `effort`, `health`, `load`, `pb`, or `streak`. |
| `window` | string | `current`, `30d`, `90d`, `180d`, `365d`, or `all_time`, depending on `kind`. |
| `title` | string | Human-readable headline. |
| `detail` | object | Kind-specific structured payload — treat as opaque unless you need one specific kind's shape. |
| `value_num` | number, nullable | The headline number, when the insight has one. |
| `metric_key`, `sport_family`, `activity_id` | string, nullable | `sport_family` is null for insights not scoped to one sport; `activity_id` is set for activity-specific insights. |
| `local_date` | string (date), nullable | |
| `computed_at` | string (date-time) | |

### PaceBandOut

`label` (string — e.g. `"5:00-5:30"`, `"< 3:30"`, or `"Walk"`), `seconds` (number — total time
across the athlete's whole running history spent at that instantaneous pace). Returned by
`GET /insights/pace-bands`, one row per band, always present even when `seconds` is 0.

### ActivityPaceBandsOut

`activity_id` (string), `local_date` (string, nullable), `bands` (`array<PaceBandOut>` — every
band in the same fixed order as `GET /insights/pace-bands`, even ones this activity spent 0
seconds in). Returned by `GET /insights/pace-bands/by-activity`, one row per running activity
with at least one non-zero band.

### NoteOut

`id` (integer), `entity_type` (`activity`/`day`), `entity_id`, `body`, `author` (nullable),
`created_at`/`updated_at` (string, date-time).

### GoalProgressOut / GoalOut / GoalProgressPoint

`GoalProgressOut.available` is required; when `false`, every other field below is optional/
omitted:

| Field | Type | Description |
|---|---|---|
| `goal` | `GoalOut`, nullable | |
| `period_end` | string (date), nullable | |
| `daily` | array\<`GoalProgressPoint`\> | Defaults to `[]`. |
| `target_per_day_m` | number, nullable | Flat daily pace needed to hit the goal exactly. |
| `current_distance_m` | number, nullable | Cumulative distance so far this period. |
| `target_distance_as_of_today_m` | number, nullable | Where the pace-to-target line sits today. |
| `ahead_behind_m` | number, nullable | `current_distance_m − target_distance_as_of_today_m`; positive means ahead. |
| `pct_complete` | number, nullable | `current_distance_m / target_distance_m × 100`. |

`GoalOut`: `id`, `period_type` (`year`/`month`), `period_start`, `sport` (nullable — `null`
means every sport combined), `target_distance_m`. `GoalProgressPoint`: `local_date`,
`cumulative_distance_m` (running total from the start of the period).

### HrZoneConfigOut

`max_hr_bpm`, `threshold_hr_bpm`, `resting_hr_bpm` (number, nullable — the three configured
inputs) plus derived `zone1_high_bpm`…`zone4_high_bpm` (number, nullable — `null` whenever the
inputs needed to compute them aren't configured).

### LoginResponse

`access_token` (string, JWT — send as `Authorization: Bearer <token>`), `expires_at` (string,
date-time).
