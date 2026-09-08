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
    # Garmin export" health signal (spec: nag past 90 days; see perseverer/staleness.py).
    Column("last_full_export_at", DateTime(), nullable=True),
    # Phase 5: per-athlete credentials (auth/passwords.py, auth/tokens.py). Nullable — an
    # athlete may have neither, either, or both a password login and a standing API key. Never
    # backfilled; existing athletes simply have no credentials until `sync athlete
    # set-password`/`create-key` is run. See docs/adr/0008-phase-5-frontend.md.
    Column("username", String, nullable=True, unique=True),
    Column("password_hash", String, nullable=True),
    Column("api_key_hash", String, nullable=True),
    Column("api_key_created_at", DateTime(), nullable=True),
    # A public, unauthenticated iCalendar feed of this athlete's own planned_workout calendar
    # (calendar_feed.py) -- one standing secret per athlete, same replace-on-rotate shape as
    # api_key_hash above, not a growing history like share_link. NULL means "not published".
    Column("calendar_feed_token_hash", String, nullable=True),
    Column("calendar_feed_created_at", DateTime(), nullable=True),
    # Optional profile facts, settable via GET/PUT /settings/profile -- used only as inputs to
    # formula-based FALLBACKS when there isn't enough empirical data yet (max HR via
    # performance_rollup.py, BMR via api/routers/health.py::get_health_dashboard), never to
    # override real observed/device data. birthdate is an ISO date string ("YYYY-MM-DD"), matching
    # the local_date convention used everywhere else in this schema rather than a native Date
    # type. sex is "male"|"female" (validated at the API layer, not here) -- the Mifflin-St Jeor
    # BMR formula's own binary constant, not a general demographic field.
    Column("birthdate", String, nullable=True),
    Column("height_cm", Float, nullable=True),
    Column("sex", String, nullable=True),
    # Inert for now -- stored for a future feature, no consumer reads it yet. Validated at the API
    # layer (a light regex, not a full RFC 5322 parse) since it isn't used for anything yet.
    Column("email", String, nullable=True),
)

# An athlete's own configured HR training zones -- independent of the per-activity, device-
# reported zones a watch bakes into its own FIT time_in_zone_mesgs (see fit/parser.py::
# _time_in_zone_metrics). One row per athlete (upsert, not a growing history): three reference
# points, not the four zone boundaries directly -- the boundaries are *derived* from these (see
# hr_zones.py::compute_hr_zone_boundaries), a blended model the athlete chose over a %max-HR-only
# or %threshold-only scheme (lower zones relative to heart rate reserve/Karvonen, upper zones
# relative to lactate threshold). All-null means "not configured yet"; the frontend falls back
# to the device-reported zones for that athlete until all three are set.
athlete_hr_zone_config = Table(
    "athlete_hr_zone_config",
    metadata,
    Column("athlete_id", String, ForeignKey("athlete.id"), primary_key=True),
    Column("max_hr_bpm", Float, nullable=True),
    Column("threshold_hr_bpm", Float, nullable=True),
    Column("resting_hr_bpm", Float, nullable=True),
    Column("updated_at", DateTime(), nullable=False),
)

# An athlete's own configured running threshold pace -- the one calibration constant
# `running_load.py::compute_running_tss` needs to turn grade-adjusted pace into a Coggan-style
# rTSS (100 = one hour at threshold pace), the same convention TrainingPeaks/intervals.icu use.
# One row per athlete (upsert, not history), null means "not configured yet" -- `refresh_
# running_tss` is then a no-op and `fitness.py` keeps using Garmin's own `training_load_peak`
# for every activity, exactly like before this table existed. Mirrors athlete_hr_zone_config's
# own shape/contract deliberately, for the same reason (one small, athlete-tunable constant).
athlete_running_load_config = Table(
    "athlete_running_load_config",
    metadata,
    Column("athlete_id", String, ForeignKey("athlete.id"), primary_key=True),
    Column("threshold_pace_sec_per_km", Float, nullable=True),
    Column("updated_at", DateTime(), nullable=False),
)

# An athlete's own Eufy Life scale credentials -- one row per athlete (upsert, same shape as
# athlete_hr_zone_config above), replacing the original single global Settings.eufy_* env-var
# fields now that a second athlete can have their own separate Eufy account/scale. Plaintext
# storage matches the pre-existing env-var precedent: Eufy's API needs the raw password (it's a
# reversible vendor credential, not a local login this app itself authenticates against, unlike
# athlete.password_hash). device_id/customer_id nullable -- see adapters/eufy.py::sync_eufy,
# which already skips gracefully when any of the four fields is missing. See
# adapters/eufy.py::resolve_eufy_credentials for how a missing row here still falls back to the
# legacy global env vars for the original athlete, so that existing setup needs no migration.
athlete_eufy_config = Table(
    "athlete_eufy_config",
    metadata,
    Column("athlete_id", String, ForeignKey("athlete.id"), primary_key=True),
    Column("email", String, nullable=True),
    Column("password", String, nullable=True),
    Column("device_id", String, nullable=True),
    Column("customer_id", String, nullable=True),
    Column("updated_at", DateTime(), nullable=False),
)

# A distance goal for a whole calendar year or month, one per (athlete, period_type,
# period_start) -- not a growing history of past goals, just "what's the target for this
# period", upserted like athlete_hr_zone_config above. `sport=NULL` means every sport combined;
# a specific sport (e.g. "running") scopes progress to just that sport's own activities.
# `period_start` is "YYYY" for a year goal, "YYYY-MM" for a month goal -- deliberately not a
# real DATE column, since a year has no single calendar date of its own. Progress itself is
# computed on read (goals.py), not stored here or in a rollup: a goal is looked up once per
# page view (its own popup, not inline on every calendar page), and its underlying query is
# already bounded to one year/month of activities, not the whole history.
goal = Table(
    "goal",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("period_type", String, nullable=False),  # "year" | "month"
    Column("period_start", String, nullable=False),
    Column("sport", String, nullable=True),
    Column("target_distance_m", Float, nullable=False),
    Column("created_at", DateTime(), nullable=False),
    Column("updated_at", DateTime(), nullable=False),
    UniqueConstraint("athlete_id", "period_type", "period_start", name="uq_goal_identity"),
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
    # True/False when Garmin Connect's own eventTypeId is known for this activity (via
    # garmin_activity_summary.py's summarizedActivities correction -- eventTypeId 1 confirmed
    # against real named races in this athlete's own export, e.g. "Bay to Breakers", "Golden
    # Gate Half Marathon"); NULL when no such correction has ever matched this activity (no
    # garmin_export archive, or nothing at this timestamp), meaning genuinely unknown rather
    # than "confirmed not a race".
    Column("is_race", Boolean, nullable=True),
    Column("description", Text, nullable=True),
    # Athlete-entered fueling intake for this activity -- there's no vendor source for this at
    # all (not FIT, not the Garmin Connect API; confirmed by introspecting both before adding
    # these), so unlike every other column here this is never derived from raw bytes. Durably
    # recorded in activity_sport_override below and reapplied by apply_sport_overrides, same
    # mechanism as the athlete's own sport/race/name corrections.
    Column("carbohydrates_g", Float, nullable=True),
    Column("sodium_mg", Float, nullable=True),
    Column("duration_s", Float, nullable=True),
    Column("moving_duration_s", Float, nullable=True),
    Column("distance_m", Float, nullable=True),
    Column("elevation_gain_m", Float, nullable=True),
    # Peak altitude reached, not cumulative ascent (elevation_gain_m above) -- a hike with modest
    # total climb can still summit a very high point starting from an already-high trailhead, and
    # vice versa. Computed once at parse time from the activity's own altitude stream (no FIT
    # session field carries this directly for this athlete's real files -- confirmed by
    # introspecting the live archive, no fit.session.max_altitude/enhanced_max_altitude metric_key
    # exists at all), same shape as elevation_gain_m. Recomputed by a trim, restored on undo.
    Column("max_altitude_m", Float, nullable=True),
    Column("calories", Float, nullable=True),
    Column("device_id", Integer, ForeignKey("device.id"), nullable=True),
    Column("primary_source", String, nullable=False),
    Column("created_at", DateTime(), nullable=False),
    Column("updated_at", DateTime(), nullable=False),
    Column("deleted_at", DateTime(), nullable=True),
    Index("ix_activity_athlete_start", "athlete_id", "start_time_utc"),
    Index("ix_activity_athlete_local_date", "athlete_id", "local_date"),
)

# An athlete's own after-the-fact correction -- originally just "this sport is wrong" (see
# perseverer/sport_override.py), broadened to also carry independent optional `is_race` and
# `name` corrections. `is_race`: the automatic eventTypeId-based heuristic
# (garmin_activity_summary.py::GARMIN_RACE_EVENT_TYPE_ID) only reflects whether the athlete
# flagged the activity as a race *inside Garmin Connect itself*, so a genuine race the athlete
# forgot to flag there has no other signal to derive it from. `name`: Garmin Connect's own name
# is *not* reliably a real custom title -- confirmed the hard way (see set_sport_override's own
# history): it can equally be a location+activity-type auto-template with zero more information
# than the FIT on-device default ("Santa Clara Other" for hundreds of unrelated activities), so
# there's no safe automatic rule for "trust Garmin's name here" -- only the athlete looking at
# one specific activity can tell a real title ("Santa Clara - Race Pace Run") from a boring
# template. `carbohydrates_g`/`sodium_mg`: fueling intake during the activity has no vendor
# source at all (not FIT, not the Garmin Connect API), so this is the durable record of that
# input, not a correction of anything derived. `sport`/`sub_sport`, `is_race`, `name`, and
# `carbohydrates_g`/`sodium_mg` are independently nullable -- a row may carry any subset of these
# corrections, and only the columns actually supplied by a given correction call are touched, per
# column, on every apply. Deliberately *not* one of
# rebuild.py's `_REBUILDABLE_TABLES`. `activity.id` itself is a fresh ULID minted on every `sync
# rebuild` (the row is deleted and re-inserted from the raw archive), so it can't be this
# table's key -- `start_time_utc` is the one value a rebuild reliably reproduces identically for
# the same physical activity, since it comes straight out of the archived FIT/GPX/TCX bytes.
# Applied as a read-time correction by `apply_sport_overrides`, called at the end of every `sync
# rebuild` (and immediately, once, when a correction is first set) -- never the raw archive
# itself, so "raw first" holds: this table, not a mutated FIT byte, is the durable record of the
# correction.
activity_sport_override = Table(
    "activity_sport_override",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("start_time_utc", DateTime(), nullable=False),
    Column("sport", String, nullable=True),
    Column("sub_sport", String, nullable=True),
    Column("is_race", Boolean, nullable=True),
    Column("name", String, nullable=True),
    Column("carbohydrates_g", Float, nullable=True),
    Column("sodium_mg", Float, nullable=True),
    Column("created_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id", "start_time_utc", name="uq_activity_sport_override_identity"
    ),
)

# Same "durable, never wiped by sync rebuild" shape as activity_sport_override above -- see
# bouldering_overrides.py's own docstring. Keyed by (athlete_id, activity_start_time_utc,
# split_index), not activity_id: split rows (like activity rows) are wiped and re-derived with a
# fresh identity on every rebuild, but a FIT-derived split's own split_index (its position in
# file order) is stable across replays of the same archived bytes.
bouldering_route_status_override = Table(
    "bouldering_route_status_override",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("activity_start_time_utc", DateTime(), nullable=False),
    Column("split_index", Integer, nullable=False),
    # Both nullable, set independently -- a correction row may fix just the status, just the
    # grade, or both, same "only the columns actually recorded are written" contract as
    # sport_override.py's activity_sport_override. See bouldering_overrides.py's own docstring.
    Column("result", String, nullable=True),
    Column("grade", Integer, nullable=True),
    Column("created_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id",
        "activity_start_time_utc",
        "split_index",
        name="uq_bouldering_route_status_override_identity",
    ),
)

# A route the athlete logged by hand (the device never recorded it at all) -- has no FIT-derived
# split_index to key off of, so it gets its own independent, athlete-assigned ordering
# (manual_order) instead. Also durable/never wiped -- re-applied after every rebuild by
# re-appending each manual route's own live `split` row in manual_order sequence.
bouldering_manual_route = Table(
    "bouldering_manual_route",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("activity_start_time_utc", DateTime(), nullable=False),
    Column("manual_order", Integer, nullable=False),
    Column("grade", Integer, nullable=False),
    Column("result", String, nullable=False),
    # The live split row's own current split_index, kept in sync by bouldering_overrides.py
    # every time it (re-)inserts that row -- once at creation, again after every rebuild. This is
    # what lets a delete find its live row directly (a plain WHERE, not a positional guess),
    # even though the live split_index itself isn't stable across a rebuild the way manual_order
    # (this row's own real identity) is.
    Column("split_index", Integer, nullable=False),
    Column("created_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id",
        "activity_start_time_utc",
        "manual_order",
        name="uq_bouldering_manual_route_identity",
    ),
)

# The athlete's own correction for a recording that includes a stretch of car travel it never
# should have (forgot to stop tracking before/after driving to/from a hike) -- see
# activity_trim.py's own docstring for why this can't be a destructive edit to the Parquet stream
# or a bare UPDATE on `activity`: `sync rebuild` re-derives `activity`/`lap`/`route_geom` from raw
# bytes on every run, so this durable record (never wiped -- see rebuild.py's
# `_REBUILDABLE_TABLES`) is what makes the correction survive, re-applied by
# apply_activity_trim_overrides. Offsets are elapsed seconds from the activity's own recorded
# start -- stable across a rebuild the same way activity_start_time_utc itself is, since both are
# parsed from the same archived bytes every time. Either offset may be null (trim only one side).
activity_trim_override = Table(
    "activity_trim_override",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("activity_start_time_utc", DateTime(), nullable=False),
    Column("trim_start_s", Float, nullable=True),
    Column("trim_end_s", Float, nullable=True),
    Column("created_at", DateTime(), nullable=False),
    Column("updated_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id", "activity_start_time_utc", name="uq_activity_trim_override_identity"
    ),
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

# The athlete's own manual "these two activities are the same, use this side's data for each
# field" correction -- see activity_merge.py's own docstring for the full mechanism. Durable
# (never wiped -- see rebuild.py's `_REBUILDABLE_TABLES`) for the same reason every other
# override table here is: `sync rebuild` re-derives `activity`/`activity_source_link` from raw
# bytes on every run, re-splitting the two activities right back apart if this correction
# weren't reapplied afterward.
#
# Keyed by (source, external_id) pairs, not activity_id or start_time_utc -- activity_id is a
# fresh ULID every rebuild, and two activities being merged share the same start_time_utc *by
# definition* (that's why they're duplicates), so it alone can't tell which of two identically-
# timestamped post-rebuild rows is "keep" and which is "absorbed". One row per absorbed *source
# link* (not per absorbed activity) -- an activity being merged in may itself already carry
# multiple source links (e.g. absorbing an already-multi-source-merged activity), and each needs
# its own independently-reapplicable redirect record.
activity_merge_override = Table(
    "activity_merge_override",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("keep_source", String, nullable=False),
    Column("keep_external_id", String, nullable=False),
    Column("absorbed_source", String, nullable=False),
    Column("absorbed_external_id", String, nullable=False),
    # JSON object of only the fields chosen "other" (absorbed) -- everything else stays "self"
    # (keep), same "only what's recorded is written" contract as sport_override.py.
    Column("field_choices", Text, nullable=False),
    Column("created_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id",
        "absorbed_source",
        "absorbed_external_id",
        name="uq_activity_merge_override_absorbed_identity",
    ),
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
    # FIT's own `total_timer_time` -- excludes a device pause within the lap, the same way
    # activity.moving_duration_s excludes it for the whole activity. See ParsedLap's own
    # docstring for the real-data confirmation of why duration_s alone isn't enough.
    Column("moving_duration_s", Float, nullable=True),
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
    # Bouldering only, only ever populated on a "climb_active" split_type row -- see
    # fit/parser.py's own comment for how these two undocumented FIT split fields were reverse-
    # engineered (confirmed absent from the installed garmin_fit_sdk's own profile for the split
    # message). climb_grade is the V-scale number (V0, V1, ...) already de-offset from the raw
    # FIT encoding (grade+1); climb_result is "attempt"/"completed" for the two confirmed raw
    # values (2/3), or "unknown_<n>" for any other raw value so a value this project hasn't seen
    # yet is never silently discarded.
    Column("climb_grade", Integer, nullable=True),
    Column("climb_result", String, nullable=True),
    # Also bouldering-only, but populated on both "climb_active" and "climb_rest" splits (unlike
    # grade/result, which only mean something for the climb itself) -- confirmed against real
    # data: field 15 <= field 16 held on all 55 real splits across two files, and both fall in a
    # plausible bpm range against the athlete's own recorded resting HR. See fit/parser.py.
    Column("climb_avg_hr", Float, nullable=True),
    Column("climb_max_hr", Float, nullable=True),
    # True only for a row bouldering_overrides.py::add_manual_route created (the athlete logging
    # a route the device never recorded at all) -- never set (stays NULL, read as "not manual")
    # for a FIT-derived row. Nullable rather than NOT NULL + a default so adding this column to
    # an already-populated table is a plain ALTER TABLE, no backfill required. This is what lets
    # the frontend offer a delete affordance only where it's actually safe: deleting a real
    # FIT-derived split would just come back on the next ingest/rebuild anyway, but a manual row
    # has no such backing and really would be gone for good.
    Column("is_manual", Boolean, nullable=True),
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

# The pre-planned workout structure downloaded to the device before the activity (Garmin
# Connect's own "Workout" builder), recorded verbatim into the FIT file's own workout_mesgs/
# workout_step_mesgs -- see fit/parser.py::_parse_workout's own docstring for the real-data
# confirmation and the "rows[0]-only" bug this fixes. One-to-one with `activity` (an activity
# either has a recorded plan or doesn't), so keyed directly on activity_id like route_geom.
activity_workout = Table(
    "activity_workout",
    metadata,
    Column("activity_id", String(26), ForeignKey("activity.id"), primary_key=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("name", String, nullable=True),
    Column("description", Text, nullable=True),
)

# One row per planned step, in FIT's own message_index order -- unexpanded (a
# "repeat_until_steps_cmplt" step is itself a row describing "repeat steps
# [repeat_from_step..step_index-1] repeat_count times", not pre-flattened into repeated rows).
# Callers that want the executed sequence (e.g. to align with recorded laps) expand this
# themselves, per "raw first" -- see fit/parser.py::ParsedWorkoutStep's own docstring.
activity_workout_step = Table(
    "activity_workout_step",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("activity_id", String(26), ForeignKey("activity.id"), nullable=False),
    Column("step_index", Integer, nullable=False),
    Column("duration_type", String, nullable=True),
    Column("duration_time_s", Float, nullable=True),
    Column("duration_distance_m", Float, nullable=True),
    Column("target_type", String, nullable=True),
    # Only populated for target_type == "speed" -- see ParsedWorkoutStep's own docstring.
    Column("target_low_mps", Float, nullable=True),
    Column("target_high_mps", Float, nullable=True),
    Column("intensity", String, nullable=True),
    Column("repeat_from_step", Integer, nullable=True),
    Column("repeat_count", Integer, nullable=True),
    UniqueConstraint(
        "athlete_id", "activity_id", "step_index", name="uq_activity_workout_step_identity"
    ),
)

# A *future*, athlete-authored workout scheduled on the calendar (Phase 10-ish, "scheduled
# workouts") -- NOT the same table as activity_workout/activity_workout_step above, even though
# the column shape (duration/target/repeat) deliberately mirrors it: those two are retrospective,
# keyed 1:1 on a completed activity_id, parsed out of a device's own recorded FIT workout_mesgs;
# these are prospective, keyed on a future local_date, authored by the athlete as free text
# (workout_syntax.py) and pushed to a Garmin watch as a real structured workout -- a push
# lifecycle activity_workout has no concept of. Any number of rows per (athlete_id, local_date) --
# originally capped at one via a UniqueConstraint (v1), lifted once the athlete asked to schedule
# more than one workout on the same day (e.g. a morning run plus an evening strength session);
# every row is independently addressed by its own id (api/routers/planned_workouts.py), never by
# date alone. Garmin's own schedule_workout() is itself date-granular with no further concept of
# ordering within a day -- Perseverer's own scheduled_time is what the UI sorts multiple same-day
# workouts by. sport is an open string, not an enum, so a 5th sport later (see workout_syntax.py's
# own docstring for the "running first" scoping) needs a data-only addition, matching the
# project's own additive-schema-evolution principle.
planned_workout = Table(
    "planned_workout",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("local_date", String, nullable=False),
    Column("sport", String, nullable=False),  # "running" | "yoga" | "bouldering" | "hiit" |
    # "strength_training" -- open string, not an enum. "fitness" was dropped from the frontend's
    # own sport list (no structured syntax and no placeholder builder ever existed for it).
    Column("name", String, nullable=True),
    # The athlete's own typed text, kept verbatim -- raw-first-adjacent: never lose what they
    # actually authored even if the parser or step model changes later. Re-parsed into
    # planned_workout_step on every save, not just the first, for sport=="running" -- for
    # yoga/bouldering (no structured syntax, see planned_workouts.py::save_planned_workout) this
    # is just freeform athlete notes, never parsed at all.
    Column("source_text", Text, nullable=True),
    # For running: derived from parsing source_text. For yoga/bouldering: set directly from the
    # athlete's own duration input (no syntax to parse a duration out of) -- see
    # save_planned_workout's own docstring.
    Column("estimated_duration_s", Float, nullable=True),
    # "HH:MM", 24h, local time -- Perseverer's own calendar display metadata only. Garmin's
    # schedule_workout() is date-only with no time-of-day API at all (confirmed directly against
    # the installed garminconnect package, see docs/adr/0015-scheduled-workouts.md's own "vendor
    # facts" section), so this can never make the watch itself prompt at this clock time -- it
    # only ever appears in Perseverer's own UI.
    Column("scheduled_time", String, nullable=True),
    Column("garmin_workout_id", Integer, nullable=True),
    Column("garmin_scheduled_at", DateTime(), nullable=True),
    Column("push_status", String, nullable=False),  # "draft" | "pushed" | "push_failed"
    Column("push_error", Text, nullable=True),
    Column("created_at", DateTime(), nullable=False),
    Column("updated_at", DateTime(), nullable=False),
)

# One row per planned step, in workout_syntax.py's own parse order -- unexpanded, same
# "repeat block is itself one row describing [repeat_from_step..step_index-1] x repeat_count"
# convention as activity_workout_step (see its own docstring), reused deliberately so the
# frontend's existing expandWorkoutSteps/groupWorkoutStepsForDisplay (workoutSteps.ts) work
# unmodified against either table's rows.
planned_workout_step = Table(
    "planned_workout_step",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("planned_workout_id", Integer, ForeignKey("planned_workout.id"), nullable=False),
    Column("step_index", Integer, nullable=False),
    # "time" | "distance" | "reps" | "repeat_until_steps_cmplt"
    Column("duration_type", String, nullable=True),
    Column("duration_time_s", Float, nullable=True),
    Column("duration_distance_m", Float, nullable=True),
    # hiit/strength_training only ("reps" duration_type) -- a rep-counted set, e.g. "10 reps of
    # bench press". Running never uses this (its own repeat counts live in repeat_count below,
    # a different concept: "do this whole block N times", not "do this one step N reps").
    Column("duration_reps", Integer, nullable=True),
    Column("target_type", String, nullable=True),  # "pace" | "heart_rate" | None (open/no target)
    # Unit depends on target_type: m/s for "pace", bpm for "heart_rate" -- same convention as
    # activity_workout_step.target_low_mps/target_high_mps, just widened to cover HR too since a
    # planned step (unlike a recorded one) can target either.
    Column("target_low", Float, nullable=True),
    Column("target_high", Float, nullable=True),
    # Alternative to target_low/high for an HR target expressed as "Z2 HR" rather than an
    # absolute bpm range -- resolved against the athlete's own athlete_hr_zone_config at push
    # time (hr_zones.py::compute_hr_zone_boundaries), not a hardcoded %-of-max scheme.
    Column("target_hr_zone", Integer, nullable=True),
    Column("cadence_low", Integer, nullable=True),
    Column("cadence_high", Integer, nullable=True),
    Column("intensity", String, nullable=True),  # warmup|active|recovery|cooldown|rest -- same
    # vocabulary activity_workout_step.intensity already uses.
    Column("repeat_from_step", Integer, nullable=True),
    Column("repeat_count", Integer, nullable=True),
    # hiit/strength_training only -- a specific Garmin exercise (garminconnect.exercises'
    # (category, exercise) enum pair, e.g. ("BENCH_PRESS", "") or ("CURL", "HAMMER_CURL")).
    # exercise_name is "" (not null) when the step is just the category with no specific variant
    # -- matches Garmin's own convention (see garminconnect.exercises' own docstring) so an empty
    # exercise_name round-trips identically rather than colliding with "no exercise at all".
    Column("exercise_category", String, nullable=True),
    Column("exercise_name", String, nullable=True),
    Column("weight_kg", Float, nullable=True),
    # A freeform note attached to this specific step -- for running, parsed from an inline
    # trailing "# comment" token on that step's own source_text line (workout_syntax.py); for
    # hiit/strength_training, typed directly against that exercise/rest row
    # (ExerciseStepEditor.tsx). Never parsed further, never sport-gated here, never sent to Garmin.
    Column("comment", Text, nullable=True),
    UniqueConstraint(
        "athlete_id",
        "planned_workout_id",
        "step_index",
        name="uq_planned_workout_step_identity",
    ),
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

# Independently-computed race-time predictions + max HR/threshold pace/threshold HR -- see
# performance_rollup.py::refresh_performance_rollup for the full model (a Daniels-Gilbert VDOT
# extension, not Garmin's own daily_race_predictions/daily_lactate_threshold, which stay shown
# separately and are never reconciled against this, same posture fitness_daily_rollup above
# already takes with Garmin's own Training Readiness). Whole-athlete-history grain, full
# recompute on every relevant ingest run, same precedent as fitness_daily_rollup.
performance_daily_rollup = Table(
    "performance_daily_rollup",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("local_date", String, nullable=False),
    # The 42-day rolling-max VDOT that every derived column below is computed from -- stored
    # alongside them for the same debuggability reason fitness_daily_rollup stores training_load.
    Column("rolling_vdot", Float, nullable=True),
    Column("max_hr_bpm", Float, nullable=True),
    # "empirical" (365-day trailing max of real observed max HR) | "formula_fallback" (Tanaka,
    # 208 - 0.7*age, used only when there's no empirical value yet and the athlete has a
    # configured birthdate) | null (neither is possible yet) -- same provenance-tagging pattern as
    # threshold_hr_source below, deliberately not overriding real data once it exists (see
    # athlete_age.py / this module's own docstring for the fallback's exact scope).
    Column("max_hr_source", String, nullable=True),
    Column("threshold_pace_s_per_km", Float, nullable=True),
    Column("threshold_hr_bpm", Float, nullable=True),
    # "empirical" (derived from real runs near threshold pace) | "fallback" (% of max_hr_bpm) |
    # null (no threshold_hr_bpm at all yet) -- so a chart/tooltip can show which path produced it.
    Column("threshold_hr_source", String, nullable=True),
    Column("predicted_5k_s", Float, nullable=True),
    Column("predicted_10k_s", Float, nullable=True),
    Column("predicted_half_marathon_s", Float, nullable=True),
    Column("predicted_marathon_s", Float, nullable=True),
    Column("refreshed_at", DateTime(), nullable=False),
    UniqueConstraint("athlete_id", "local_date", name="uq_performance_daily_rollup_identity"),
)

# --- Insights (Phase 8): a rules-based, deterministic derived table -- see
# src/perseverer/insights/ and docs/adr/0012-phase-8-strava-merge-insights.md. Full
# delete-and-reinsert per athlete per refresh (same justified precedent as fitness_daily_rollup's
# full CTL/ATL/TSB recompute above: cheap at this data volume, avoids stale rows lingering).
# Refreshed both on ingest (like every other rollup here) and once daily by the worker's own
# APScheduler job, since a window like "last 30 days" shifts every day even with zero new
# ingests -- the one rollup in this codebase that isn't purely ingest-triggered.

insight = Table(
    "insight",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("kind", String, nullable=False),  # effort | streak | pb | window_best | load | health
    Column("window", String, nullable=False),  # e.g. "30d", "90d", "180d", "year", "365d"
    # A short deterministic string each rule builds identifying what this row is about within
    # its (kind, window) -- e.g. "distance:high:run", "streak:current" -- what makes a refresh a
    # clean delete-and-reinsert rather than an ever-growing table of one-off historical rows.
    Column("subject_key", String, nullable=False),
    Column("metric_key", String, nullable=True),
    Column("sport_family", String, nullable=True),
    Column("activity_id", String(26), ForeignKey("activity.id"), nullable=True),
    Column("local_date", String, nullable=True),
    Column("title", String, nullable=False),
    Column("detail", Text, nullable=False),  # JSON
    Column("value_num", Float, nullable=True),
    Column("computed_at", DateTime(), nullable=False),
    UniqueConstraint(
        "athlete_id", "kind", "window", "subject_key", name="uq_insight_identity"
    ),
    Index("ix_insight_athlete_kind_window", "athlete_id", "kind", "window"),
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
    # An activity id when entity_type="activity", an ISO local_date when entity_type="day".
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

# --- Share links (Phase 10): an athlete-issued token granting unauthenticated, read-only
# access to one activity or one summary period -- see sharing.py and
# api/routers/share.py. Only viable now that activity.id is deterministic (see
# fit_folder.py::_derive_activity_id) -- a link into a random-ULID id would go dead on the
# next rebuild, defeating the point of a link meant to be pasted somewhere durable. Follows
# auth/api_keys.py's own convention exactly: only a sha256 of the token is ever stored, never
# the plaintext, even though a share link isn't a login credential. -----------------------

share_link = Table(
    "share_link",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", String, ForeignKey("athlete.id"), nullable=False),
    Column("token_hash", String, nullable=False),
    # "activity" -> target_id is an activity.id. "period" -> target_id is
    # "<period_type>:<period_start>" (period_type one of week|month|year|all; period_start
    # e.g. "2026-06" or "2026", omitted -- just "all" -- for period_type="all").
    Column("target_type", String, nullable=False),
    Column("target_id", String, nullable=False),
    Column("created_at", DateTime(), nullable=False),
    # Nullable-means-"hasn't happened" -- same idiom as activity.deleted_at, not a separate
    # boolean flag.
    Column("revoked_at", DateTime(), nullable=True),
    UniqueConstraint("token_hash", name="uq_share_link_token_hash"),
    Index("ix_share_link_athlete", "athlete_id"),
)

# --- Login brute-force lockout (Phase 9 hardening, ADR 0014) -- see auth/lockout.py ------

auth_login_attempt = Table(
    "auth_login_attempt",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Deliberately no athlete_id / FK to athlete: a login attempt against a nonexistent
    # username must still be counted (that's exactly the case a brute-force attempt usually
    # is), and there's no athlete row to attach it to until/unless the username is real -- see
    # EXEMPT_FROM_ATHLETE_SCOPING below.
    Column("username", String, nullable=False),
    Column("attempted_at", DateTime(), nullable=False),
    Column("success", Boolean, nullable=False),
    Index("ix_auth_login_attempt_username_time", "username", "attempted_at"),
)

# --- Athlete-scoping bookkeeping, enforced by tests/db/test_schema.py -----------

#: Tables that intentionally do NOT carry athlete_id because they are shared catalogs, not an
#: individual athlete's data. Any table not in this set and not carrying athlete_id is a bug.
EXEMPT_FROM_ATHLETE_SCOPING = frozenset(
    {"athlete", "metric_definition", "auth_login_attempt"}
)
