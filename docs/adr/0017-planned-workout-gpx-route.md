# ADR 0017: A GPX route on a planned running workout

## Status

Step 1 (store and show the route) and step 2 (push it to Garmin as a private course) are both built.
Step 2 rests on an undocumented Garmin API, verified live on the athlete's own account on
2026-10-03 (see "Live verification"); it can break without notice, and the failure mode is a
recorded `garmin_course_error`, never a failed workout push.

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
4. **Shown on the workout card.** The route is drawn on the workout card with the existing
   `ActivityMap` (CARTO raster thumbnail). Reusing it keeps one map component and avoids spinning
   up a WebGL canvas per card (see that component's own docstring).
5. **Not exposed through MCP**: attaching a file isn't something an agent can usefully do; the
   `route` summary is already on every `PlannedWorkoutOut` an agent reads.

## Step 2 — pushing the course to Garmin

6. **Push the route as a private Garmin course, alongside the workout push** (`planned_workouts.py::
   _push_route_course` after the workout succeeds; `GarminConnectAdapter.push_course`). Once per
   route: an unchanged route is not re-uploaded, a replaced one deletes the stale course first. The
   course name is `"<workout name> <date> (Perseverer)"`.
7. **Fail closed on privacy.** The athlete's existing courses are public (`privacyRule.typeId 1`)
   and a course carries the route's start location, so the push sends `rulePK 2`, then reads the
   course list and requires `privacyRule.typeKey == "private"`; anything else (or the course not
   being listed) deletes it again and raises `CourseError`.
8. **A course failure never fails the workout push** — it lands in `planned_workout.garmin_course_error`
   (shown on the card). Only a Garmin 429 propagates, to stop a multi-workout loop, exactly like the
   workout push. Detaching the route, or deleting the workout, deletes the Garmin course best-effort.
9. **Same safety contract as every Garmin call**: token store only, never credentials, one
   rate-limited call per request, a 429 aborts with no retry.

## Live verification (2026-10-03, the athlete's own account)

Run by hand with the saved token store, a handful of calls, a labelled test GPX
("PERSEVERER TEST - delete me") that was deleted afterwards:

- `POST /course-service/course/import` (multipart `file`) → 200 with a *draft* course
  (`courseId: null`, `geoPoints`, `courseLines`); nothing is created yet.
- `POST /course-service/course` with the draft → 400 `'createCourse.arg3.sourceTypeId' must not be
  null`; retried with `sourceTypeId: 3` (the value the athlete's existing imported courses carry),
  `activityTypePk: 1`, `rulePK: 2`, `distanceMeter`, `startPoint` → 200, `courseId` returned. Garmin
  filled the elevation gain from its own model.
- `GET /course-service/course/{id}` → 200; `DELETE` the same → 204.
- A second run through the real `push_course` code path created a course, `GET
  /course-service/course` listed it with `privacyRule.typeKey == "private"`, and it was deleted.
- `GET /course-service/course` also showed the athlete's existing courses as
  `privacyRule {typeId: 1, typeKey: "public"}`, which is why the push verifies privacy every time.

## Consequences

- A running workout can carry a route with zero Garmin dependency; the Garmin push is an add-on.
- A Garmin course is a separate object from the scheduled workout; the athlete chooses the course on
  the watch (Courses). Whether a given watch lets you follow a course during a structured workout is
  not known.
- The `course-service` API is undocumented and may change; the blast radius is `garmin_course.py` and
  `push_course`.
