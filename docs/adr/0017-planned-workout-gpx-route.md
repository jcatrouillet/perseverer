# ADR 0017: A GPX route on a planned running workout

## Status

Step 1 (store and show the route) is built. Step 2 (push it to Garmin as a course) is an
**experiment**, not a decision: nothing below about Garmin's course API is verified until the live
test recorded in "Live verification" has been run.

## Context

The athlete asked to schedule a running workout *with a GPX*. A scheduled workout today is a
structured workout (steps with duration/pace/HR/cadence/repeat targets) pushed to the Garmin watch;
it has no route. Checking the installed `garminconnect` package (not memory) showed:

- no course/route call at all — `upload_activity` is for *completed* activity files, and
  `upload_workout`/`schedule_workout` take a workout document that has no course field;
- a generic authenticated `client.post(...)` that a course call could be built on;
- Garmin's own Workout Builder has no way to attach a course to a workout, so even a successful
  course upload would be a separate object the athlete selects on the watch.

The only public GPX-to-course tool found (`themylogin/garmin-connect-tools`) drives a legacy JSF web
form with browser cookies and is almost certainly dead.

## Decisions

1. **Store the GPX with the planned workout, running only.** `POST
   /planned-workouts/{id}/route` (multipart), `DELETE` the same path, `GET .../route.gpx` for the
   original. Raw first: the file is archived verbatim (`raw_object`, `source="athlete_upload"`,
   `kind="planned_workout_gpx"`); `planned_workout.route_*` hold only a display summary (name,
   haversine distance, elevation gain, a polyline thinned to ~600 points) that can be re-derived
   from the archive. Removing a route detaches it; the archive is never deleted.
2. **The upload is untrusted input.** 5 MB cap; `<!DOCTYPE`/`<!ENTITY` declarations are refused
   outright (no reliance on the XML parser's defaults); a file with fewer than two valid points is
   rejected with a 400 that says why. Only running workouts accept a route.
3. **Elevation gain is approximate and says so**: rises under 1 m between points are ignored so a flat
   route doesn't accrue a phantom climb; `null` when the file has no elevation.
4. **Shown, not pushed (yet).** The route is drawn on the workout card with the existing
   `ActivityMap` (CARTO raster thumbnail). Reusing it keeps one map component and avoids spinning
   up a WebGL canvas per card (see that component's own docstring). Pushing it to Garmin is step 2.
5. **Not exposed through MCP**: attaching a file isn't something an agent can usefully do; the
   `route` summary is already on every `PlannedWorkoutOut` an agent reads.

## Step 2 — pushing the course to Garmin (experiment)

To be decided only after a single, human-approved live test on the athlete's own account, run with
the saved token store under the same rules as every Garmin call (never credentials, rate-limited,
abort on a 429 with no retry). The test uploads one small clearly-labelled GPX as a course, records
the exact endpoint, request shape and response, and the athlete deletes the test course afterwards.
If it works, the result is written below and a push can ride alongside the existing workout push; if
it doesn't, step 1 stands on its own and this ADR says why the push was abandoned.

## Live verification

_Not yet run._

## Consequences

- A running workout can carry a reference route today, with zero Garmin dependency.
- Even if step 2 works, a Garmin course is a separate object from the scheduled workout; the athlete
  would still choose the course on the watch.
