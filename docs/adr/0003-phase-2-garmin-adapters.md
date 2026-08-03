# ADR 0003: Phase 2 — garmin_export importer, garmin_connect adapter, scheduler, staleness

## Status

Accepted. Built and unit-tested against a fake Garmin client (45/45 tests passing, no real
network calls anywhere in the suite). Live validation of both adapters against real Garmin
data is explicitly deferred to the user — see decision 1 and 2.

## Context

Phase 1 proved the raw-first/rebuildable architecture with `fit_folder`. Phase 2 adds the two
Garmin-specific adapters the whole project depends on for ongoing operation. Two real
constraints, confirmed with the user before designing, shaped everything below:

1. **No Garmin export archive was available** (the user hadn't requested one — Garmin's export
   takes days to arrive). `garmin_export`'s FIT-discovery logic is fully buildable and testable
   without one; its health/wellness JSON parsing is not, since Garmin's export JSON schema is
   undocumented and unsampled.
2. **The `garmin_connect` live login is the user's to run, not the agent's.** Garmin's SSO
   429-locks per account with no recovery path, and MFA is inherently interactive. The full
   adapter, token store, and `sync auth login` CLI command were built; the user runs `sync auth
   login` themselves when ready.

## Decisions

### 1. API verified by introspecting the installed package, not recalled from training data

The project spec explicitly warns that `python-garminconnect`'s API changes fast. Rather than
build against a remembered shape, the actual installed `garminconnect==0.3.8` package was
introspected directly (`inspect.signature`, `inspect.getsource`) for every method this adapter
calls: `Garmin.__init__`, `login`, `get_activities_by_date`, `download_activity`,
`ActivityDownloadFormat`, and the exception hierarchy. This caught a real, non-obvious behavior
that shaped the whole authentication design — see decision 2.

### 2. "Never fall back to credentials" is enforced by *not giving the client credentials*,
not by intercepting the library's own logic

Reading `Garmin.login()`'s actual source revealed it **does** fall back to a credentialed login
if the token store fails to load — `if not tokens_loaded: ... self.client.login(self.username,
self.password, ...)`. This is exactly the dangerous behavior the spec warns against. Rather
than try to intercept or monkeypatch that internal fallback, `GarminConnectAdapter.authenticate()`
simply never constructs the client with credentials in the first place (`Garmin()`, no email/
password). When the token store then fails to load, the library's own check
(`if not self.username or not self.password: raise GarminConnectAuthenticationError(...)`) fires
naturally — caught and re-raised as our own `GarminAuthRequired`. Credentials only ever exist in
one place in this codebase: `sync auth login`, run interactively by the user. This is verified
by a real (network-free) test: `test_authenticate_never_falls_back_to_credentials`.

### 3. `activity_source_link.raw_object_id` points at the FIT, not the JSON summary

`garmin_connect` archives both the JSON activity summary and the original FIT for every
activity — both get real `raw_object` rows — but only the FIT's `raw_object_id` is threaded
through to `activity_source_link`, because only the FIT is actually parsed (through the same
`parse_fit` as `fit_folder`/`garmin_export` — no second parser was written). The JSON is
archived and traceable via `raw_object` (queryable by `source`/`external_id`), just not
cross-referenced from the link table. "Every synced activity has its original FIT archived" is
what the acceptance criterion asks for, and that's exactly what this guarantees.

### 4. `garmin_export`'s health/wellness JSON is archived raw, not parsed — deliberately

Every non-FIT file in an export archive is still archived (`archive_raw_bytes`, raw-first holds
regardless), but nothing attempts to interpret Garmin's `DI-Connect-Wellness` JSON schema. That
schema is undocumented, has changed over the years, and no real sample existed to validate
against during this phase. Guessing it and getting it subtly wrong (wrong field names, wrong
units, wrong aggregation semantics) would be worse than not parsing it: it would either silently
drop real health data into the wrong shape, or need a corrective migration later. `health_
observation`/`health_stream` population from the export stays a fast-follow once the user has a
real archive to build and validate against — the schema for it already exists (Phase 1).

### 5. `garmin_export`'s external_id: filename first, FIT-content fallback

Garmin's own export naming (`<activityId>_ACTIVITY.fit`, confirmed from real filenames in
Phase 1's test data) embeds a real, stable, vendor-assigned ID — a strictly better identity
than the device-serial+start-time heuristic `fit_folder` needs for anonymous device-folder
drops. `ingest_canonical_batch` gained an optional `external_id_hint` parameter (Phase 1 code,
minimally extended) so `garmin_export` and `garmin_connect` can supply this directly while
`fit_folder` keeps its original derivation unchanged.

### 6. Rate limiting: interval + hard hourly cap + immediate abort on 429, no retries anywhere

`RateLimiter` sleeps between requests (default 3s) and tracks a rolling-hour request count,
raising rather than blocking once the hourly cap is hit — a sync run should finish in minutes,
not hours, and hitting the cap should mean "stop, let tomorrow's run continue," not "wait
around." A `GarminConnectTooManyRequestsError` aborts the run immediately with no retry of any
kind, anywhere in the call stack — the single behavior most directly tied to the account-lock
risk the spec calls out.

### 7. Scheduler: APScheduler `BlockingScheduler` + `CronTrigger` with built-in jitter

The worker container's only job going forward. `garmin_export` is never scheduled — it's a
one-off/occasional CLI action, not a recurring one. Default 04:15 local ± 600s jitter, both
configurable.

### 8. New dependencies

- `garminconnect>=0.3.5` — the current best Garmin client per the project spec; isolated
  entirely behind `adapters/garmin_connect.py`.
- `apscheduler>=3.10` — the scheduler named explicitly in the project spec.
- `httpx` promoted from dev-only to a real dependency (the staleness webhook POST;
  `notify_webhook` swallows `httpx.HTTPError` so a bad webhook URL can't crash the worker).

### 9. Testing strategy: dependency-injected fake client, zero real network access

`GarminConnectAdapter` takes an optional `client_factory: Callable[[], Garmin]` (defaulting to
the real `Garmin` class) purely for testability — tests inject a `FakeGarminClient` with no
`__init__` parameters that could carry credentials, which is itself part of the safety argument:
the adapter has no code path capable of passing credentials to an automatically-constructed
client, proven by the factory's zero-argument signature. `sync_garmin_connect` (the full
orchestration entrypoint the scheduler and CLI both use) accepts the same injection point, so
the full ingest_run-bookkeeping path is covered end to end, not just the adapter class in
isolation.

## Consequences

- The moment a real Garmin export archive exists, `docs/DATA_DICTIONARY.md`'s health/wellness
  tables need actual population code — tracked as follow-up work, not forgotten (decision 4).
- `sync auth login`'s first real run (by the user) is the actual validation of everything
  written against the introspected-but-never-executed-live API surface. Expect this library to
  break again per the spec's own framing; when it does, the fix should stay inside
  `adapters/garmin_connect.py`.
- Because `activity_source_link` only tracks the FIT's raw_object, a future audit view showing
  "all raw objects behind this activity" needs to join through `raw_object.external_id` /
  `source`, not assume one link row = one raw object per source.
