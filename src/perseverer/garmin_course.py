"""Garmin Connect "course" payload building -- the pure half of pushing a GPX route to the watch.

Verified live on the athlete's own account (2026-10-03, docs/adr/0017-planned-workout-gpx-route.md),
not assumed from documentation (Garmin's official Courses API is partner-only; this uses the same
`course-service` calls Garmin Connect's own web import makes):

1. `POST /course-service/course/import` (multipart `file`) parses a GPX and answers with a *draft*
   course (`courseId: null`, `geoPoints`, `courseLines`).
2. `POST /course-service/course` with that draft plus the fields below saves it.
   `sourceTypeId` is required (Garmin's own error says so); 3 is what the athlete's existing
   imported courses carry. `rulePK` is the privacy rule: the athlete's existing courses are
   `privacyRule.typeId == 1` ("public"); we send 2 and *verify* it came back "private" before
   keeping the course (see GarminConnectAdapter.push_course).
3. A course is a separate object from a scheduled workout; Garmin offers no link between them.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Any

RUNNING_ACTIVITY_TYPE_PK = 1
IMPORTED_SOURCE_TYPE_ID = 3
#: Garmin `privacyRule` ids as observed on the athlete's own account: 1 is "public". 2 is expected
#: to be "private" and is checked against the created course's reported `typeKey` every time.
PRIVATE_RULE_PK = 2
PRIVATE_TYPE_KEY = "private"
_EARTH_RADIUS_M = 6_371_000.0


class CourseError(RuntimeError):
    """The course could not be created, or was created with the wrong privacy and removed."""


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = radians(a[0]), radians(a[1]), radians(b[0]), radians(b[1])
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * _EARTH_RADIUS_M * asin(sqrt(h))


def build_course_payload(draft: dict[str, Any], course_name: str) -> dict[str, Any]:
    """The body for `POST /course-service/course`, from the draft `.../course/import` returned.

    Distance is recomputed from the points (the draft leaves it null); elevation gain is left null
    so Garmin fills it from its own elevation model (it did, on the live test)."""
    geo = draft.get("geoPoints") or []
    if len(geo) < 2:
        raise CourseError("Garmin could not read any route points from that GPX")
    distance = 0.0
    for i, point in enumerate(geo):
        if i:
            prev = geo[i - 1]
            distance += _haversine_m(
                (prev["latitude"], prev["longitude"]), (point["latitude"], point["longitude"])
            )
        point["distance"] = distance
    payload = dict(draft)
    payload.update(
        {
            "courseName": course_name,
            "activityTypePk": RUNNING_ACTIVITY_TYPE_PK,
            "distanceMeter": distance,
            "elevationGainMeter": None,
            "startPoint": {"latitude": geo[0]["latitude"], "longitude": geo[0]["longitude"]},
            "coordinateSystem": "WGS84",
            "rulePK": PRIVATE_RULE_PK,
            "sourceTypeId": IMPORTED_SOURCE_TYPE_ID,
        }
    )
    return payload


def course_privacy_key(course_list: list[dict[str, Any]], course_id: int) -> str | None:
    """The `privacyRule.typeKey` Garmin reports for `course_id` in `GET /course-service/course`,
    or None if that course isn't in the list."""
    for course in course_list:
        if course.get("courseId") == course_id:
            rule = course.get("privacyRule") or {}
            key = rule.get("typeKey")
            return str(key) if key is not None else None
    return None
