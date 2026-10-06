"""Re-derives a `CanonicalBatch` from an already-archived `raw_object`'s bytes, without
re-archiving anything and without running merge-matching -- the one place outside the original
ingest adapters that turns raw bytes back into a parsed activity. Used by the sources-split
endpoint (`api/routers/activities.py`) to reconstruct a standalone activity from one source's
raw content after a human says two sources were wrongly merged together (see ADR 0012).

Every `raw_object.kind` an `activity_source_link` can actually point at is handled: `"fit"` (the
one shared kind every FIT-producing adapter -- fit_folder/garmin_export/garmin_connect/
strava_export -- archives under, via `ingest_dispatch.FIT_KIND`), `"fit_activity"` (the Phase 1
kind predating that unification, still real in this athlete's own archive for anything ingested
before it -- same `parse_fit`, same rebuild.py `row.kind.startswith("fit")` precedent this
mirrors; missing here until a real `activity_trim.py::clear_activity_trim` call against one such
activity failed with "raw bytes no longer parse as an activity" despite the bytes being fine),
and strava_export's own `"strava_export_gpx"`/`"strava_export_tcx"`/`"strava_export_manual_entry"`
(the last is a raw CSV row, not a stream file -- reconstructed the same way `strava_export.py`'s
own ingest path does, via `_activity_from_csv_only`). A kind this function doesn't recognize
returns `CanonicalBatch(kind="unrecognized", ...)` rather than raising -- callers must handle
that, not assume every source link's raw_object is necessarily re-parseable in isolation (e.g. a
`strava_export_csv_row` raw_object is provenance-only, never itself a split source).
"""

from __future__ import annotations

import json

from perseverer.adapters.strava_export import _activity_from_csv_only
from perseverer.fit.parser import parse_fit
from perseverer.fit.types import CanonicalBatch
from perseverer.gpx.parser import parse_gpx
from perseverer.ingest_dispatch import FIT_KIND
from perseverer.tcx.parser import parse_tcx

_PARSERS = {
    FIT_KIND: parse_fit,
    "fit_activity": parse_fit,
    "strava_export_gpx": parse_gpx,
    "strava_export_tcx": parse_tcx,
}


def _parse_manual_entry(content: bytes) -> CanonicalBatch:
    row = dict(json.loads(content))
    activity = _activity_from_csv_only(row)
    if activity is None:
        return CanonicalBatch(
            kind="unrecognized",
            activity=None,
            unrecognized_message_types=["strava_export_manual_entry"],
        )
    return CanonicalBatch(kind="activity", activity=activity)


def reparse_raw_object(kind: str, content: bytes) -> CanonicalBatch:
    if kind == "strava_export_manual_entry":
        return _parse_manual_entry(content)
    parser = _PARSERS.get(kind)
    if parser is None:
        return CanonicalBatch(kind="unrecognized", activity=None, unrecognized_message_types=[kind])
    return parser(content)
