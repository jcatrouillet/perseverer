"""SQLAlchemy 2.0 Core schema — plain `Table` objects, no ORM.

Core (not declarative ORM) was chosen because most of this codebase's data model is
batch-ingest and open-ended (metric_key/value_num rows rather than fixed attributes per
class), and Core keeps us close to the SQL that DuckDB will later query across SQLite +
Parquet in one statement. See docs/adr/0002-phase-1-schema-and-ingestion.md.

Every table holding an athlete's own data carries `athlete_id`. The two intentional
exemptions are `metric_definition` and `source_registry`, which are shared catalogs/registries
rather than personal data — see ATHLETE_SCOPED_TABLES / EXEMPT_TABLES below, which
tests/db/test_schema.py enforces mechanically so this exemption list can't silently grow.

All datetime columns are declared as plain `DateTime()` (no `timezone=True`) and hold naive
values that are implicitly UTC. This is deliberate, not an oversight: SQLite has no native
timestamp-with-timezone type, and SQLAlchemy's SQLite dialect does not actually round-trip
`tzinfo` through `DateTime()` — a value written as timezone-aware UTC comes back
naive on read. Declaring `timezone=True` here would advertise a guarantee this backend can't
keep, and it's exactly what caused a real bug during Phase 1 development (merge-matching
compared a freshly-parsed aware datetime against a DB-read naive one and raised
`TypeError: can't subtract offset-naive and offset-aware datetimes` on real data — see
docs/adr/0002-phase-1-schema-and-ingestion.md). Every datetime that reaches this schema must
already be UTC; callers are responsible for converting, not this layer.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

# --- Athlete -----------------------------------------------------------------

athlete = Table(
    "athlete",
    metadata,
    Column("id", String, primary_key=True),
    Column("display_name", String, nullable=False),
    Column("timezone", String, nullable=False, default="UTC"),
    Column("unit_preference", String, nullable=False, default="metric"),
    Column("created_at", DateTime(), nullable=False),
    # Set by the garmin_export adapter on successful completion — the "days since last full
    # Garmin export" health signal (spec: nag past 90 days; see sporthealth/staleness.py).
    Column("last_full_export_at", DateTime(), nullable=True),
)

# --- Bronze: immutable raw archive --------------------------------------------

raw_object = Table(
    "raw_object",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("source", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("external_id", String, nullable=True),
    # Generalizes the original sketch's `request_url`: an HTTP URL for API sources, or a
    # local file path for file-based sources like fit_folder. Never used for de-duplication
    # (sha256 is) — purely provenance/debugging.
    Column("source_locator", Text, nullable=True),
    Column("http_status", Integer, nullable=True),
    Column("fetched_at", DateTime(), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("byte_size", Integer, nullable=False),
    Column("storage_path", Text, nullable=False),
    Column("created_at", DateTime(), nullable=False),
    # Per-athlete, not global: two athletes with byte-identical files (not realistic for real
    # activity data, but the tenancy model shouldn't leak across athletes even in theory).
    UniqueConstraint("athlete_id", "sha256", name="uq_raw_object_athlete_sha256"),
    Index("ix_raw_object_athlete_source_kind", "athlete_id", "source", "kind"),
)

# --- Core ----------------------------------------------------------------------

device = Table(
    "device",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("manufacturer", String, nullable=True),
    Column("product", String, nullable=True),
    Column("serial_number", String, nullable=True),
    Column("first_seen_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id",
        "manufacturer",
        "product",
        "serial_number",
        name="uq_device_athlete_identity",
    ),
)

activity = Table(
    "activity",
    metadata,
    Column("id", String(26), primary_key=True),  # ULID
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("start_time_utc", DateTime(), nullable=False),
    Column("utc_offset_s", Integer, nullable=False, default=0),
    Column("tz_name", String, nullable=True),
    Column("sport", String, nullable=False),
    Column("sub_sport", String, nullable=True),
    Column("name", String, nullable=True),
    Column("description", Text, nullable=True),
    Column("duration_s", Float, nullable=True),
    Column("moving_duration_s", Float, nullable=True),
    Column("distance_m", Float, nullable=True),
    Column("elevation_gain_m", Float, nullable=True),
    Column("calories", Float, nullable=True),
    Column("device_id", Integer, ForeignKey("device.id"), nullable=True),
    Column("primary_source", String, nullable=False),
    Column("created_at", DateTime(), nullable=False),
    Column("updated_at", DateTime(), nullable=False),
    Column("deleted_at", DateTime(), nullable=True),
    Index("ix_activity_athlete_start", "athlete_id", "start_time_utc"),
)

activity_source_link = Table(
    "activity_source_link",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("activity_id", String(26), ForeignKey("activity.id"), nullable=False),
    Column("source", String, nullable=False),
    # For fit_folder: derived from FIT content (device serial + start_time), never the
    # filename — see fit_folder.py. This is the idempotency key for "re-import = no-op".
    Column("external_id", String, nullable=False),
    Column("raw_object_id", Integer, ForeignKey("raw_object.id"), nullable=False),
    Column("ingested_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id", "source", "external_id", name="uq_activity_source_link_identity"
    ),
    Index("ix_activity_source_link_activity", "athlete_id", "activity_id"),
)

activity_metric = Table(
    "activity_metric",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("activity_id", String(26), ForeignKey("activity.id"), nullable=False),
    Column("metric_key", String, ForeignKey("metric_definition.metric_key"), nullable=False),
    Column("value_num", Float, nullable=True),
    Column("value_text", Text, nullable=True),
    Column("unit", String, nullable=True),
    Column("source", String, nullable=False),
    Column("created_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id",
        "activity_id",
        "metric_key",
        "source",
        name="uq_activity_metric_identity",
    ),
)

activity_stream = Table(
    "activity_stream",
    metadata,
    Column("activity_id", String(26), ForeignKey("activity.id"), primary_key=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("parquet_path", Text, nullable=False),
    Column("n_samples", Integer, nullable=False),
    Column("channels", Text, nullable=False),  # JSON list of channel names
    Column("sample_rate_hint", Float, nullable=True),
)

lap = Table(
    "lap",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("activity_id", String(26), ForeignKey("activity.id"), nullable=False),
    Column("lap_index", Integer, nullable=False),
    Column("start_time_utc", DateTime(), nullable=False),
    Column("duration_s", Float, nullable=True),
    Column("distance_m", Float, nullable=True),
    Column("avg_hr", Float, nullable=True),
    Column("max_hr", Float, nullable=True),
    Column("avg_speed_mps", Float, nullable=True),
    UniqueConstraint("athlete_id", "activity_id", "lap_index", name="uq_lap_identity"),
)

split = Table(
    "split",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("activity_id", String(26), ForeignKey("activity.id"), nullable=False),
    Column("split_index", Integer, nullable=False),
    Column("split_type", String, nullable=True),
    Column("start_time_utc", DateTime(), nullable=True),
    Column("end_time_utc", DateTime(), nullable=True),
    Column("duration_s", Float, nullable=True),
    Column("distance_m", Float, nullable=True),
    UniqueConstraint("athlete_id", "activity_id", "split_index", name="uq_split_identity"),
)

route_geom = Table(
    "route_geom",
    metadata,
    Column("activity_id", String(26), ForeignKey("activity.id"), primary_key=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("encoded_polyline", Text, nullable=True),
    # Equal to encoded_polyline for now — real Douglas-Peucker simplification is deferred to
    # Phase 7 (map explorer), which is when it actually matters. See ADR 0002.
    Column("simplified_polyline", Text, nullable=True),
    Column("min_lat", Float, nullable=True),
    Column("min_lng", Float, nullable=True),
    Column("max_lat", Float, nullable=True),
    Column("max_lng", Float, nullable=True),
    Column("start_lat", Float, nullable=True),
    Column("start_lng", Float, nullable=True),
    Column("end_lat", Float, nullable=True),
    Column("end_lng", Float, nullable=True),
    Index("ix_route_geom_athlete_bbox", "athlete_id", "min_lat", "min_lng", "max_lat", "max_lng"),
)

# --- Health (schema created now; population starts Phase 2) --------------------

health_observation = Table(
    "health_observation",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("metric_key", String, ForeignKey("metric_definition.metric_key"), nullable=False),
    Column("observed_at_utc", DateTime(), nullable=False),
    Column("local_date", String, nullable=False),  # ISO date, e.g. "2026-08-02"
    Column("interval_start", DateTime(), nullable=True),
    Column("interval_end", DateTime(), nullable=True),
    Column("aggregation", String, nullable=False),  # instant | interval | daily
    Column("value_num", Float, nullable=True),
    Column("value_text", Text, nullable=True),
    Column("unit", String, nullable=True),
    Column("source", String, nullable=False),
    Column("device_id", Integer, ForeignKey("device.id"), nullable=True),
    Column("raw_object_id", Integer, ForeignKey("raw_object.id"), nullable=True),
    UniqueConstraint(
        "athlete_id",
        "metric_key",
        "observed_at_utc",
        "source",
        name="uq_health_observation_identity",
    ),
    Index("ix_health_observation_athlete_date", "athlete_id", "local_date"),
)

health_stream = Table(
    "health_stream",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("metric_key", String, ForeignKey("metric_definition.metric_key"), nullable=False),
    Column("year_month", String(7), nullable=False),  # "2026-08"
    Column("parquet_path", Text, nullable=False),
    Column("n_samples", Integer, nullable=False),
    Column("source", String, nullable=False),
    UniqueConstraint(
        "athlete_id", "metric_key", "year_month", "source", name="uq_health_stream_identity"
    ),
)

sleep_session = Table(
    "sleep_session",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("local_date", String, nullable=False),
    Column("start_time_utc", DateTime(), nullable=False),
    Column("end_time_utc", DateTime(), nullable=False),
    Column("total_sleep_s", Float, nullable=True),
    Column("sleep_score", Float, nullable=True),
    Column("source", String, nullable=False),
    Column("raw_object_id", Integer, ForeignKey("raw_object.id"), nullable=True),
    UniqueConstraint("athlete_id", "local_date", "source", name="uq_sleep_session_identity"),
)

sleep_stage = Table(
    "sleep_stage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("sleep_session_id", Integer, ForeignKey("sleep_session.id"), nullable=False),
    Column("stage", String, nullable=False),  # light | deep | rem | awake
    Column("start_time_utc", DateTime(), nullable=False),
    Column("end_time_utc", DateTime(), nullable=False),
)

# --- Registries (exempt from athlete scoping — shared catalogs, not personal data) ---

metric_definition = Table(
    "metric_definition",
    metadata,
    Column("metric_key", String, primary_key=True),
    Column("display_name", String, nullable=False),
    Column("unit_si", String, nullable=True),
    Column("category", String, nullable=False),  # activity | health | device | unknown
    Column("value_type", String, nullable=False),  # numeric | text | boolean
    Column("chart_hints", Text, nullable=True),  # JSON
    Column("first_seen_at", DateTime(), nullable=False),
    Column("first_seen_source", String, nullable=False),
    Column("is_promoted", Boolean, nullable=False, default=False),
)

# --- Ops -------------------------------------------------------------------------

ingest_run = Table(
    "ingest_run",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("source", String, nullable=False),
    Column("started_at", DateTime(), nullable=False),
    Column("finished_at", DateTime(), nullable=True),
    Column("status", String, nullable=False),  # running | success | failed
    Column("watermark_from", DateTime(), nullable=True),
    Column("watermark_to", DateTime(), nullable=True),
    Column("items_seen", Integer, nullable=False, default=0),
    Column("items_new", Integer, nullable=False, default=0),
    Column("errors", Text, nullable=True),  # JSON
    Index("ix_ingest_run_athlete_source", "athlete_id", "source"),
)

merge_decision = Table(
    "merge_decision",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("matched_activity_id", String(26), ForeignKey("activity.id"), nullable=True),
    Column("candidate_ref", String, nullable=False),
    Column("decision", String, nullable=False),  # matched | new
    Column("reasons", Text, nullable=False),  # JSON list[str]
    Column("inputs", Text, nullable=False),  # JSON
    Column("decided_at", DateTime(), nullable=False),
)

# --- Athlete-scoping bookkeeping, enforced by tests/db/test_schema.py -----------

#: Tables that intentionally do NOT carry athlete_id because they are shared catalogs, not an
#: individual athlete's data. Any table not in this set and not carrying athlete_id is a bug.
EXEMPT_FROM_ATHLETE_SCOPING = frozenset({"athlete", "metric_definition"})
