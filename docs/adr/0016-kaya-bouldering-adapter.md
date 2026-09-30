# ADR 0016: Kaya bouldering adapter — route-level logbook merged with Garmin sessions

## Status

Accepted; login and importer built (`sync import kaya`, `adapters/kaya_ingest.py`, migration
`e2b7c4d19a63` adding `kaya_session`/`kaya_ascent`), with the local-date merge from decision 6.
Deviations from the original plan: the parsed Kaya rows live in two durable tables keyed by Kaya ids
(re-upserted by the rebuild replay) rather than being written straight into `activity`, so
`apply_kaya_sessions` can re-derive the activities idempotently after any rebuild. The
authentication slice was built first (`adapters/kaya.py`, `sync auth kaya-login` /
`kaya-status`, unit-tested against a mocked HTTP layer). Endpoints and field names came from the
open-source [dofek](https://github.com/Asherlc/dofek/pull/2852) Kaya client
(`packages/kaya-client/src/client.ts`) and were **confirmed live on 2026-09-29**: password login
works, `POST /graphql` with `{query, variables:{user_id, offset, count}}` returns both queries
below with exactly the documented fields (36 sessions since 2024-09, 50-row ascent page).
Ingestion, merging and UI are not started.

Live shape notes: `ascent_type` is `Flash`/`Onsight`/`Redpoint`/`Repeat` (a `Repeat` is a re-climb,
not a new send); grades are lowercase `v0`…`v4` (`climb_type_group` is Kaya's own numeric bucket,
not the V number); `attempts`, `rating` and `name` are mostly null; `stiffness` is always 0; there
are no `board`/`destination` sessions (all gym). `climb.name` is usually null, so a route is
identified by `climb.id`. Ascents must be paged until exhausted (the first page was full).

## Context

Garmin's bouldering data is thin. `climb_result`/`climb_grade` are a reverse-engineered decode that
is occasionally wrong (hence the manual route-correction overrides), and a route climbed after the
watch stopped has no split at all. There are no route names, no flash/send distinction beyond that
decode, no star ratings, and outdoor or gym sessions where the watch was off are missing entirely.
Kaya holds exactly that: the logged problem, gym or crag, grade, ascent type, attempts, rating and
comments. The owner asked whether Kaya data can be retrieved and combined with the Garmin data.

Kaya publishes no API and offers CSV **import** only. Its own web/mobile app talks to a private
REST + GraphQL backend, which is the only automated route to the data.

## Decisions

1. **Use Kaya's private API, human-authenticated, like `garmin_connect`.**
   - Base URL `https://kaya-beta.kayaclimb.com` (app origin `https://kaya-app.kayaclimb.com`).
   - `POST /api/user/login` `{email, password}` → `{token, refresh_token, user: {id}}`;
     `POST /api/user/refresh-token` `{refresh_token}` → `{token}`; Bearer auth plus `origin`/
     `referer` headers.
   - Credentials are used in exactly one function (`login_with_credentials`), reached only from
     the human-initiated `sync auth kaya-login`. Only tokens are stored
     (`<data_dir>/kaya_tokens/<athlete_id>/tokens.json`, chmod 600 where supported), never the
     password. Scheduled/adapter code only loads or refreshes tokens; a dead refresh token raises
     `KayaAuthRequired` — never a silent credential fallback. Any 429 aborts the run with no retry.
   - CLI-only for now; a Settings-page card (mirroring `POST /settings/garmin/login`) is deferred.
   - This is an unofficial, undocumented `beta` host that can change or block us without notice.
     Accepted, because the alternative is no data; the blast radius is one adapter file.

2. **Raw first.** Every GraphQL/REST response is archived verbatim as a `raw_object`
   (`kind="kaya_sessions"`, `"kaya_ascents"`) with URL, timestamp, status and SHA-256 before any
   parsing; parsing is a pure function over the archive and `sync rebuild` replays it. Unmapped
   keys are registered in `metric_definition`, never dropped.

3. **Data pulled (GraphQL, offset/count paging):**
   - `sessionsForUser(user_id, offset, count)`: `id start_time end_time notes gym{…} board{…}
     destination{…}`.
   - `ascentsForUser(user_id, offset, count)`: `id session_id date comment rating stiffness
     attempts ascent_type{id name} gym{…} climb{id name lead climb_type{id name}
     grade{id name climb_type_group} gym{…}}`.
   - Unsuccessful attempts appear on sessions as `attempted_climbs` per the dofek PR; its query
     is not in the client excerpt read and is **to be discovered** from a live capture.
   - Full sync is cheap and idempotent (upsert on Kaya ids), so no incremental cursor at first.

4. **Model: a Kaya session is a bouldering `activity`; each ascent is a `split` row**, so goals,
   the grade chart, the routes table and the climbing summary work unchanged. Source ids
   (`kaya` session id, ascent id) live in `activity_source_link` for idempotency and provenance.
   Only bouldering ascents (`climb_type` bouldering) feed the bouldering views; roped climbs are
   archived and can get their own sport mapping later. Result mapping: an ascent with a flash/send
   `ascent_type` → `climb_result="completed"`; attempts-only → an incomplete result, so goals
   (which count completed only) stay correct.

5. **Grades.** Kaya grades are strings (`"V4"`, `"6B+"`) grouped by `climb_type_group`. A fixed
   mapping table converts to the V-scale integer the app stores (`climb_grade`); the original
   string is kept alongside and shown. An unmapped grade is stored null and reported, never guessed.

6. **Merging with Garmin: by local date, resolved per field.**
   - **Live finding (2026-09-29): Kaya session times cannot be matched by overlap.** A Kaya session
     is logged after the fact in a burst: `start_time`≈`end_time` (0–4 min for every recent session)
     and it lands hours after the watch started (Garmin 01:41 UTC vs Kaya 04:37 UTC the same
     evening). So a Kaya session merges into a Garmin bouldering activity that shares its **local
     date** (athlete timezone), not by interval overlap; with several Garmin activities the same
     day, the nearest start wins only if unambiguous. Kaya supplies no usable duration, so duration
     always comes from Garmin (or is null for a Kaya-only session).
   - Ambiguous matches (one Kaya session overlapping several Garmin activities or the reverse) are
     not merged automatically; they surface in the existing possible-duplicates review and the
     per-field manual merge (`activity_merge_override`).
   - Field ownership: sends (grade, result), ratings and comments come from Kaya; duration, heart
     rate, calories and **failed attempts** from Garmin. Found in the first dry run against real data:
     Garmin recorded attempt rows Kaya's ascent feed simply does not have, so Kaya's sends replace
     only Garmin's *completed* route splits and Garmin's attempt rows are kept. Both remain in the
     raw archive, so `sync rebuild` stays reproducible. Existing bouldering route corrections keyed
     on a replaced Garmin `split_index` no longer apply on a Kaya-covered day; they remain valid for
     Garmin-only sessions.
   - A Kaya session with no Garmin counterpart is a standalone activity and counts toward goals.
     Merge/split visibility reuses `GET /activities/{id}/sources` and split.

7. **Rollups and insights** follow the standing contract: touched `local_date`s are collected and
   `refresh_daily_rollup`, insights and period rollups run once per date after the loop.

## Consequences

- The bouldering data becomes route-accurate and covers sessions without a watch.
- New fragile vendor dependency: the private API may break or change auth; documented, isolated,
  and never in the scheduled path until a live capture proves it stable.
- Grade-mapping and Font↔V conversion introduce a small policy table to maintain.
- The `attempted_climbs` shape and the exact login behaviour (MFA, social sign-in) are open until
  the first real capture; if the account uses social sign-in only, password login will not work and
  this ADR must be revisited.

## Next steps

1. Owner runs `sync auth kaya-login`; capture and archive one real sessions and ascents page.
2. Correct this ADR from the real shapes; write the parsers and grade table with fixtures built from
   that capture.
3. Implement `sync import kaya`, session merging, then docs (`DATA_DICTIONARY.md`, `AGENTS.md`).
