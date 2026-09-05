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

### `GET /activities/climbing-summary`

A true aggregate across every bouldering session in `[start_date, end_date]` — session count,
total climb time, total routes, the best completed grade, and the full attempted/completed
distribution per grade. Unlike the client-side reductions elsewhere in this app, this is a real
server-side aggregate, needed because the per-grade breakdown isn't a scalar any single activity
carries.

| Param | In | Required | Type | Default |
|---|---|---|---|---|
| `start_date` | query | optional | string (date) | — |
| `end_date` | query | optional | string (date) | — |

**Response `200`:** `ClimbingSummaryOut`.

### `GET /activities/needs-trim`

Settings page's list-wide scan for hiking/walking activities likely to include a stretch of car
travel (see `TrimCandidateOut`'s own `flag`, the same heuristic `ActivityDetail.transport_mix_flag`
uses per-activity). A deliberate exception to this app's usual never-scan-list-wide rule — cheap
enough (~0.9s over ~360 eligible activities) for a page visited occasionally rather than on every
dashboard load. Excludes any activity that already has a trim recorded.

**Response `200`:** `array<TrimCandidateOut>`, most recent first.

### `GET /activities/possible-duplicates`

Settings page's list-wide scan for cross-source duplicate activities never merged at ingest time
— every relationship `ActivityDetail.duplicate_candidates` would also independently flag from
either side, deduplicated so each pair is reported once. Same never-scan-list-wide exception as
`.../needs-trim` above (~0.5s over the full archive).

**Response `200`:** `array<DuplicatePairOut>`.

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

### `GET /activities/{activity_id}/comparisons`

The 10 most recent *other* activities of the same sport, within ±15% of this one's own distance
**and** starting within 300m of this one's own GPS start point — e.g. "how did today's 10K from
home compare to my last 10 10Ks from home." Same honest-comparison posture as `.../context`
above: real per-activity numbers (VDOT, average GAP speed, average HR, average cadence), no
fabricated composite score. `rows` is empty (with `matched_count: 0`) whenever this activity has
no distance, no recorded GPS start point (e.g. a treadmill run), or genuinely no match yet.

**Responses:** `200` → `ActivityComparisonsOut`. `404`.

### `GET /activities/{activity_id}/climb-comparisons`

Bouldering's own version of `.../comparisons` above: the 10 most recent *other* bouldering
sessions within ±15% of this one's own total duration (climb + rest) — e.g. "how did today's
~2hr session compare to my last 10 sessions of about the same length." Real per-session numbers
(route count, max completed grade, climb time), no fabricated score. `rows` is empty (with
`matched_count: 0`) whenever this activity has no duration, or genuinely no similar-length
session yet.

**Responses:** `200` → `ClimbComparisonsOut`. `404`.

### `PATCH /activities/{activity_id}/climb-routes/{split_index}`

Corrects a bouldering route's logged result and/or grade — for a route the watch misread (e.g.
scored an attempt as completed). Durable, rebuild-safe override; updates a different underlying
table than the manual-add endpoint below depending on whether `split_index` refers to a
FIT-derived route or one added by hand.

| Param | In | Required | Type |
|---|---|---|---|
| `activity_id` | path | **required** | string |
| `split_index` | path | **required** | integer |

**Request body** (`ClimbRouteStatusIn`):

| Field | Type | Required |
|---|---|---|
| `result` | string, nullable | optional — one of the values `bouldering_overrides.py`'s `VALID_RESULTS` allows, e.g. `attempt`, `completed`. |
| `grade` | integer, nullable | optional |

Send just one field to correct only that one, leaving any separately-recorded correction on the
same route untouched.

**Responses:** `200` → `SplitOut`. `404` → activity or split not found. `422` → neither `result`
nor `grade` given.

### `POST /activities/{activity_id}/climb-routes`

Adds a route the device never tracked at all — forgotten to start/stop tracking, climbed after
the watch was already stopped, etc. Durable, rebuild-safe record, not a one-off row insert.

| Param | In | Required | Type |
|---|---|---|---|
| `activity_id` | path | **required** | string |

**Request body** (`ClimbRouteAddIn`): `grade` (integer, required), `result` (string, required —
`attempt` or `completed`).

**Responses:** `200` → `SplitOut`. `404` → activity not found. `422` → `result` isn't one of
`attempt`/`completed`.

### `DELETE /activities/{activity_id}/climb-routes/{split_index}`

Removes a manually-added route — never a FIT-derived one, which would just be re-derived by the
next ingest/rebuild regardless.

| Param | In | Required | Type |
|---|---|---|---|
| `activity_id` | path | **required** | string |
| `split_index` | path | **required** | integer |

**Responses:** `204`. `400` → this split isn't a manually-added route (or doesn't exist).

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

### `POST /activities/{activity_id}/trim`

Trims a stretch of car travel from the start and/or end of a hiking/walking recording (see
`ActivityDetail.transport_mix_flag`/`GET /activities/needs-trim` for how a candidate is
surfaced). Distance, duration, elevation, heart rate, route, and laps are honestly recomputed
from the kept window of the activity's own per-second stream; calories and training load are
cleared to `null` rather than estimated, since neither is honestly re-derivable from a partial
window (both are proprietary Firstbeat computations over the *original* full recording). Durable,
rebuild-safe override.

| Param | In | Required | Type |
|---|---|---|---|
| `activity_id` | path | **required** | string |

**Request body** (`ActivityTrimIn`):

| Field | Type | Required |
|---|---|---|
| `trim_start_s` | number, nullable | optional — elapsed seconds from the activity's own recorded start. Omit to leave the start untrimmed. |
| `trim_end_s` | number, nullable | optional — same, for the end. |

At least one of the two is required.

**Responses:** `200` → `ActivityDetail` (fields recomputed/cleared per above, `has_trim: true`).
`400` → neither bound resolves to an actual trim. `404`. `422` → both fields omitted.

### `DELETE /activities/{activity_id}/trim`

Undoes a trim, restoring the pristine pre-trim state — re-parses the activity's own already-
archived raw bytes rather than "un-trimming" numerically, since that could never bring back
calories/training load (never recomputed from a window in the first place, only cleared).

| Param | In | Required | Type |
|---|---|---|---|
| `activity_id` | path | **required** | string |

**Responses:** `200` → `ActivityDetail` (original values restored, `has_trim: false`). `400` → no
trim is currently active. `404`.

### `GET /activities/{activity_id}/merge-preview/{other_id}`

Side-by-side field comparison between this activity and `other_id`, for the manual "these are the
same activity" merge tool — the athlete's own correction for a cross-source duplicate automatic
merge-matching failed to catch at ingest time (see `ActivityDetail.duplicate_candidates`/
`GET /activities/possible-duplicates` for how a candidate is surfaced). Distance, duration,
elevation, calories, heart rate, and training load each show both sides' actual value; a
collection field (route/laps/splits/stream) is a whole-side choice, represented as the literal
strings `"self"`/`"other"` rather than the collection's own contents.

| Param | In | Required | Type |
|---|---|---|---|
| `activity_id` | path | **required** | string — the activity that would survive the merge. |
| `other_id` | path | **required** | string — the activity that would be absorbed. |

**Responses:** `200` → `ActivityMergePreviewOut`. `404` → either activity not found.

### `POST /activities/{activity_id}/merge`

Merges `body.other_activity_id` into this activity — this activity is always the survivor, the
other is soft-deleted and every one of its raw sources is relinked onto the survivor. Only fields
in `body.field_choices` valued `"other"` change anything; everything else keeps this activity's
own current value (merging is opt-in per field, never a silent overwrite). Durable, rebuild-safe
correction — `sync rebuild` re-derives both activities from raw bytes on every run and would
otherwise re-split them apart.

| Param | In | Required | Type |
|---|---|---|---|
| `activity_id` | path | **required** | string — the survivor. |

**Request body** (`ActivityMergeIn`):

| Field | Type | Required | Description |
|---|---|---|---|---|
| `other_activity_id` | string | required | The activity to absorb. |
| `field_choices` | object\<string, string\> | optional (default `{}`) | Keys from `distance_m`, `duration_s`, `moving_duration_s`, `elevation_gain_m`, `calories`, `avg_hr_bpm`, `max_hr_bpm`, `training_load`, `route`, `laps`, `splits`, `stream`; each value `"other"` (the only value that changes anything — omit a key, or send `"self"`, to keep this activity's own current value). |

**Responses:** `200` → `ActivityDetail` (the merged survivor). `404` → either activity not found,
or they're already the same activity.

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

### `GET /health/stream`

One day's intraday `health_stream` series — currently the only producer is
`garmin.daily_body_battery.level`, a real per-reading body-battery curve (~3-minute cadence)
fetched live from Garmin's own `get_stress_data` endpoint. Distinct from `GET
/health/observations`, which returns once-or-a-few-per-day EAV rows — `health_stream` is a
genuine intraday series, one metric_key per Parquet file.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `metric_key` | query | **required** | string | e.g. `garmin.daily_body_battery.level`. |
| `date` | query | **required** | string (date) | A single local date — this endpoint has no range form. |

**Response `200`:** `HealthStreamResponse`. No stream data for that metric/day → empty
`timestamps`/`values` arrays, not a `404` — a normal state (e.g. any day before this endpoint's
own live-fetch start date).

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

## Planned Workouts

Scheduled (future) workouts authored on the calendar and pushed to the Garmin watch. Running
only, v1 — a non-running `sport` saves and lists fine but `POST .../push` fails cleanly
(`push_status: "push_failed"`) since there's no step-level structure to build a Garmin workout
from yet. See `docs/adr/0015-scheduled-workouts.md` and the workout-syntax text format described
there (duration, a pace/HR/zone target, cadence, a simple `Nx` repeat block).

### `GET /planned-workouts`

Date-range list for the calendar grid's own per-day indicator — not the full workout, just
enough to render one (see `GET /planned-workouts/{local_date}` for the rest).

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `start_date` | query | **required** | string (date) | Inclusive. |
| `end_date` | query | **required** | string (date) | Inclusive. |

**Response `200`:** array\<`PlannedWorkoutListItemOut`\>.

### `GET /planned-workouts/{local_date}`

One day's planned workout, its parsed steps, and any parse errors from the currently-stored
`source_text`. `available: false` (not `404`) when nothing is scheduled for that date.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `local_date` | path | **required** | string (date) | |

**Response `200`:** `PlannedWorkoutOut`.

### `PUT /planned-workouts/{local_date}`

Creates or replaces the planned workout for one date. Upsert keyed on `(athlete_id, local_date)`
— calling this again for the same date replaces the existing workout (and re-parses
`source_text` into a fresh set of steps) rather than creating a second one. Editing a workout
that was already `"pushed"` resets `push_status` back to `"draft"` — the old Garmin copy is now
stale and gets re-pushed fresh on the next push.

**Request body** (`PlannedWorkoutIn`):

| Field | Type | Required | Description |
|---|---|---|---|
| `sport` | string | required | `running` / `yoga` / `bouldering` / `fitness` / `hiit` / `strength_training` — open string, not an enum. |
| `name` | string, nullable | optional | |
| `source_text` | string, nullable | optional | For `running`: the athlete's own workout-syntax text — a malformed line doesn't reject the save, it's still stored, and the resulting `parse_errors` come back in the response. For `yoga`/`bouldering`: freeform notes only, never parsed. Ignored for `hiit`/`strength_training` — use `steps` instead. |
| `scheduled_time` | string, nullable | optional | `"HH:MM"` (24h). Perseverer's own calendar display metadata only — Garmin's own scheduling has no time-of-day API. |
| `duration_minutes` | number, nullable | optional | `yoga`/`bouldering` only — sets the workout's duration directly (there's no syntax to derive one from). Ignored for `running`/`hiit`/`strength_training`, where duration is derived instead. |
| `steps` | array\<`PlannedWorkoutStepIn`\>, nullable | optional | `hiit`/`strength_training` only — the exercise-picker steps, arriving already-structured (never parsed from text). Ignored for every other sport. |

**Response `200`:** `PlannedWorkoutOut`.

`PlannedWorkoutStepIn`:

| Field | Type | Required | Description |
|---|---|---|---|
| `step_index` | integer | required | Position within the (unexpanded) step list — see `PlannedWorkoutStepOut` below for the repeat-block convention. |
| `duration_type` | string | required | `"reps"` / `"time"` for a real step, or `"repeat_until_steps_cmplt"` for a trailing repeat-group marker (`repeat_from_step`/`repeat_count` set, every other field omitted). |
| `duration_time_s` | number, nullable | optional | Seconds — for a time-based exercise step or a rest step. |
| `duration_reps` | integer, nullable | optional | For a reps-based exercise step (a counted set, e.g. "10 reps of bench press"). |
| `intensity` | string, nullable | optional | `"active"` / `"rest"`. |
| `repeat_from_step` | integer, nullable | optional | Repeat-marker only. |
| `repeat_count` | integer, nullable | optional | Repeat-marker only. |
| `exercise_category` | string, nullable | optional | The exact `(category, exercise)` pair from `garminconnect.exercises`, e.g. `"BENCH_PRESS"`. Omitted for a rest step. |
| `exercise_name` | string, nullable | optional | `""` (not omitted) when the step names just the category with no specific variant, matching Garmin's own catalog convention. |
| `weight_kg` | number, nullable | optional | Converted to grams (Garmin's own wire unit) only at push time. |

### `DELETE /planned-workouts/{local_date}`

Deletes the planned workout for one date. If it was already pushed, also best-effort deletes the
Garmin-side workout template — a Garmin-side failure there (e.g. unreachable, no token store)
never blocks the local delete.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `local_date` | path | **required** | string (date) | |

**Responses:** `200` (no response body). `404` → `detail: "planned workout not found"`.

### `POST /planned-workouts/{local_date}/push`

Manually pushes one workout to Garmin right now, regardless of date — the override alongside the
worker's own automatic push for anything due within the coming week
(`PERSEVERER_PLANNED_WORKOUT_PUSH_WINDOW_DAYS`, default 7). Runs in the background; poll
`GET /planned-workouts/{local_date}` afterward for the updated `push_status`/`push_error`, since
that status lives on the workout row itself, not a generic job log.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `local_date` | path | **required** | string (date) | |

**Responses:** `200` → `JobTriggerOut`. `404` → `detail: "planned workout not found"`.

### `POST /planned-workouts/recurring`

Creates one independent `planned_workout` row per occurrence date — not a recurring-rule object;
each row is a full copy of the same content and can be edited or deleted independently of the
others afterward. A date that already has a planned workout is skipped, not overwritten, and
reported back in `skipped_dates`.

**Request body** (`RecurringWorkoutIn`):

| Field | Type | Required | Description |
|---|---|---|---|
| `local_date` | string (date) | required | First occurrence. |
| `sport` | string | required | |
| `name` | string, nullable | optional | |
| `source_text` | string, nullable | optional | |
| `scheduled_time` | string, nullable | optional | `"HH:MM"` (24h) — see `PlannedWorkoutIn` above. |
| `duration_minutes` | number, nullable | optional | `yoga`/`bouldering` only — see `PlannedWorkoutIn` above. |
| `steps` | array\<`PlannedWorkoutStepIn`\>, nullable | optional | `hiit`/`strength_training` only — see `PlannedWorkoutIn` above. |
| `frequency` | string | required | `weekly` / `every_n_days` / `monthly`. |
| `interval_days` | integer | required for `every_n_days` | `>= 1`. |
| `count` | integer | exactly one of `count`/`until` | Total occurrences, including the first. |
| `until` | string (date) | exactly one of `count`/`until` | Inclusive. |

**Responses:** `200` → `RecurringWorkoutOut`. `422` → invalid `frequency`, missing
`interval_days` for `every_n_days`, or neither/both of `count`/`until` given. `detail` is a plain
string.

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

### `GET /settings/running-load`

The athlete's configured running threshold pace — the calibration constant behind
`perseverer.performance.running_tss`, a pace-based training-load input `fitness_daily_rollup`'s
CTL/ATL/TSB prefers over Garmin's own uncalibrated `training_load_peak` for running (see
`docs/DATA_DICTIONARY.md`'s "Running TSS" section for why). `null` means not configured yet —
Fitness & Form keeps using Garmin's own number for every activity in that case.

**Response `200`:** `RunningLoadConfigOut` — `threshold_pace_sec_per_km` (number, nullable).

### `PUT /settings/running-load`

Sets the athlete's threshold pace. Unlike `PUT /settings/hr-zones`, this also immediately
recomputes `running_tss`/`fitness_daily_rollup`/insights for the athlete before returning, so
CTL/ATL/TSB reflect the new pace right away rather than waiting for the next sync.

**Request body** (`RunningLoadConfigIn`): `threshold_pace_sec_per_km` — `number, nullable`,
optional, must be positive if given.

**Response `200`:** `RunningLoadConfigOut`.

### `GET /settings/garmin/status`

Garmin Connect connection status — a token store presence/age check (no network call to Garmin
itself) plus the most recent `garmin_connect` sync result. There is no real token expiry to
report: Garmin exposes no readable expiry for the refresh token that actually determines session
lifetime (the short-lived access token auto-refreshes silently and its own expiry isn't
actionable) — `staleness_severity`/`staleness_message` are the practical substitute, an
escalating warning/critical signal off how many days the last sync has been failing.

**Response `200`:** `GarminAuthStatusOut` — `token_store_present` (bool), `token_store_age_days`
(int, nullable), `last_sync_status` (`"running"|"success"|"failed"`, nullable — `null` means no
sync has ever run), `last_sync_at` (datetime, nullable), `last_sync_error` (string, nullable),
`staleness_severity` (`"warning"|"critical"`, nullable — `null` whenever the last sync
succeeded), `staleness_message` (string, nullable).

### `POST /settings/garmin/login`

A human-initiated, one-shot Garmin login — the web counterpart of `sync auth login`. Only the
resulting session token is persisted; the password is used once and never stored. **Does not
support Garmin's MFA challenge** — if the account requires one, this returns `422` pointing at
`sync auth login` (interactive, MFA-capable) instead. See
`adapters/garmin_connect.py::login_with_credentials`'s own docstring for why: multiple uvicorn
workers with no shared memory means the in-progress login can't reliably survive across the two
requests an MFA flow would need.

**Request body** (`GarminLoginIn`): `username` (string), `password` (string).

**Response `200`:** `GarminLoginOut` — `{"success": true}`. **`400`** — wrong username/password
(deliberately not `401`: this app's frontend treats any `401` as "your own Perseverer session
expired" and force-logs you out — see the endpoint's own docstring). **`422`** — this account
requires MFA. **`429`** — Garmin rate-limited the attempt; wait before retrying (never retried
automatically, matching this adapter's own no-retry-on-429 rule everywhere else).

### `POST /settings/garmin/sync`

Triggers the same `sync_garmin_connect()` call the daily worker and `sync import garmin-connect`
already make, once, right now. Runs in the background — poll `GET /settings/jobs/latest?
source=garmin_connect` for progress; this call itself returns immediately.

**Response `200`:** `JobTriggerOut` — `{"triggered": true}`.

### `POST /settings/rebuild`

Web counterpart of `sync rebuild`. Never destructive — only derived tables are wiped and
replayed from the raw archive. Runs in the background — poll `GET /settings/jobs/latest?
source=rebuild` for progress. Can take several minutes on a large archive.

**Response `200`:** `JobTriggerOut` — `{"triggered": true}`.

### `POST /settings/import/bulk-export`

Web counterpart of `sync import garmin-export`/`sync import strava-export <path>` — uploads a
bulk-export `.zip` instead of pointing at a path already on disk. Always safe to re-run — both
importers are idempotent full-archive rescans, so an overlapping/updated export is a clean
no-op for anything already ingested. Runs in the background — poll `GET /settings/jobs/latest?
source=garmin_export` (or `strava_export`) for progress.

**Request body:** `multipart/form-data` — `kind` (`"garmin"|"strava"`), `file` (the `.zip`).

**Response `200`:** `JobTriggerOut` — `{"triggered": true}`. **`422`** — the uploaded file isn't
a `.zip`. Note: a reverse proxy in front of this API may reject a very large export before it
reaches this endpoint at all — outside this app's own configuration.

### `GET /settings/jobs/latest`

The most recent `ingest_run` row for one source — generic status polling shared by the three
triggers above (and, incidentally, every other sync/import entrypoint that writes to the same
table).

**Query params:** `source` (`"garmin_connect"|"rebuild"|"garmin_export"|"strava_export"`,
required).

**Response `200`:** `JobStatusOut | null` — `null` if that source has never run.
`JobStatusOut`: `source`, `status` (`"running"|"success"|"failed"`), `started_at`,
`finished_at` (nullable), `items_seen`, `items_new`, `error_count`, `first_error` (string,
nullable).

---

## Sharing

An athlete-issued link granting **unauthenticated, read-only** access to one activity or one
summary period. The creation/revocation endpoints below need the usual `X-API-Key`/JWT; the
`GET /share/{token}` page itself deliberately does not — same "public by omission" mechanism as
`/healthz`/`/version`/`/auth/login`, not a special-cased bypass. It returns server-rendered HTML
(not JSON), with real `<meta property="og:...">` tags computed from the same render as the
visible page, so a pasted link's social-preview card can never disagree with what it links to.
The public path is un-prefixed (`/share/{token}`, not `/api/v1/share/{token}`) so the link is
short and shareable, and so a reverse proxy that splits by port rather than path (see
`docs/DEPLOY.md`) only needs one `location /share/` forwarding rule.

### `POST /activities/{activity_id}/share`

Creates a share link for one activity. **`404`** if the activity doesn't exist (or isn't owned
by the authenticated athlete, or is soft-deleted).

**Response `200`:** `ShareLinkOut` — `{"id": 1, "url": "https://.../share/<token>"}`. The raw
token is only ever returned here; only its SHA-256 hash is stored.

### `POST /periods/{period_type}/share`

Creates a share link for one summary period. `period_type` is a path segment
(`"week"|"month"|"year"|"all"`).

**Query params:** `period_start` (string, required unless `period_type` is `"all"` — e.g.
`"2026-06"` for a month, `"2026"` for a year, `"2026-06-01"` — the period's start date — for a
week). **`422`** if omitted for a non-`"all"` period type.

**Response `200`:** `ShareLinkOut`.

### `POST /share/{id}/revoke`

Revokes a share link by its numeric id (the `id` `ShareLinkOut` returned at creation, not the
token itself). Idempotent — revoking an already-revoked or cross-athlete-owned link returns
`revoked: false` rather than erroring, so this endpoint never leaks whether a given id belongs
to another athlete.

**Response `200`:** `RevokeShareOut` — `{"revoked": true|false}`.

### `GET /share/{token}`

**No authentication.** Renders the shared activity or period as a plain HTML page. An
unknown/revoked token renders a "this link is no longer available" page (never a `404` — a
`404` would look identical to "revoked" and there's no reason to distinguish the two to an
outside visitor).

Activity pages show name, sport, date, distance, duration, pace, elevation gain — deliberately
excluding weight/HR fields. Period pages aggregate `day_rollup` over the period's date range
(distance, activity count, elevation gain, active days) — a month, a year, or (for `"all"`)
every day on record.

**Response `200`:** `text/html`.

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
| `transport_mix_flag` | `TransportMixFlagOut`, nullable | required | Computed fresh from this activity's own stream — `null` unless the sport is hiking/walking and a sustained fast segment (likely car travel) touches either boundary. Detail-page-only; never computed list-wide (see `GET /activities/needs-trim` for the one deliberate exception). |
| `has_trim` | boolean | required | `true` once a trim has been committed via `POST .../trim`. |
| `duplicate_candidates` | array\<`DuplicateCandidateOut`\> | required | Other activities that look like the same real-world activity as this one, bounded to a ±1 day window. Usually empty. |

Returned by `GET /activities/{id}`.

### DeviceOut

`manufacturer`, `product`, `serial_number` — all `string, nullable`, required.

### LapOut

| Field | Type |
|---|---|
| `lap_index` | integer (0-based) |
| `start_time_utc` | string (date-time) |
| `duration_s`, `moving_duration_s`, `distance_m`, `avg_hr`, `max_hr`, `avg_speed_mps` | number, nullable |
| `avg_gap_speed_mps` | number, nullable — grade-adjusted average speed (m/s) for this lap, computed fresh per request from the activity's raw stream (`gap.py::compute_lap_gap_speeds_mps`), not stored. `null` for a non-running activity, an activity with no stream, or a lap whose own slice of the stream is too short/missing altitude or distance data. This is the same value the `avg_gap_speed_mps` field elsewhere in this API already reports at whole-activity granularity — the Intervals table on the frontend reads it from here rather than computing it client-side, so a headless caller gets the identical number. |

### SplitOut

| Field | Type |
|---|---|
| `split_index` | integer (0-based) |
| `split_type` | string, nullable — e.g. `distance` for an auto-lap km/mile split, `climb_active`/`climb_rest` for bouldering |
| `start_time_utc`, `end_time_utc` | string (date-time), nullable |
| `duration_s`, `distance_m` | number, nullable |
| `climb_grade` | integer, nullable — bouldering only, V-scale grade, only set on a `climb_active` split. Reverse-engineered from an undocumented FIT field (see `docs/DATA_DICTIONARY.md`) |
| `climb_result` | string, nullable — bouldering only, `"attempt"`/`"completed"` (or `"unknown_<n>"` for a raw value not yet confirmed), only set on a `climb_active` split |
| `climb_avg_hr`, `climb_max_hr` | number, nullable — bouldering only, set on both `climb_active` and `climb_rest` splits |

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

### TransportMixFlagOut

| Field | Type | Description |
|---|---|---|
| `at_start` | boolean | A sustained fast (likely car-travel) segment touches the recording's start. |
| `at_end` | boolean | Same, at the end. |
| `suggested_trim_start_s` | number, nullable | Elapsed seconds from the activity's own start where the fast segment settles back to walking pace — a suggested `trim_start_s` for `POST .../trim`. `null` when `at_start` is `false`. |
| `suggested_trim_end_s` | number, nullable | Same, for the end — a suggested `trim_end_s`. `null` when `at_end` is `false`. |

### DuplicateCandidateOut

| Field | Type | Description |
|---|---|---|
| `id` | string | The other activity's id. |
| `name` | string, nullable | |
| `primary_source` | string | |
| `start_time_utc` | string (date-time) | |
| `distance_m` | number, nullable | |
| `duration_s` | number, nullable | |

### DuplicatePairOut

`activity_a`, `activity_b` — both `DuplicateCandidateOut`, one relationship from
`GET /activities/possible-duplicates`.

### TrimCandidateOut

Everything `ActivityDetail.transport_mix_flag` carries, plus enough activity identity to link
straight to it from `GET /activities/needs-trim`'s list.

| Field | Type | Description |
|---|---|---|
| `id` | string | |
| `name` | string, nullable | |
| `sport` | string | |
| `start_time_utc` | string (date-time) | |
| `distance_m` | number, nullable | |
| `duration_s` | number, nullable | |
| `flag` | `TransportMixFlagOut` | Never `null` here — only activities that *were* flagged appear in this list at all. |

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

### ActivityComparisonsOut

| Field | Type | Description |
|---|---|---|
| `start_radius_m` | number | The GPS start-proximity threshold actually used (300m). |
| `distance_band_fraction` | number | The distance-tolerance fraction actually used (0.15). |
| `matched_count` | integer | How many activities matched before capping to the 10 most recent. |
| `rows` | array\<`ActivityComparisonRowOut`\> | The 10 most recent matches, most recent first. |

### ActivityComparisonRowOut

`id`, `local_date` (nullable), `distance_m`, `duration_s` — required (`duration_s` is
moving-preferred, same convention as `ActivityContextRecentOut`). `vdot`, `avg_gap_speed_mps`
(m/s), `avg_hr_bpm`, `avg_cadence_spm` (number, nullable, each) — `avg_cadence_spm` is already
doubled to strides/minute server-side (FIT's own field is a single-foot rate).

### ClimbComparisonsOut

Bouldering's own version of `ActivityComparisonsOut`, from `GET .../climb-comparisons`.

| Field | Type | Description |
|---|---|---|
| `duration_band_fraction` | number | The duration-tolerance fraction actually used (0.15). |
| `matched_count` | integer | How many sessions matched before capping to the 10 most recent. |
| `rows` | array\<`ClimbComparisonRowOut`\> | The 10 most recent matches, most recent first. |

### ClimbComparisonRowOut

`id`, `local_date` (nullable), `duration_s` — required. `route_count` (integer — how many splits
in that session have a grade at all), `max_completed_grade` (integer, nullable), `climb_time_s`
(number, nullable — total time actually climbing, excluding rest).

### ClimbingSummaryOut

From `GET /activities/climbing-summary`.

| Field | Type | Description |
|---|---|---|
| `session_count` | integer | Bouldering sessions in the requested period. |
| `total_climb_time_s` | number | Summed across every session's `climb_active` splits. |
| `total_routes` | integer | Every split with a grade, across every session. |
| `max_completed_grade` | integer, nullable | The best `result: "completed"` grade in the period. |
| `grade_breakdown` | array\<`ClimbGradeBreakdownOut`\> | Attempted/completed counts, one row per grade actually seen. |

### ClimbGradeBreakdownOut

`grade` (integer), `attempted` (integer — includes any unconfirmed raw `"unknown_<n>"` result,
counted conservatively rather than assumed completed), `completed` (integer).

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

### ActivityMergePreviewOut

`fields` — array\<`FieldComparisonOut`\>, from `GET .../merge-preview/{other_id}`.

### FieldComparisonOut

| Field | Type | Description |
|---|---|---|
| `field` | string | One of the keys `POST .../merge`'s own `field_choices` accepts. |
| `self_value` | number, string, nullable | This activity's own current value. For a collection field (`route`/`laps`/`splits`/`stream`), the literal string `"self"` rather than the collection's actual contents. |
| `other_value` | number, string, nullable | The candidate's value, same convention. |

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

### HealthStreamResponse

Returned by `GET /health/stream`.

| Field | Type | Description |
|---|---|---|
| `metric_key` | string | Echoes the request's `metric_key`. |
| `local_date` | string (date) | Echoes the request's `date`. |
| `timestamps` | array\<string (date-time)\> | UTC, ordered ascending. Same length as `values`. |
| `values` | array\<number\> | |

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

### GarminAuthStatusOut / JobStatusOut

See `GET /settings/garmin/status` and `GET /settings/jobs/latest` above — both documented
field-by-field there rather than repeated here.

### LoginResponse

`access_token` (string, JWT — send as `Authorization: Bearer <token>`), `expires_at` (string,
date-time).

### ShareLinkOut / RevokeShareOut

See `POST /activities/{activity_id}/share` and `POST /share/{id}/revoke` under **Sharing**
above. `ShareLinkOut`: `id` (int), `url` (string, the full public share URL). `RevokeShareOut`:
`revoked` (bool).

### PlannedWorkoutListItemOut

`local_date` (string, date), `id` (integer), `sport` (string), `name` (string, nullable),
`scheduled_time` (string, nullable — `"HH:MM"`), `push_status` (`draft`/`pushed`/`push_failed`).

### PlannedWorkoutOut

`available` (boolean, required); when `false`, every field below is `null`/empty:

| Field | Type | Description |
|---|---|---|
| `id` | integer, nullable | |
| `local_date` | string (date), nullable | |
| `sport` | string, nullable | |
| `name` | string, nullable | |
| `source_text` | string, nullable | `running`: the athlete's own typed workout-syntax text, verbatim. `yoga`/`bouldering`: freeform notes only, never parsed. `hiit`/`strength_training`: always `null` — see `steps`. |
| `scheduled_time` | string, nullable | `"HH:MM"` (24h) — display-only, not sent to Garmin. |
| `estimated_duration_s` | number, nullable | `running`: an estimate — a distance-based step's real duration depends on the athlete's actual pace. `yoga`/`bouldering`: exactly the `duration_minutes` given at save time, in seconds. `hiit`/`strength_training`: an estimate computed from `steps` (a rough assumed seconds/rep for a reps-based step, real seconds otherwise). |
| `steps` | array\<`PlannedWorkoutStepOut`\> | Defaults to `[]`. Raw, unexpanded (repeat-block markers included). Always `[]` for `yoga`/`bouldering` — no structured syntax for those sports. |
| `parse_errors` | array\<`ParseErrorOut`\> | Defaults to `[]`. From re-parsing the currently-stored `source_text` — `running` only; always `[]` for `yoga`/`bouldering`. |
| `push_status` | string, nullable | `draft` / `pushed` / `push_failed`. |
| `push_error` | string, nullable | |
| `garmin_workout_id` | integer, nullable | |
| `garmin_scheduled_at` | string (date-time), nullable | |

### PlannedWorkoutStepOut

| Field | Type | Description |
|---|---|---|
| `step_index` | integer | |
| `duration_type` | string, nullable | `time` / `distance` / `repeat_until_steps_cmplt`. |
| `duration_time_s`, `duration_distance_m` | number, nullable | |
| `target_type` | string, nullable | `pace` / `heart_rate`. |
| `target_low`, `target_high` | number, nullable | m/s for `pace`, bpm for `heart_rate`. |
| `target_hr_zone` | integer, nullable | Alternative to `target_low`/`target_high` — resolved against the athlete's own configured HR zones at push time. |
| `cadence_low`, `cadence_high` | integer, nullable | Steps/min. |
| `intensity` | string, nullable | e.g. `warmup`, `active`, `recovery`, `cooldown`, `rest`. |
| `repeat_from_step` | integer, nullable | For a repeat-block step: the `step_index` it loops back to. |
| `repeat_count` | integer, nullable | |
| `duration_reps` | integer, nullable | `hiit`/`strength_training` only — a rep-counted set. |
| `exercise_category`, `exercise_name` | string, nullable | `hiit`/`strength_training` only — the exact `(category, exercise)` pair from `garminconnect.exercises`. `exercise_name` is `""` (not `null`) when the step names just the category with no specific variant. |
| `weight_kg` | number, nullable | `hiit`/`strength_training` only. |

### ParseErrorOut

`line_no` (integer, 1-indexed), `message` (string).

### RecurringWorkoutOut

`created_dates` (array\<string\>, dates), `skipped_dates` (array\<string\>, dates — already had a
planned workout, left untouched).
