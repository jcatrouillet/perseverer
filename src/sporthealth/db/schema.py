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
    # Phase 5: per-athlete credentials (auth/passwords.py, auth/tokens.py). Nullable — an
    # athlete may have neither, either, or both a password login and a standing API key. Never
    # backfilled; existing athletes simply have no credentials until `sync athlete
    # set-password`/`create-key` is run. See docs/adr/0008-phase-5-frontend.md.
    Column("username", String, nullable=True, unique=True),
    Column("password_hash", String, nullable=True),
    Column("api_key_hash", String, nullable=True),
    Column("api_key_created_at", DateTime(), nullable=True),
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
    # ISO date (e.g. "2026-08-02") the activity belongs to in local time -- mirrors
    # health_observation/sleep_session's own local_date column. Populated at ingest time;
    # without it, rollups.refresh_daily_rollup would need UTC-offset arithmetic inline. See
    # docs/adr/0006-phase-3-read-api-and-rollups.md decision 2.
    Column("local_date", String, nullable=True),
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
    Index("ix_activity_athlete_local_date", "athlete_id", "local_date"),
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
    # The uniqueness index above leads with athlete_id, so it can't serve a lookup keyed on
    # activity_id first (e.g. the /activities list endpoint's per-row avg/max heart rate
    # correlated subqueries) without a full scan. This index serves that access pattern.
    Index("ix_activity_metric_activity_key", "activity_id", "metric_key"),
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

# --- Rollups (Phase 3): derived caches, refreshed on ingest via rollups.refresh_daily_rollup,
# never written to directly by adapters. Wiped/recomputed like any other _REBUILDABLE_TABLES
# entry -- "never destructive" doesn't apply to a derived cache. See ADR 0006 decisions 1, 3. --

day_rollup = Table(
    "day_rollup",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("local_date", String, nullable=False),
    Column("activity_count", Integer, nullable=False, default=0),
    Column("activity_duration_s", Float, nullable=True),
    Column("activity_moving_duration_s", Float, nullable=True),
    Column("activity_distance_m", Float, nullable=True),
    Column("activity_elevation_gain_m", Float, nullable=True),
    Column("activity_calories", Float, nullable=True),
    Column("sleep_total_s", Float, nullable=True),
    Column("sleep_score", Float, nullable=True),
    Column("refreshed_at", DateTime(), nullable=False),
    UniqueConstraint("athlete_id", "local_date", name="uq_day_rollup_identity"),
)

health_metric_daily_rollup = Table(
    "health_metric_daily_rollup",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("local_date", String, nullable=False),
    Column("metric_key", String, ForeignKey("metric_definition.metric_key"), nullable=False),
    Column("value_sum", Float, nullable=True),
    Column("value_avg", Float, nullable=True),
    Column("value_min", Float, nullable=True),
    Column("value_max", Float, nullable=True),
    # value_num of the observation with the latest observed_at_utc that day -- what a
    # point-in-time metric (e.g. resting_heart_rate) usually wants, vs. sum/avg for a
    # cumulative one (e.g. steps). The API picks per metric_key; this table stores all five so
    # no per-metric aggregation-method registry is needed here. See ADR 0006 decision 1.
    Column("value_last", Float, nullable=True),
    Column("n_observations", Integer, nullable=False, default=0),
    Column("refreshed_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id", "local_date", "metric_key", name="uq_health_rollup_identity"
    ),
    Index("ix_health_rollup_athlete_date", "athlete_id", "local_date"),
)

# --- Period rollups (Phase 6): the same day_rollup/health_metric_daily_rollup shape, one
# level coarser -- week and month share a `period_type` discriminator column rather than four
# separate tables, since they're the same shape at two grains and a caller almost always wants
# "periods of type X in this range." Computed as a rollup OF day_rollup/
# health_metric_daily_rollup (sum-of-sums), never of raw tables -- see rollups.py::
# refresh_period_rollup and docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.
# period_start/period_end are stored, not derived at query time, since callers (the calendar
# grid) need them to render period boundaries and they're already known at refresh time.

period_rollup = Table(
    "period_rollup",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("period_type", String, nullable=False),  # "week" | "month"
    Column("period_start", String, nullable=False),  # local_date, e.g. Monday for a week
    Column("period_end", String, nullable=False),  # inclusive
    Column("activity_count", Integer, nullable=False, default=0),
    Column("activity_duration_s", Float, nullable=True),
    Column("activity_moving_duration_s", Float, nullable=True),
    Column("activity_distance_m", Float, nullable=True),
    Column("activity_elevation_gain_m", Float, nullable=True),
    Column("activity_calories", Float, nullable=True),
    # Distinct days within the period with >=1 activity -- a standard "X of 7 days active"
    # signal, cheap to add while already computing the row, not present at daily grain (a day
    # is trivially 0 or 1 active by definition there).
    Column("activity_days_count", Integer, nullable=False, default=0),
    Column("sleep_total_s", Float, nullable=True),
    Column("sleep_score", Float, nullable=True),
    Column("refreshed_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id", "period_type", "period_start", name="uq_period_rollup_identity"
    ),
)

health_metric_period_rollup = Table(
    "health_metric_period_rollup",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("period_type", String, nullable=False),
    Column("period_start", String, nullable=False),
    Column("metric_key", String, ForeignKey("metric_definition.metric_key"), nullable=False),
    Column("value_sum", Float, nullable=True),
    # Weighted by each day's n_observations, not a naive average of daily averages -- see
    # refresh_period_rollup.
    Column("value_avg", Float, nullable=True),
    Column("value_min", Float, nullable=True),
    Column("value_max", Float, nullable=True),
    Column("value_last", Float, nullable=True),
    Column("n_observations", Integer, nullable=False, default=0),
    Column("refreshed_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id",
        "period_type",
        "period_start",
        "metric_key",
        name="uq_health_period_rollup_identity",
    ),
    Index("ix_health_period_rollup_athlete_period", "athlete_id", "period_type", "period_start"),
)

# --- Fitness & Form (Phase 6): an independently-computed Banister/Coggan CTL(42d)/ATL(7d)/TSB
# EWMA over daily fit.session.training_load_peak, compared against (not required to match)
# Garmin's own TrainingReadinessDTO/TrainingHistory signals in the frontend -- Garmin's raw
# exports have no CTL/ATL/TSB triplet at all (confirmed). Whole-athlete-history grain, full
# recompute on every relevant ingest run -- see fitness.py::refresh_fitness_rollup and ADR 0009.

fitness_daily_rollup = Table(
    "fitness_daily_rollup",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("local_date", String, nullable=False),
    # The deduplicated daily input that fed the EWMA -- stored alongside the derived values so
    # the model is debuggable without re-deriving it (raw-first/provenance instinct).
    Column("training_load", Float, nullable=False, default=0.0),
    Column("ctl", Float, nullable=False),
    Column("atl", Float, nullable=False),
    Column("tsb", Float, nullable=False),
    Column("refreshed_at", DateTime(), nullable=False),
    UniqueConstraint("athlete_id", "local_date", name="uq_fitness_daily_rollup_identity"),
)

# --- Notes (Phase 3): the write path CLAUDE.md's mission statement calls for -- one
# polymorphic table rather than per-entity note tables, matching the project's existing
# preference for additively-extensible shapes. Scoped to activities/days only for now; a new
# entity_type is a data-only addition, not a schema change. See ADR 0006 decision 7. ---------

note = Table(
    "note",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("entity_type", String, nullable=False),  # "activity" | "day"
    # An activity ULID when entity_type="activity", an ISO local_date when entity_type="day".
    Column("entity_id", String, nullable=False),
    Column("body", Text, nullable=False),
    Column("author", String, nullable=True),  # e.g. "agent", a human's name, or null
    Column("created_at", DateTime(), nullable=False),
    Column("updated_at", DateTime(), nullable=False),
    Index("ix_note_athlete_entity", "athlete_id", "entity_type", "entity_id"),
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
