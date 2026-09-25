# API Reference

The source of truth for integrating with the Perseverer REST API — every
endpoint, parameter (required/optional, default), and response shape. Generated from the live
OpenAPI schema (`GET /openapi.json`) and cross-checked against router source for the details
OpenAPI doesn't capture (auth semantics, error codes, enumerable string fields). Grows every
phase, alongside `AGENTS.md` and `DATA_DICTIONARY.md`, per the project's "ways of working" rule.

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

Point-in-time insights for one activity, computed fresh at request time — unlike `GET /insights`,
which reads a precomputed table. Running activities can return pace and distance records;
bouldering activities can return records for route count, highest attempted/completed V-grade,
active climb time, and high/low average and peak heart rate. Each claim uses the last 30 days,
last 90 days, calendar year, or all history, and only compares activities that happened no later
than the displayed activity (including its timestamp when two activities share a date), so an old
activity never learns about a later performance.

**Responses:** `200` → `array<InsightOut>`. `404`.

### `GET /activities/{activity_id}/weather`

Temperature/humidity range, feels-like temperature, wind speed/direction, a WMO weather code,
dew point/solar radiation/cloud cover/apparent-temperature ranges, total precipitation,
sunrise/sunset, and an hour-by-hour trajectory for the activity's time window, from Open-Meteo's
historical archive,
cached forever once fetched. `available: false` when there's no GPS start point or the fetch came
back empty. Everything besides the original temperature/humidity/weather-code/feels-like/wind
fields is designed so this one response supports a full conditions judgement (heat stress in
bpm/pace terms) with no second call to Open-Meteo needed — see `ActivityWeatherOut` below and
`weather.py`'s own module docstring for exactly why each field matters. Every field added
alongside the original ones is `None` (or `hourly: []`) on an activity whose weather was cached
before these fields existed, until a backfill re-fetches it (`sync backfill-weather-fields`, see
docs/DATA_DICTIONARY.md's own Weather section).

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
| `start_s` | query | optional | number | — | Narrow to elapsed seconds from the activity's own first recorded sample (e.g. `start_s=1200&end_s=1380` for the 20:00–23:00 mark). Bucket width is sized from this narrowed window's own span, not the whole activity's duration — the way to get true near-1-second resolution for a short stretch of a long activity at `tier=high` without paying for (or receiving) the whole activity's own high-tier response. Intersected with, never widening past, an active trim. |
| `end_s` | query | optional | number | — | See `start_s`. Omit for "to the end" (or "to the end of the trim," if one is active). |

**Responses:** `200` → `StreamResponse`. `400` → a requested channel isn't available for this
activity, `start_s >= end_s`, or another downsample-argument mismatch. `404` → activity not
found, or no stream data for this activity.

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

## Blood Tests

Athlete-entered blood test results — one row per marker per draw, several rows sharing one
`local_date` forming one logical panel (e.g. a full lipid panel drawn the same day). Deliberately
a plain CRUD table, not the `health_observation` EAV pipeline every vendor adapter feeds — this
data is typed in by the athlete, not parsed from a vendor payload. Reference ranges are the
athlete's own, copied from their lab report; informational only, never a claim this API makes.

### `GET /blood-tests`

| Param | In | Required | Type |
|---|---|---|---|
| `start_date` | query | **required** | string (date) |
| `end_date` | query | **required** | string (date) |

**Response `200`:** `array<BloodTestResultOut>`, ordered by `local_date` descending then `marker`.

### `GET /blood-tests/{result_id}`

**Response `200`:** `BloodTestResultOut`. `404` if not found (or belongs to another athlete).

### `POST /blood-tests`

Creates one marker result. See `POST /blood-tests/batch` below for entering a whole panel at once
— this route exists for adding a single marker to an existing draw, or for scripts that don't
need the batch shape.

**Request body** (`BloodTestResultIn`):

| Field | Type | Required | Description |
|---|---|---|---|
| `local_date` | string (date) | **required** | The draw date. |
| `marker` | string | **required** | e.g. `"LDL Cholesterol"` — freeform, not a fixed catalog. |
| `value_num` | number | **required** | |
| `unit` | string, nullable | optional | e.g. `"mg/dL"`. |
| `reference_low`, `reference_high` | number, nullable | optional | The athlete's own lab-reported range; either or both may be omitted (e.g. a marker with only an upper bound). `reference_low` must not exceed `reference_high` when both are given. |
| `lab_name` | string, nullable | optional | |
| `notes` | string, nullable | optional | |

**Response `200`:** `BloodTestResultOut`.

### `POST /blood-tests/batch`

The primary write path — a whole panel, many markers, one draw date, in one call.

**Request body** (`BloodTestBatchIn`):

| Field | Type | Required | Description |
|---|---|---|---|
| `local_date` | string (date) | **required** | |
| `lab_name` | string, nullable | optional | Shared across every marker in this call. |
| `notes` | string, nullable | optional | Shared across every marker in this call. |
| `results` | array\<object\> | **required** | At least one. Each entry: `marker`, `value_num`, `unit`, `reference_low`, `reference_high` — same shape/validation as the single-result fields above, minus `local_date`/`lab_name`/`notes`. |

**Response `200`:** `array<BloodTestResultOut>`, one per created marker, in the order submitted.

### `PUT /blood-tests/{result_id}`

**Request body:** `BloodTestResultIn` (same shape as `POST`, full replacement).

**Response `200`:** `BloodTestResultOut`. `404` if not found.

### `DELETE /blood-tests/{result_id}`

Deletes one marker result. `200` on success, `404` if not found.

### `DELETE /blood-tests/by-date/{local_date}`

Deletes every marker recorded on `local_date` — the whole panel at once, rather than removing
each of a panel's markers one by one. `200` on success, `404` if nothing was recorded that date.

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

## Performance

Independently-computed race-time predictions, threshold pace/HR, max HR, and VO2max — all
derived from the athlete's own running data via the Daniels-Gilbert VDOT model, never Garmin's
own precomputed equivalents (`daily_race_predictions`, `daily_lactate_threshold`). See
`performance_rollup.py`'s own docstring for the full model.

### `GET /performance`

Daily rollup of `rolling_vdot` (a 42-day trailing maximum VDOT — under the Daniels-Gilbert model
this figure *is* the VO2max estimate, in ml/kg/min, not a separately-converted value), max HR
(365-day trailing max, empirical-first with a Tanaka-formula fallback), anaerobic and aerobic
threshold pace/HR (both derived from `rolling_vdot` — anaerobic is the original, unqualified
`threshold_pace_s_per_km`/`threshold_hr_bpm` fields; aerobic is the newer, always-slower
`aerobic_threshold_pace_s_per_km`/`aerobic_threshold_hr_bpm`), and predicted
5k/10k/half-marathon/marathon times.

| Param | In | Required | Type |
|---|---|---|---|
| `start_date` | query | **required** | string (date) |
| `end_date` | query | **required** | string (date) |

**Response `200`:** `array<PerformanceDailyRollupOut>`.

### `GET /performance/vo2max-analysis`

Point-in-time factor analysis for the current `rolling_vdot`/VO2max value: which run set it
(a rolling *maximum*, so exactly one run drives it, never an average), every other run that
qualified in the same window, when the driving run ages out, and plain-text diagnostics for gaps
(no qualifying run yet, the value about to change with nothing to replace it, a stale window with
no recent effort). Deliberately request-time, not rollup-backed — see `vo2max_analysis.py`'s own
docstring for why this tiny, occasional lookup doesn't fall under the rollup mandate the rest of
this API follows, the same exception `GET /activities/needs-trim` already establishes.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `as_of` | query | optional | string (date) | Defaults to today. |

**Response `200`:** `Vo2maxFactorAnalysisOut`.

### `GET /performance/pace-hr-zones`

The complete 5-zone pace + heart-rate training table (Recovery / Basic Endurance / Aerobic
Threshold / Lactate Threshold / VO2 Max), each a real pace range and HR range plus a
plain-language "when/how to use it," built from the athlete's *recent* running history — not their
literal entire history, and not `GET /performance`'s own 42-day rolling "current fitness" snapshot
either. `profile_vdot`/`profile_max_hr_bpm` are the single best (highest) values within their own
trailing lookback window, each with the one activity that set them — `profile_vdot` prefers an
activity marked as a race within the last 2 years (`activity.is_race`) over any training run in
the last year, since VDOT is calibrated against genuine race performances and a short, all-out
training segment can post an inflated value; `profile_vdot_source` (`"race"`/`"training_run"`/
`null`) records which one fired, falling back to the athlete's best training run in the last year
(with an explicit `missing` caveat) only when no race qualifies in the last 2 years.
`profile_max_hr_bpm` uses its own 2-year window. See `pace_hr_zones.py`'s own "How far back"
docstring section for exactly why these three windows differ. Every zone's own HR range is
empirical-first
(the real 25th-75th percentile among the athlete's own qualifying runs at that pace, once there
are enough of them) and formula-fallback-second (a fraction of max HR, from the same VT1/VT2
literature `vdot.py` already cites) — see `pace_hr_zones.py`'s own module docstring for the full
model, every zone boundary's reasoning, and its sources. `sample_runs` per zone are the literal
answer to "why these numbers," evenly sampled down to a cap when there are many. Deliberately
request-time, not rollup-backed — same exception as `GET /performance/vo2max-analysis` above.
Replaces the old `GET /performance/threshold-analysis` (a factor-analysis breakdown of two
point-in-time numbers, no ranges) — `performance_daily_rollup`'s own `threshold_pace_s_per_km`/
`aerobic_threshold_pace_s_per_km`/HR fields are unaffected and still served by `GET /performance`.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `as_of` | query | optional | string (date) | Defaults to today. |

**Response `200`:** `PaceHrZonesOut`.

### `GET /performance/race-readiness`

Has the athlete run enough *volume* for their next scheduled race, not just "are they fit" — a
materially different question from this same API's own VDOT-based race prediction (`GET
/planned-races`'s `predicted_duration_s`), which is reused as-is here and shown alongside,
never blended into the readiness percentage. Compares recency-weighted weekly running distance
(182-day window) and long-run distance (70-day window, a week's own longest run standing in for
"long run") against targets interpolated from published training-plan data for the race's own
distance, combined into one readiness percentage (60% weekly distance / 40% long run — see
`race_readiness.py`'s own module docstring for the full reasoning behind every number here).
Deliberately request-time, not rollup-backed — same "bounded, occasional diagnostic lookup"
exception `GET /performance/vo2max-analysis` above already establishes.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `race_id` | query | optional | integer | A specific `planned_race` id. Defaults to the athlete's own nearest upcoming running race. |
| `as_of` | query | optional | string (date) | Defaults to today. |

**Response `200`:** `RaceReadinessOut` — `available` (`false` — never fabricated — when there's
no upcoming running race, or `race_id` doesn't belong to the caller), `race_id`/`race_name`/
`race_local_date`/`race_distance_m` (all nullable), `weekly_distance_target_m`/
`long_run_target_m` (meters, nullable), `as_of` (string, nullable), `current`
(`RaceReadinessPointOut`, nullable), `predicted_duration_s` (number, nullable — `null` for a
non-standard race distance), `history` (`RaceReadinessPointOut[]`, oldest first, one point per
week over the 182-day window), `weekly_distance_series`/`long_run_series`
(`RaceReadinessWeekOut[]`, oldest first, one entry per Monday-start week over each series' own
182-day/70-day window — the actual realized distance behind the two compliance percentages
above, `0.0` never omitted for a week with nothing recorded).

`RaceReadinessPointOut`: `as_of` (string, date), `weekly_distance_compliance_pct` (number),
`long_run_compliance_pct` (number), `readiness_pct` (number) — all 0-100.

`RaceReadinessWeekOut`: `week_start` (string, date, the Monday that week starts), `distance_m`
(number).

### `GET /performance/curve`

The best sustained value for each of a fixed set of durations (1s through 2h), across every
qualifying activity in the date range — the single best D-second window anywhere, not one
activity's own average, the same idea as cycling's "Critical Power Curve"/Runalyze's "Heart Rate
Curve." Deliberately request-time, not rollup-backed — same "bounded, occasional diagnostic
lookup" exception `GET /performance/vo2max-analysis` above already establishes. `pace`/`gap` are
always running-only regardless of `sports`; `heart_rate` honors `sports` since a hard bike ride
or hiit session is a real sustained HR effort too. See `performance_curve.py`'s own module
docstring, and `docs/DATA_DICTIONARY.md`'s "Performance Curve" section, for the sliding-window
algorithm and its gap-disqualification rule.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `metric` | query | **required** | string | One of `pace`, `gap`, `heart_rate`. |
| `start_date` | query | **required** | string (date) | |
| `end_date` | query | **required** | string (date) | |
| `sports` | query | optional | string | Comma-separated sport values. Only applies to `heart_rate` — ignored for `pace`/`gap`, which are always running-only. |

**Response `200`:** `PerformanceCurveOut` — `available` (`false` — never fabricated — when no
activity in range qualifies for this metric), `metric` (string, echoes the request), `points`
(`PerformanceCurvePointOut[]`, one per duration bucket that has data, never every bucket padded
with nulls), `threshold_pace_s_per_km`/`aerobic_threshold_pace_s_per_km`/`threshold_hr_bpm`/
`aerobic_threshold_hr_bpm`/`max_hr_bpm` (all number, nullable — only the pair relevant to
`metric` is ever non-null; these are the athlete's own already-computed reference values from
`GET /performance`, shown alongside the curve, never blended into it).

`PerformanceCurvePointOut`: `duration_s` (integer), `value` (number — bpm for `heart_rate`,
seconds/km for `pace`/`gap`), `activity_id` (string, the activity that actually set this
bucket's record), `local_date` (string, date, that activity's own local date).

---

## Gear

Shoes are athlete-owned gear. Mileage is calculated from distance-bearing activities, never kept
as a mutable counter: an activity's selected pair takes precedence; otherwise the sport default
applies from the moment it was assigned. `initial_distance_km` adds kilometres already used before
the pair was entered in Perseverer.

### `GET /gear/shoes`

Returns `array<ShoeOut>`. `ShoeOut` includes `brand`, `model`, optional `size` and `comments`,
`initial_distance_km`, `max_distance_km`, live `distance_km`, `remaining_km`, `over_limit`, and
`default_sports`.

### `POST /gear/shoes`

Creates a pair. Request body: `brand` and `model` (required strings); `size` and `comments`
(optional strings); `initial_distance_km` (number, default `0`, non-negative); and
`max_distance_km` (positive number, default `800`, or `null` for no mileage limit). Returns the
created `ShoeOut` (`201`).

### `PUT /gear/defaults/{sport}`

Sets the athlete's default pair for a sport. Request body: `{ "shoe_id": "…" }`. A changed
default starts a new mileage boundary, so prior activities are not reassigned.

### `PUT /gear/shoes/{shoe_id}/retire`

Retires a pair while preserving its history. Retired pairs are excluded from normal Gear lists,
cannot be selected as a default, and no longer create mileage alerts. Add
`include_retired=true` to `GET /gear/shoes` to include them.

### `GET /gear/activities/{activity_id}/shoe`

Returns `{ "shoe_id": string | null, "is_default": boolean }` — the pair that actually applies to
this activity, resolved the same way mileage totals are: this activity's own explicit choice if
one was made, otherwise the athlete's dated sport default when the activity falls on or after
that default's own assignment date. `is_default` is `true` when `shoe_id` came from that
fallback rather than an explicit per-activity pick; `shoe_id` is `null` only when neither applies.

### `PUT /gear/activities/{activity_id}/shoe`

Sets, replaces, or clears the pair for one distance-bearing activity. Request body:
`{ "shoe_id": string | null }`. The shoe must belong to the authenticated athlete; use `null`
to remove the explicit choice and resume the dated sport-default fallback. Mileage is derived from
the current assignment, so replacing a pair removes that activity's distance from the old pair
and adds it to the replacement. Returns the same resolved shape as the `GET` above (clearing an
explicit choice with `null` immediately resolves back to the sport default, if one applies).

### `GET /gear/alerts`

Returns each pair whose calculated mileage, including its initial distance, has reached its
configured maximum. The app uses this for its persistent banner. The worker sends one email per
pair when SMTP and an athlete email address are configured.

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

Attaches a free-text note to an activity, a calendar day, or a calendar week — the write path an
AI agent (or the UI) uses to record observations. The one write endpoint most of this API is
deliberately read-only around.

**Request body** (`NoteCreate`):

| Field | Type | Required | Description |
|---|---|---|---|
| `entity_type` | `"activity"` \| `"day"` \| `"week"` | required | |
| `entity_id` | string | required | An activity id when `entity_type` is `activity`; a `local_date` (`YYYY-MM-DD`) string when `entity_type` is `day`; that week's own Monday `local_date` when `entity_type` is `week` (matching `rollups.py`'s own Monday-start week convention). |
| `body` | string | required | |
| `author` | string, nullable | optional | |

**Responses:** `201` → `NoteOut`. `404` → `detail: "activity not found"` — only checked when
`entity_type` is `activity`; a `day`/`week` entity_id is never validated against anything, since
both are just a date, not a row that can be missing.

### `GET /notes`

Lists notes attached to one entity.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `entity_type` | query | **required** | string | `activity`, `day`, or `week`. |
| `entity_id` | query | **required** | string | |

**Response `200`:** `array<NoteOut>`.

### `PUT /notes/{note_id}`

Edits a note's text. Only `body` is editable — `entity_type`/`entity_id` never move (delete and
re-create elsewhere if that's really what's meant), and `author` is set once at creation.

| Param | In | Required | Type |
|---|---|---|---|
| `note_id` | path | **required** | integer |

**Request body** (`NoteUpdate`):

| Field | Type | Required |
|---|---|---|
| `body` | string | required |

**Responses:** `200` → `NoteOut`. `404` → `detail: "note not found"` (missing, or belongs to
another athlete).

### `DELETE /notes/{note_id}`

Deletes one note.

| Param | In | Required | Type |
|---|---|---|---|
| `note_id` | path | **required** | integer |

**Responses:** `200` (no response body of interest). `404` → `detail: "note not found"`.

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

A step's duration may also be the keyword `lap`, which ends the step on the watch's lap button
instead of a time or distance — for terrain whose real boundary is a landmark rather than an
exact distance. `lap` may be followed by an ordinary duration token (`lap 5km`, `lap 40m`) used
solely as an estimate for the calendar's own duration/load figures; it is never sent to Garmin,
so the step waits for the button however far it actually runs.

A day can hold any number of independently-addressed workouts — every workout is identified by
its own `id`, never by date alone.

### `GET /planned-workouts`

Date-range summary list for the calendar grid's own per-day indicator — not the full workout,
just enough to render one per row (see `GET /planned-workouts/by-date/{local_date}` for the full
detail of every workout on one date).

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `start_date` | query | **required** | string (date) | Inclusive. |
| `end_date` | query | **required** | string (date) | Inclusive. |

**Response `200`:** array\<`PlannedWorkoutListItemOut`\>.

### `GET /planned-workouts/by-date/{local_date}`

Every workout scheduled on one date — an empty array, one, or many. Ordered by `scheduled_time`
(nulls last) then `id`, the order the calendar's own day panel displays them in.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `local_date` | path | **required** | string (date) | |

**Response `200`:** array\<`PlannedWorkoutOut`\>.

### `GET /planned-workouts/{workout_id}`

One workout, its parsed steps, and any parse errors from the currently-stored `source_text`.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `workout_id` | path | **required** | integer | From `PlannedWorkoutOut.id`. |

**Responses:** `200` → `PlannedWorkoutOut`. `404` → `detail: "planned workout not found"`.

### `POST /planned-workouts`

Creates a new workout on the given date, alongside any others already scheduled there.

**Request body** (`PlannedWorkoutCreateIn` — `PlannedWorkoutIn` below plus `local_date`):

| Field | Type | Required | Description |
|---|---|---|---|
| `local_date` | string (date) | required | Which date to create the workout on. |

**Response `200`:** `PlannedWorkoutOut`.

### `PUT /planned-workouts/{workout_id}`

Updates that specific workout in place (and re-parses `source_text` into a fresh set of steps).
Its date can't be changed via this route. Editing a workout that was already `"pushed"` resets
`push_status` back to `"draft"` — the old Garmin copy is now stale and gets re-pushed fresh on
the next push.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `workout_id` | path | **required** | integer | From `PlannedWorkoutOut.id`. |

**Request body** (`PlannedWorkoutIn`):

| Field | Type | Required | Description |
|---|---|---|---|
| `sport` | string | required | `running` / `yoga` / `bouldering` / `fitness` / `hiit` / `strength_training` — open string, not an enum. |
| `name` | string, nullable | optional | |
| `source_text` | string, nullable | optional | For `running`: the athlete's own workout-syntax text — a malformed line doesn't reject the save, it's still stored, and the resulting `parse_errors` come back in the response. A standalone `"<N>x"` line starts a repeat block: every non-blank line that follows, up to the next blank line (or end of text), becomes one of its children — the boundary is the blank line, not indentation, so a step meant to follow the repeat needs its own blank line before it even if the repeat's own steps were typed indented. A trailing `# comment` on any line attaches a freeform note to that step (see `PlannedWorkoutStepOut.comment` below); a line that's only a comment errors as a missing duration. For `yoga`/`bouldering`: freeform notes only, never parsed. Ignored for `hiit`/`strength_training` — use `steps` instead. |
| `scheduled_time` | string, nullable | optional | `"HH:MM"` (24h). Perseverer's own calendar display metadata only — Garmin's own scheduling has no time-of-day API. |
| `duration_minutes` | number, nullable | optional | `yoga`/`bouldering` only — sets the workout's duration directly (there's no syntax to derive one from). Ignored for `running`/`hiit`/`strength_training`, where duration is derived instead. |
| `steps` | array\<`PlannedWorkoutStepIn`\>, nullable | optional | `hiit`/`strength_training` only — the exercise-picker steps, arriving already-structured (never parsed from text). Ignored for every other sport. |
| `comment` | string, nullable | optional | A general note for the *whole* workout, read before any step — `running`/`hiit`/`strength_training` only (distinct from a step's own `comment`; `yoga`/`bouldering` already use `source_text` as freeform notes). Never parsed. Pushed to Garmin as the workout's own `description` field. |

**Responses:** `200` → `PlannedWorkoutOut`. `404` → `detail: "planned workout not found"`.

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
| `comment` | string, nullable | optional | A freeform note on this specific step — typed directly for `hiit`/`strength_training` (a repeat-marker's own comment is the "set"'s comment). Never parsed, never pushed to Garmin. For `running`, ignored here — comes from an inline `#` token in `source_text` instead. |

### `DELETE /planned-workouts/{workout_id}`

Deletes that workout. If it was already pushed, also best-effort deletes the Garmin-side workout
template — a Garmin-side failure there (e.g. unreachable, no token store) never blocks the local
delete.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `workout_id` | path | **required** | integer | From `PlannedWorkoutOut.id`. |

**Responses:** `200` (no response body). `404` → `detail: "planned workout not found"`.

### `POST /planned-workouts/{workout_id}/push`

Manually pushes one workout to Garmin right now, regardless of date — the override alongside the
worker's own automatic push for anything due within the coming week
(`PERSEVERER_PLANNED_WORKOUT_PUSH_WINDOW_DAYS`, default 7). Runs in the background; poll
`GET /planned-workouts/{workout_id}` afterward for the updated `push_status`/`push_error`, since
that status lives on the workout row itself, not a generic job log.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `workout_id` | path | **required** | integer | From `PlannedWorkoutOut.id`. |

**Responses:** `200` → `JobTriggerOut`. `404` → `detail: "planned workout not found"`.

### `POST /planned-workouts/{workout_id}/complete`

The athlete's own manual "I did this" marker — entirely independent of `push_status` (works for a
workout that was never pushed, or failed to push, at all) and never linked to a recorded
`activity` row here; see `PlannedWorkoutOut.matched_activity_id` for the separate, read-time-only
signal a synced Garmin activity provides instead, without ever touching this column. Idempotent —
calling it again just refreshes `completed_at` to now.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `workout_id` | path | **required** | integer | From `PlannedWorkoutOut.id`. |

**Responses:** `200` → `PlannedWorkoutOut` (with `completed_at` now set). `404` → `detail:
"planned workout not found"`.

### `POST /planned-workouts/{workout_id}/uncomplete`

Clears the manual `completed_at` marker. Never clears `matched_activity_id` — a day the watch
already confirms as done via a synced activity still reads as done on `GET /planned-workouts`
even right after this call; see `db/schema.py::planned_workout`'s own docstring for why the two
signals are deliberately independent.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `workout_id` | path | **required** | integer | From `PlannedWorkoutOut.id`. |

**Responses:** `200` → `PlannedWorkoutOut` (with `completed_at` now `null`). `404` → `detail:
"planned workout not found"`.

### `POST /planned-workouts/recurring`

Creates one independent `planned_workout` row per occurrence date — not a recurring-rule object;
each row is a full copy of the same content and can be edited or deleted independently of the
others afterward. Always creates, even on a date that already has a workout scheduled — a day
can hold more than one, so there's nothing to skip.

**Request body** (`RecurringWorkoutIn`):

| Field | Type | Required | Description |
|---|---|---|---|
| `local_date` | string (date) | required | First occurrence. |
| `sport` | string | required | |
| `name` | string, nullable | optional | |
| `source_text` | string, nullable | optional | Applied to every created occurrence, comments (inline `#` tokens) included. |
| `scheduled_time` | string, nullable | optional | `"HH:MM"` (24h) — see `PlannedWorkoutIn` above. |
| `duration_minutes` | number, nullable | optional | `yoga`/`bouldering` only — see `PlannedWorkoutIn` above. |
| `steps` | array\<`PlannedWorkoutStepIn`\>, nullable | optional | `hiit`/`strength_training` only — see `PlannedWorkoutIn` above. |
| `comment` | string, nullable | optional | See `PlannedWorkoutIn` above — applied to every created occurrence. |
| `frequency` | string | required | `weekly` / `every_n_days` / `monthly`. |
| `interval_days` | integer | required for `every_n_days` | `>= 1`. |
| `count` | integer | exactly one of `count`/`until` | Total occurrences, including the first. |
| `until` | string (date) | exactly one of `count`/`until` | Inclusive. |

**Responses:** `200` → `RecurringWorkoutOut`. `422` → invalid `frequency`, missing
`interval_days` for `every_n_days`, or neither/both of `count`/`until` given. `detail` is a plain
string.

---

## Races on the calendar

A dated event with a distance and an optional target finish time — deliberately its own table,
not a `planned_workout` sport tier: a race has no step model and is never pushed to Garmin. Own
id-keyed rows, any number per athlete per date, same route shape as `/planned-workouts`.

### `GET /planned-races`

Date-range list for the calendar grid's own per-day indicator.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `start_date` | query | **required** | string (date) | Inclusive. |
| `end_date` | query | **required** | string (date) | Inclusive. |

**Response `200`:** array\<`PlannedRaceOut`\>.

### `GET /planned-races/by-date/{local_date}`

Every race scheduled on one date — an empty array, one, or many.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `local_date` | path | **required** | string (date) | |

**Response `200`:** array\<`PlannedRaceOut`\>.

### `POST /planned-races`

Creates a new race.

**Request body** (`PlannedRaceIn`):

| Field | Type | Required | Description |
|---|---|---|---|
| `local_date` | string (date) | required | |
| `name` | string | required | Non-blank. |
| `sport` | string | optional | Default `"running"` — open string, not an enum (a cycling sportive or a swim event can be logged too). |
| `distance_m` | number | required | Must be positive. |
| `scheduled_time` | string, nullable | optional | `"HH:MM"` (24h). Display-only — races are never pushed to Garmin, so there's no vendor time-of-day constraint to work around. |
| `target_duration_s` | number, nullable | optional | The athlete's own goal finish time, e.g. "run in less than 4 hours" → `14400`. Must be positive when set. `null` means no target — the race still shows with just its distance/countdown. |

**Response `200`:** `PlannedRaceOut`. `422` → blank name, non-positive `distance_m`/
`target_duration_s`, or a malformed `scheduled_time`/`local_date`.

### `GET /planned-races/{race_id}`

One race.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `race_id` | path | **required** | integer | From `PlannedRaceOut.id`. |

**Responses:** `200` → `PlannedRaceOut`. `404` → `detail: "planned race not found"`.

### `PUT /planned-races/{race_id}`

Updates that race in place (a full replacement, same request body as `POST`).

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `race_id` | path | **required** | integer | From `PlannedRaceOut.id`. |

**Request body:** `PlannedRaceIn` (see `POST /planned-races` above).

**Responses:** `200` → `PlannedRaceOut`. `404` → `detail: "planned race not found"`.

### `DELETE /planned-races/{race_id}`

Deletes that race.

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `race_id` | path | **required** | integer | From `PlannedRaceOut.id`. |

**Responses:** `200` (no response body). `404` → `detail: "planned race not found"`.

`PlannedRaceOut` adds three fields beyond `PlannedRaceIn`, computed fresh on every read (never
stored — both go stale immediately, the countdown daily and the prediction the moment a new
performance rollup runs):

| Field | Type | Description |
|---|---|---|
| `id` | integer | |
| `days_until` | integer | `local_date` minus today, in days. Negative for a past race. |
| `predicted_duration_s` | number, nullable | The most recent `performance_daily_rollup` prediction for a standard distance matching `distance_m` (5K/10K/half marathon/marathon, small float tolerance) — `null` for a custom distance or before any qualifying rollup exists. Compared against `target_duration_s` by the frontend ("Predicted 3:52:10 — on track" or "— N over target"). |

Also surfaces in the iCal feed (a `🏁`-prefixed all-day or timed `VEVENT`, `docs/DEPLOY.md`'s own
calendar-feed docs) and the weekly email's "Races this week" section — both read the same
`planned_race` row the calendar already fetches.

---

## Weather forecast

### `GET /weather/forecast`

The athlete's own home-location forecast, up to Open-Meteo's own 16-day cap — the future-facing
counterpart to `GET /activities/{id}/weather`'s past-activity weather. Deliberately **not**
archived or cached (see `weather_forecast.py`'s own module docstring): a forecast has no
permanent-record concept, unlike every other vendor fetch in this codebase, since it's
superseded by reality as the date approaches. Requested in the athlete's own timezone (`PUT
/settings/profile`'s `timezone`), never a hardcoded UTC, so a forecast day's own date lines up
with the athlete's real local calendar dates rather than shifting by a day for roughly a third of
the globe.

`upcoming` additionally carries the same richer field set `GET /activities/{id}/weather` gathers
for a past run (dew point, solar radiation, cloud cover, apparent temperature, precipitation,
sunrise/sunset, an hour-by-hour trajectory), but for just the next 3 upcoming days
(`weather_forecast.UPCOMING_DETAIL_DAYS`) — from a **second, independent** Open-Meteo request,
so it can succeed or fail on its own regardless of `days`/`available` above. Unlike the activity
endpoint's own `hourly[]` (UTC), every datetime field under `upcoming` is the athlete's own local
time (see `ForecastDayDetailOut` below).

| Param | In | Required | Type | Description |
|---|---|---|---|---|
| `days` | query | optional | integer, 1-16 | Defaults to 16 (Open-Meteo's own cap). Only affects `days` below — `upcoming` always covers its own fixed 3-day window. |

**Response `200`:** `WeatherForecastOut`:

| Field | Type | Description |
|---|---|---|
| `available` | boolean | `false` — never a fabricated forecast — when the athlete hasn't set a home location (`PUT /settings/profile`'s `home_lat`/`home_lon`) or the coarse-forecast Open-Meteo fetch failed. |
| `days` | `ForecastDayOut[]` | Empty when `available` is `false`. |
| `upcoming` | `ForecastDayDetailOut[]` | The near-term rich-conditions detail, from a separate Open-Meteo request than `days`. `[]` whenever that request fails or returns nothing usable — independent of `available`/`days`, in either direction. |

`ForecastDayOut`:

| Field | Type | Description |
|---|---|---|
| `local_date` | string | ISO date. |
| `weather_code` | integer | WMO weather code (same taxonomy `GET /activities/{id}/weather`'s `weather_code` uses) — icon/label mapping is a frontend presentation concern, not modeled here. |
| `temperature_min_c` | number | |
| `temperature_max_c` | number | |

`ForecastDayDetailOut` — the same richer field set `ActivityWeatherOut` below collects for a
past run's own window, gathered instead for one upcoming calendar day. No scalar
`feels_like_c`/`wind_speed_mps`/`wind_direction_deg` here (unlike `ActivityWeatherOut`) — a whole
day has no single "activity start" hour to anchor one representative reading against, so
`hourly` carries per-hour wind/apparent-temperature instead, letting a consumer pick whichever
hour matches their own planned time:

| Field | Type | Description |
|---|---|---|
| `local_date` | string (date) | The athlete's own local date. |
| `weather_code` | integer, nullable | |
| `temperature_min_c`, `temperature_max_c` | number, nullable | |
| `humidity_min_pct`, `humidity_max_pct` | number, nullable | |
| `dew_point_min_c`, `dew_point_max_c` | number, nullable | |
| `solar_radiation_max_wm2`, `solar_radiation_mean_wm2` | number, nullable | |
| `cloud_cover_min_pct`, `cloud_cover_max_pct` | number, nullable | |
| `apparent_temperature_min_c`, `apparent_temperature_max_c` | number, nullable | |
| `precipitation_mm` | number, nullable | A day-total **sum**, not a range — `0.0` is a real reading (no rain forecast), `null` means no usable precipitation data at all. |
| `sunrise_local`, `sunset_local` | string (date-time), nullable | The athlete's own local time, not UTC. |
| `hourly` | `ForecastHourlyPointOut[]` | Defaults to `[]`. |

`ForecastHourlyPointOut` — one hourly bucket of a single upcoming day; `time_local` is naive, in
the athlete's own local time, **not** UTC (unlike `ActivityWeatherHourlyPointOut`'s own
`time_utc` below):

| Field | Type | Description |
|---|---|---|
| `time_local` | string (date-time) | |
| `temperature_c`, `apparent_temperature_c`, `dew_point_c` | number, nullable | |
| `relative_humidity_pct` | number, nullable | |
| `shortwave_radiation_wm2`, `cloud_cover_pct` | number, nullable | |
| `wind_speed_mps`, `wind_direction_deg` | number, nullable | |
| `precipitation_mm` | number, nullable | |

**`422`** — `days` outside 1-16.

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

### `GET /settings/profile`

The athlete's optional profile facts (birthdate, height, biological sex, email, home location,
timezone). birthdate/height_cm/sex are used only as inputs to formula-based fallbacks elsewhere
(`GET /performance`'s `max_hr_bpm`, `GET /health/dashboard`'s `bmr_kcal`) when there isn't enough
empirical/device data yet — never reconciled against or overriding real data once it exists.
`email` is the recipient for the opt-in weekly/monthly training-report emails (`GET/PUT
/settings/email-reports`). `home_lat`/`home_lon` are the one location `GET /weather/forecast`
fetches a forecast for. `timezone` is the athlete's own IANA timezone — also used by `GET
/weather/forecast` (so a forecast's own days land on the athlete's real local dates) and by the
calendar feed's VTIMEZONE. `null` for any field except `timezone` means not set (`timezone` is
never null, defaulting to `"UTC"`).

**Response `200`:** `AthleteProfileOut` — `birthdate` (string, nullable, ISO date), `height_cm`
(number, nullable), `sex` (`"male"|"female"`, nullable), `email` (string, nullable), `home_lat`
(number, nullable, degrees), `home_lon` (number, nullable, degrees), `timezone` (string, e.g.
`"America/Los_Angeles"`).

### `PUT /settings/profile`

Replaces the athlete's profile. **A full replacement, not a partial patch**, same convention as
`PUT /settings/hr-zones` — omitting `timezone` resets it to `"UTC"` since it can never be null.

**Request body** (`AthleteProfileIn`): `birthdate` (string, nullable, ISO date — rejected if in
the future or implies an age over 120 years), `height_cm` (number, nullable, 50-250), `sex`
(`"male"|"female"`, nullable), `email` (string, nullable, a light format check only), `home_lat`
(number, nullable, -90..90), `home_lon` (number, nullable, -180..180 — must be set together with
`home_lat`, both or neither), `timezone` (string, default `"UTC"` — must be a real IANA timezone
name) — all optional except `timezone` carries a default rather than being nullable.

**Response `200`:** `AthleteProfileOut`. **`422`** — a value fails validation.

### `GET /settings/personalize`

Four pure display preferences — week start day, time format, starting page on load, and distance
units. A deliberate second endpoint from `/settings/profile` (same `athlete` table, different
concern): unlike Profile's fields, none of these four are ever read by any backend computation,
only by the frontend's own rendering. `unit_preference` reuses the same `athlete.unit_preference`
column `sync athlete create` has always set, previously never read anywhere in the app.

**Response `200`:** `PersonalizeSettingsOut` — `week_start_day` (`"monday"|"sunday"`, default
`"monday"`), `time_format` (`"24h"|"12h"`, default `"24h"`), `default_view`
(`"week"|"month"|"day"|"activities"`, default `"week"`), `unit_preference`
(`"metric"|"imperial"`, default `"metric"`).

### `PUT /settings/personalize`

Replaces the athlete's personalize settings. **A full replacement, not a partial patch**, same
convention as `PUT /settings/profile` — omitting any field resets it to its own default, since
every field here always carries one (none are nullable).

**Request body** (`PersonalizeSettingsIn`): same four fields as the `GET` response, each
optional with its own default. Any value outside the stated closed set is rejected.

**Response `200`:** `PersonalizeSettingsOut`. **`422`** — a value outside the closed set for any
field.

### `PUT /settings/password`

Self-service password change. Verifies `current_password` first — the same `is_locked_out`/
`verify_password`/`record_attempt` sequence `POST /auth/login` uses, so repeated wrong attempts
lock the athlete's username out the same way a brute-forced login would. Skips that check only
when the athlete has no password set yet (nothing to verify against). Never changes `username`.

**Request body** (`ChangePasswordIn`): `current_password` (string, required), `new_password`
(string, required, at least 8 characters).

**Response `200`:** `ChangePasswordOut` — `{"success": true}`. **`400`** — incorrect current
password, or no username configured yet. **`401`** — too many recent attempts, try again later.
**`422`** — `new_password` is too short.

### `GET /settings/api-key`

Self-service counterpart of `sync athlete create-key` — the Settings page's own per-athlete
`X-API-Key` (`athlete.api_key_hash`), same "one standing secret, replace on rotate" shape as the
calendar feed above, not a growing history of one-off keys.

**Response `200`:** `ApiKeyStatusOut` — `{"enabled": bool, "created_at": string | null}`. Never
includes the key itself — only whether one is currently active.

### `POST /settings/api-key`

Generates (if none exists) or rotates (if one already does) the athlete's API key, always minting
a fresh value — the old key's hash is overwritten, not appended to a list, so any client still
using it starts getting `401`s immediately.

**Response `200`:** `ApiKeyOut` — `{"api_key": string}`. The raw key is only ever returned here;
only its SHA-256 hash is stored, and it cannot be recovered later — losing it means rotating to a
new one.

### `DELETE /settings/api-key`

Revokes the athlete's API key (any client using it starts getting `401`s immediately).

**Response `200`:** `ApiKeyStatusOut` — `{"enabled": false, "created_at": null}`.

### `GET /settings/eufy/status`

Whether the athlete has a Eufy scale account connected. Never carries the password — only the
configured email, so the athlete can confirm which account is linked.

**Response `200`:** `EufyStatusOut` — `configured` (bool), `email` (string, nullable).

### `POST /settings/eufy/login`

The web counterpart of `sync athlete set-eufy-credentials`. Verifies the credential against
Eufy's own login endpoint BEFORE saving it, mirroring `POST /settings/garmin/login`'s own "don't
persist something we haven't confirmed works" posture. `device_id`/`customer_id` can't be
verified this way (only exercised by a real sync) — stored as given either way. Unlike Garmin,
the credential is persisted in full (plaintext, same as this project's legacy env-var
precedent) — Eufy sync needs it again for every future run.

**Request body** (`EufyLoginIn`): `email` (string), `password` (string), `device_id` (string),
`customer_id` (string) — all required.

**Response `200`:** `EufyLoginOut` — `{"success": true}`. **`400`** — incorrect Eufy email or
password. **`502`** — could not reach Eufy, try again.

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

## Calendar feed

A Google-Calendar-subscribable public iCalendar (.ics) feed of the athlete's own `planned_workout`
calendar — a parallel mechanism to Sharing above (single standing per-athlete secret, mirroring
the API key, not a growing list of one-off tokens), not a third share kind. See
`docs/DATA_DICTIONARY.md`'s own "Calendar feed" section for the full event-rendering rules.

### `GET /settings/calendar-feed`

**Response `200`:** `CalendarFeedStatusOut` — `{"enabled": bool, "created_at": string | null}`.
Never includes the feed URL or token itself — only whether one is currently published.

### `POST /settings/calendar-feed`

Publishes (if not already) or rotates (if already published) the feed, always minting a fresh
token — invalidating any previously issued link.

**Response `200`:** `CalendarFeedUrlOut` — `{"url": "https://.../share/calendar/<token>.ics"}`.
The raw token is only ever returned here; only its SHA-256 hash is stored, and it cannot be
recovered later — losing it means rotating to a new one.

### `DELETE /settings/calendar-feed`

Unpublishes the feed (the existing link stops working immediately).

**Response `200`:** `CalendarFeedStatusOut` — `{"enabled": false, "created_at": null}`.

### `GET /settings/email-reports`

The athlete's opt-in for the weekly / monthly training-report emails.

**Response `200`:** `EmailReportConfigOut` — `{"weekly_enabled": bool, "monthly_enabled": bool,
"smtp_configured": bool, "recipient_email": string | null}`. `smtp_configured` is whether the
deployment has an SMTP relay set up (`PERSEVERER_SMTP_*`); `recipient_email` echoes
`athlete.email` (set via `PUT /settings/profile`) — reports go there. Both are read-only context
for the UI.

### `PUT /settings/email-reports`

Sets the two opt-in switches (upsert). Both default `false`; no configuration means no emails.

**Body:** `EmailReportConfigIn` — `{"weekly_enabled": bool, "monthly_enabled": bool}`.
**Response `200`:** `EmailReportConfigOut`.

### `POST /settings/email-reports/test`

Sends the *current* weekly report to the athlete's own `athlete.email` immediately — the way to
verify SMTP and the Profile email without waiting for the scheduled send.

**Response `200`:** `{"triggered": true}`. **`400`** if SMTP or the Profile email isn't
configured; **`502`** if the SMTP send itself fails.

### `GET /share/calendar/{token}.ics`

**No authentication.** Same "public by omission" posture as `GET /share/{token}` above, and
reuses the same `/share/` reverse-proxy forwarding rule. An unknown or unpublished token returns a
plain **`404`** (unlike `GET /share/{token}`'s own soft "unavailable" HTML page — there is no
public page here for a bad token to render, so a normal HTTP error code is the honest response).

**Response `200`:** `text/calendar; charset=utf-8` — a full `VCALENDAR` with one `VEVENT` per
planned workout, freshly regenerated on every request (Google Calendar itself polls a subscribed
feed roughly every 8-24 hours, not live).

---

## System

### `GET /healthz`

Liveness check. Unauthenticated. Always `200` once the process is up: `{"status": "ok"}`.

### `GET /version`

Server version and environment. Unauthenticated: `{"version": "0.1.0", "environment": "production"}`.

---

## MCP server

Most read endpoints above, plus the full Notes CRUD (`create_note`/`list_notes`/`update_note`/
`delete_note`), are also exposed as MCP (Model Context Protocol) tools for AI agents, mounted at
`/mcp` on the same host and gated by the same `X-API-Key` credential described in Authentication.
Each tool is a thin wrapper that calls its REST counterpart in-process — the shapes documented
above apply there too. Write tools are also exposed: planned workouts (create/update/delete/
complete/uncomplete/push/recurring — the create tools' descriptions carry the full running
workout syntax, so an agent can author a training plan; a saved workout is a draft until the
daily job or `push_planned_workout` sends it to the athlete's Garmin), planned races, goals, blood
tests, gear, and per-activity sport/race/name/fueling corrections. Deliberately not exposed: every
`settings/*` endpoint (credentials and operational actions), `auth/login`, the public
`share`/`calendar` feed pages, and trim/merge/split/climb-route edits. See
`docs/adr/0007-phase-4-mcp-server.md` decisions 8-9.

Authentication for `/mcp` is either the `X-API-Key` header above or **OAuth 2.1** for remote
clients that cannot send one (e.g. claude.ai's custom connector): discovery at
`/.well-known/oauth-authorization-server` and `/.well-known/oauth-protected-resource/mcp`,
dynamic client registration at `POST /register`, `GET /authorize` (PKCE `S256` required), `POST
/token` (`authorization_code` and `refresh_token` grants; access tokens last 1 hour, refresh
tokens 30 days and rotate), and `POST /revoke` (send an empty `client_secret` for a public
client). The authorize step sends the browser to `/oauth/login`, where the athlete signs in with
their Perseverer username/password; only the deployment's primary athlete may authorize. See ADR
0007 decision 10.

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
or the Open-Meteo fetch/parse came back empty; every other field is `null` (or `hourly: []`) in
that case rather than omitted. `temperature_min_c`/`temperature_max_c`/`humidity_min_pct`/
`humidity_max_pct` (number, nullable) are ranges across the activity's own duration; `weather_code`
(integer, nullable — WMO weather interpretation code, see https://open-meteo.com/en/docs) is a
single representative code at the hour closest to the activity's start. `feels_like_c`,
`wind_speed_mps` (metres/second), and `wind_direction_deg` (degrees, meteorological convention —
the direction the wind is blowing *from*) are likewise single values at that same closest hour,
not ranges, and each is independently nullable since Open-Meteo's historical archive doesn't
always carry every field for every hour.

Everything below was added so this one endpoint supports a full conditions judgement (heat stress
in bpm/pace terms) without a second call to Open-Meteo. All are window aggregates over the
activity's own duration (same convention as `temperature_min_c`/`max_c` above) unless noted, and
all are independently `null` — never fabricated — whenever Open-Meteo's response lacks that
particular data, including on every activity whose weather was cached before these fields
existed (until a backfill re-fetches it, see docs/DATA_DICTIONARY.md's own Weather section):

| Field | Type | Description |
|---|---|---|
| `dew_point_min_c` / `dew_point_max_c` | number, nullable | Dew point (°C) — the humidity number that actually predicts heat strain; relative humidity alone doesn't. |
| `solar_radiation_max_wm2` / `solar_radiation_mean_wm2` | number, nullable | Shortwave radiation (W/m²) — direct-sun load. Sustained readings past ~800 are severe. |
| `cloud_cover_min_pct` / `cloud_cover_max_pct` | number, nullable | Cloud cover (%). |
| `apparent_temperature_min_c` / `apparent_temperature_max_c` | number, nullable | Full-window range on apparent temperature — a materially different signal than the single start-of-run `feels_like_c` above, since apparent temperature sitting notably below air temperature (dry air/wind doing real evaporative-cooling work) is invisible in one value. |
| `precipitation_mm` | number, nullable | A window **sum**, not a range — "how much rain fell during the run." `0.0` is a real reading (no rain); `null` means no usable precipitation data for this window at all. |
| `sunrise_utc` / `sunset_utc` | string(date-time), nullable | The daily entry matching the activity's own start date — not a range. |
| `sunset_during_run` | boolean, nullable | Whether `sunset_utc` falls inside `[start, end]` of the activity — computed at request time, never stored. `null` only when `sunset_utc` itself is `null` (nothing to judge against). |
| `hourly` | array\<`ActivityWeatherHourlyPointOut`\> | The hour-by-hour trajectory across the activity's window — the field that actually replaces a consumer's own second Open-Meteo call. `[]` (never omitted) when nothing was ever archived for this activity or no hour overlaps the window. |

### ActivityWeatherHourlyPointOut

One hourly bucket of the activity's own window, re-derived from the archived raw Open-Meteo
response on every request rather than stored (`activity_metric` is scalar-only, so an hourly
series doesn't fit it — see docs/DATA_DICTIONARY.md's own Weather section). Every field below is
independently `null` — never fabricated — whenever Open-Meteo's response lacks that array
entirely (an old archive predating `dew_point_2m`/`shortwave_radiation`/`cloud_cover`) or that
one hour's reading.

| Field | Type | Description |
|---|---|---|
| `time_utc` | string(date-time) | The UTC hour this bucket starts at. |
| `temperature_c` | number, nullable | Air temperature. |
| `apparent_temperature_c` | number, nullable | |
| `dew_point_c` | number, nullable | |
| `relative_humidity_pct` | number, nullable | |
| `shortwave_radiation_wm2` | number, nullable | |
| `cloud_cover_pct` | number, nullable | |
| `wind_speed_mps` | number, nullable | |
| `wind_direction_deg` | number, nullable | Degrees, meteorological convention — the direction the wind is blowing *from*. |
| `precipitation_mm` | number, nullable | This one hour's own reading (not a sum — that's the day/window-level `precipitation_mm` above). |

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

### BloodTestResultOut

| Field | Type | Description |
|---|---|---|
| `id` | integer | |
| `local_date` | string (date) | The draw date. |
| `marker` | string | e.g. `"LDL Cholesterol"` — the athlete's own label. |
| `value_num` | number | |
| `unit` | string, nullable | |
| `reference_low`, `reference_high` | number, nullable | The athlete's own lab-reported range; informational only. |
| `lab_name` | string, nullable | |
| `notes` | string, nullable | |
| `created_at`, `updated_at` | string (date-time) | |

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

### PerformanceDailyRollupOut

`local_date`, `rolling_vdot` (number, nullable — the VO2max estimate), `max_hr_bpm` (number,
nullable), `max_hr_source` (`"empirical"` | `"formula_fallback"` | null),
`threshold_pace_s_per_km`/`threshold_hr_bpm`/`threshold_hr_source` (anaerobic threshold; number,
nullable / number, nullable / `"empirical"` | `"fallback"` | null),
`aerobic_threshold_pace_s_per_km`/`aerobic_threshold_hr_bpm`/`aerobic_threshold_hr_source`
(aerobic threshold, always a slower pace/lower HR than the anaerobic one above; same shape),
`predicted_5k_s`/`predicted_10k_s`/`predicted_half_marathon_s`/`predicted_marathon_s` (number,
nullable).

### Vo2maxFactorAnalysisOut

| Field | Type | Description |
|---|---|---|
| `as_of` | string (date) | |
| `window_start`, `window_end` | string (date) | The trailing window `rolling_vdot` was computed over. |
| `rolling_vdot` | number, nullable | Same value `GET /performance` would return for `as_of`. |
| `driving_activity` | `Vo2maxContributorOut`, nullable | The one run whose own VDOT currently equals `rolling_vdot`. Null when no run in the window qualifies. |
| `other_contributors` | `array<Vo2maxContributorOut>` | Every other qualifying run in the window, sorted by VDOT descending — these did NOT set the current value, but are what takes over if `driving_activity` ages out first. |
| `expires_on` | string (date), nullable | First date `driving_activity` no longer counts. |
| `days_since_last_qualifying_run` | integer, nullable | |
| `missing` | `array<string>` | Human-readable gap/staleness diagnostics, if any. |

### Vo2maxContributorOut

`activity_id`, `local_date`, `name` (string, nullable), `sport`, `distance_m` (number, nullable),
`duration_s` (number, nullable — moving time), `vdot` (number).

### PaceHrZonesOut

| Field | Type | Description |
|---|---|---|
| `as_of` | string (date) | |
| `profile_vdot` | number, nullable | The single best (highest) VDOT within its own lookback window (2 years for a race, 1 year for a training-run fallback) — not the athlete's literal entire history, and not `GET /performance`'s own 42-day rolling `rolling_vdot` either. Prefers an activity marked as a race over any training run (see `profile_vdot_source`). |
| `profile_vdot_activity` | `ActivityRefOut`, nullable | The one run that set `profile_vdot`. |
| `profile_vdot_source` | `"race"` \| `"training_run"` \| null | `"race"` when `profile_vdot` came from an activity marked as a race (`activity.is_race`) within the last 2 years — preferred, since VDOT is calibrated against genuine race efforts. `"training_run"` when no race qualifies in the last 2 years and this fell back to the best training run in the last year instead (a short, all-out training segment can post an inflated VDOT); the response's `missing` list carries an explicit caveat in that case. |
| `profile_max_hr_bpm` | number, nullable | The single highest heart rate in the last 2 years, any sport. |
| `profile_max_hr_source` | `"empirical"` \| `"formula_fallback"` \| null | `"formula_fallback"` when there's no empirical max-HR reading at all yet and the athlete has a birthdate set (Tanaka formula) — same convention `PerformanceDailyRollupOut.max_hr_source` uses. |
| `zones` | `array<PaceHrZoneOut>` | Always exactly 5, Zone 1 (Recovery) through Zone 5 (VO2 Max). |
| `missing` | `array<string>` | Human-readable gap diagnostics (no qualifying run yet, no HR data and no birthdate), if any. |

### PaceHrZoneOut

| Field | Type | Description |
|---|---|---|
| `number` | integer | 1-5. |
| `label` | string | e.g. `"Aerobic Threshold"`. |
| `description` | string | Plain-language "when and how to use this zone." |
| `pace_fast_s_per_km`, `pace_slow_s_per_km` | number, nullable | The fast/slow edges of the zone's pace band — `pace_fast` is always numerically smaller (a faster pace) than `pace_slow`. Zone 1 has no `pace_slow` (unbounded easy); Zone 5 has no `pace_fast` (unbounded fast). |
| `hr_low_bpm`, `hr_high_bpm` | integer, nullable | The zone's heart-rate range. |
| `hr_source` | `"empirical"` \| `"formula_fallback"` \| null | `"empirical"` when the range is the real 25th-75th percentile among the athlete's own qualifying runs at this pace; `"formula_fallback"` when there weren't enough of them yet. |
| `qualifying_run_count` | integer | Every one of the athlete's own runs (ever) whose pace falls in this zone's band — not just the `sample_runs` shown below. |
| `sample_runs` | `array<ZoneRunSampleOut>` | The literal "why these numbers" evidence — evenly sampled down to a cap when `qualifying_run_count` is large, so the low/middle/high of the real spread stay represented. |

### ZoneRunSampleOut

`ActivityRefOut`'s own fields, plus `pace_s_per_km` (number) and `avg_hr_bpm` (number).

### ActivityRefOut

`activity_id`, `local_date`, `name` (string, nullable), `sport`, `distance_m` (number, nullable),
`duration_s` (number, nullable — moving time).

### RaceReadinessOut

| Field | Type | Description |
|---|---|---|
| `available` | boolean | `false` — never fabricated — when there's no upcoming running race, or `race_id` doesn't belong to the caller. |
| `race_id`, `race_name`, `race_local_date`, `race_distance_m` | nullable | The targeted `planned_race`'s own fields. |
| `weekly_distance_target_m`, `long_run_target_m` | number, nullable | Interpolated from this race's own distance — see `race_readiness.py`'s own module docstring. |
| `as_of` | string (date), nullable | |
| `current` | `RaceReadinessPointOut`, nullable | Today's own reading — the last entry of `history` too. |
| `predicted_duration_s` | number, nullable | The same VDOT-based prediction `GET /planned-races` already surfaces for this distance, reused as-is — `null` for a non-standard distance. |
| `history` | `array<RaceReadinessPointOut>` | One point per week over the 182-day weekly-distance window, oldest first — a genuine backtest, not a fabricated smoothing. |
| `weekly_distance_series`, `long_run_series` | `array<RaceReadinessWeekOut>` | The actual realized distance behind the two compliance percentages above, not just the recency-weighted fraction — one entry per Monday-start week, oldest first, over each series' own window (182 days / 70 days). `0.0` never omitted for a week with nothing recorded. |

### RaceReadinessPointOut

`as_of` (string, date), `weekly_distance_compliance_pct` (number, 0-100), `long_run_compliance_pct`
(number, 0-100), `readiness_pct` (number, 0-100 — `0.6 * weekly_distance_compliance_pct + 0.4 *
long_run_compliance_pct`).

### RaceReadinessWeekOut

`week_start` (string, date — the Monday that week starts), `distance_m` (number).

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

`id` (integer), `entity_type` (`activity`/`day`/`week`), `entity_id`, `body`, `author` (nullable),
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
`scheduled_time` (string, nullable — `"HH:MM"`), `push_status` (`draft`/`pushed`/`push_failed`),
`completed_at` (string, nullable, ISO datetime — the athlete's own manual "I did this" marker,
`null` until marked; see `POST .../{workout_id}/complete` above), `matched_activity_id` (string,
nullable — a same-day, matching-sport recorded activity, if one exists; computed at read time,
never stored, never overriding `completed_at`). The Week view's own sport-by-sport compliance
stat counts a workout as done when either field is non-null.

### PlannedWorkoutOut

`available` (boolean, required) — always `true` in every response from the routes above; a
nonexistent `workout_id` is a `404`, not an `available: false` object, now that a workout is
always addressed by id rather than by date:

| Field | Type | Description |
|---|---|---|
| `id` | integer, nullable | |
| `local_date` | string (date), nullable | |
| `sport` | string, nullable | |
| `name` | string, nullable | |
| `source_text` | string, nullable | `running`: the athlete's own typed workout-syntax text, verbatim (inline `#` comments included). `yoga`/`bouldering`: freeform notes only, never parsed. `hiit`/`strength_training`: always `null` — see `steps`. |
| `scheduled_time` | string, nullable | `"HH:MM"` (24h) — display-only, not sent to Garmin. |
| `comment` | string, nullable | A general note for the whole workout, read before any step — `running`/`hiit`/`strength_training` only. Pushed to Garmin as the workout's own `description` field. |
| `estimated_duration_s` | number, nullable | `running`: an estimate — a distance-based step's real duration depends on the athlete's actual pace. `yoga`/`bouldering`: exactly the `duration_minutes` given at save time, in seconds. `hiit`/`strength_training`: an estimate computed from `steps` (a rough assumed seconds/rep for a reps-based step, real seconds otherwise). |
| `steps` | array\<`PlannedWorkoutStepOut`\> | Defaults to `[]`. Raw, unexpanded (repeat-block markers included). Always `[]` for `yoga`/`bouldering` — no structured syntax for those sports. |
| `parse_errors` | array\<`ParseErrorOut`\> | Defaults to `[]`. From re-parsing the currently-stored `source_text` — `running` only; always `[]` for `yoga`/`bouldering`. |
| `push_status` | string, nullable | `draft` / `pushed` / `push_failed`. |
| `push_error` | string, nullable | |
| `garmin_workout_id` | integer, nullable | |
| `garmin_scheduled_at` | string (date-time), nullable | |
| `completed_at` | string (date-time), nullable | The athlete's own manual "I did this" marker, set/cleared via `POST .../{workout_id}/complete`/`.../uncomplete`. `null` until marked. |
| `matched_activity_id` | string, nullable | A same-day, matching-sport recorded activity, if one exists. Computed at read time, never stored, never overriding `completed_at` — a companion "this looks done" signal for a workout already confirmed by a synced Garmin activity. |
| `estimated_distance_m`, `estimated_load` | number, nullable | `running` only — always `null` for `yoga`/`bouldering`/`hiit`/`strength_training`, and for `running` itself until the athlete configures a running-load threshold pace (`estimated_load` only). |
| `segments` | array\<`PlannedWorkoutSegmentOut`\> | Defaults to `[]`. Repeat-expanded (unlike `steps`), `running` only. |

### PlannedWorkoutSegmentOut

`duration_s` (number), `zone` (integer, nullable), `intensity_factor` (number, nullable).

### PlannedWorkoutStepOut

| Field | Type | Description |
|---|---|---|
| `step_index` | integer | |
| `duration_type` | string, nullable | `time` / `distance` / `lap_button` / `repeat_until_steps_cmplt`. |
| `duration_time_s`, `duration_distance_m` | number, nullable | For `lap_button`, these are an **estimate only** — used for the calendar's planned-duration and load figures, never pushed to Garmin as an end condition. |
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
| `comment` | string, nullable | A freeform note on this specific step. `running`: parsed from an inline trailing `# comment` token on that step's own `source_text` line. `hiit`/`strength_training`: typed directly against that row in the exercise picker (a repeat-marker row's own comment is the "set"'s comment). Never parsed further, never sent to Garmin. |

### ParseErrorOut

`line_no` (integer, 1-indexed), `message` (string).

### RecurringWorkoutOut

`created_dates` (array\<string\>, dates) — every occurrence date got its own new row, even one
that already had a workout scheduled.

### PlannedRaceOut

| Field | Type | Description |
|---|---|---|
| `id` | integer | |
| `local_date` | string (date) | |
| `name` | string | |
| `sport` | string | Open string, default `"running"`. |
| `distance_m` | number | |
| `scheduled_time` | string, nullable | `"HH:MM"` (24h) — display-only. |
| `target_duration_s` | number, nullable | The athlete's own goal finish time. `null` means no target was set. |
| `days_until` | integer | `local_date` minus today, in days. Computed fresh on every read, never stored. |
| `predicted_duration_s` | number, nullable | The most recent `performance_daily_rollup` prediction for a standard distance matching `distance_m`. `null` for a custom distance or before any qualifying rollup exists. Computed fresh on every read, never stored. |
