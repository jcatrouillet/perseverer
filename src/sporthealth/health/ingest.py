"""Upserts a `HealthBatch` into `health_observation`/`health_stream`/`sleep_session`/
`sleep_stage`. The health-domain counterpart to `adapters.fit_folder.ingest_canonical_batch`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Connection, select

from sporthealth.db.schema import health_observation, health_stream, sleep_session, sleep_stage
from sporthealth.health.types import HealthBatch, HealthStreamPoint
from sporthealth.metrics.registry import get_or_register_metric
from sporthealth.streams import write_health_stream


@dataclass
class HealthIngestResult:
    observations_seen: int = 0
    observations_new: int = 0
    stream_points_seen: int = 0
    sleep_sessions_seen: int = 0
    sleep_sessions_new: int = 0
    # Distinct local_dates that gained a new observation/sleep session -- what callers
    # accumulate across an ingest run and feed to rollups.refresh_daily_rollup once each. See
    # docs/adr/0006-phase-3-read-api-and-rollups.md decision 3.
    affected_local_dates: set[str] = field(default_factory=set)


def ingest_health_batch(
    conn: Connection,
    parquet_dir: Path,
    *,
    athlete_id: str,
    source: str,
    batch: HealthBatch,
) -> HealthIngestResult:
    result = HealthIngestResult()

    for key in batch.unrecognized_field_keys:
        get_or_register_metric(conn, metric_key=key, source=source, category="unknown")

    for obs in batch.observations:
        result.observations_seen += 1
        get_or_register_metric(
            conn,
            metric_key=obs.metric_key,
            source=source,
            category="health",
            value_type="numeric" if obs.value_num is not None else "text",
            unit_si=obs.unit,
        )
        existing = conn.execute(
            select(health_observation.c.id).where(
                health_observation.c.athlete_id == athlete_id,
                health_observation.c.metric_key == obs.metric_key,
                health_observation.c.observed_at_utc == obs.observed_at_utc,
                health_observation.c.source == source,
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        conn.execute(
            health_observation.insert().values(
                athlete_id=athlete_id,
                metric_key=obs.metric_key,
                observed_at_utc=obs.observed_at_utc,
                local_date=obs.local_date,
                interval_start=obs.interval_start,
                interval_end=obs.interval_end,
                aggregation=obs.aggregation,
                value_num=obs.value_num,
                value_text=obs.value_text,
                unit=obs.unit,
                source=source,
            )
        )
        result.observations_new += 1
        result.affected_local_dates.add(obs.local_date)

    grouped: dict[tuple[str, str], list[HealthStreamPoint]] = defaultdict(list)
    for point in batch.stream_points:
        result.stream_points_seen += 1
        year_month = point.timestamp_utc.strftime("%Y-%m")
        grouped[(point.metric_key, year_month)].append(point)

    for (metric_key, year_month), points in grouped.items():
        get_or_register_metric(conn, metric_key=metric_key, source=source, category="health")
        relative_path, n_samples = write_health_stream(
            parquet_dir, athlete_id, metric_key, year_month, points
        )
        existing_stream_id = conn.execute(
            select(health_stream.c.id).where(
                health_stream.c.athlete_id == athlete_id,
                health_stream.c.metric_key == metric_key,
                health_stream.c.year_month == year_month,
                health_stream.c.source == source,
            )
        ).scalar_one_or_none()
        if existing_stream_id is not None:
            conn.execute(
                health_stream.update()
                .where(health_stream.c.id == existing_stream_id)
                .values(parquet_path=relative_path, n_samples=n_samples)
            )
        else:
            conn.execute(
                health_stream.insert().values(
                    athlete_id=athlete_id,
                    metric_key=metric_key,
                    year_month=year_month,
                    parquet_path=relative_path,
                    n_samples=n_samples,
                    source=source,
                )
            )

    for parsed_session in batch.sleep_sessions:
        result.sleep_sessions_seen += 1
        existing_session_id = conn.execute(
            select(sleep_session.c.id).where(
                sleep_session.c.athlete_id == athlete_id,
                sleep_session.c.local_date == parsed_session.local_date,
                sleep_session.c.source == source,
            )
        ).scalar_one_or_none()
        if existing_session_id is not None:
            continue  # idempotent: this night was already ingested for this source

        insert_result = conn.execute(
            sleep_session.insert().values(
                athlete_id=athlete_id,
                local_date=parsed_session.local_date,
                start_time_utc=parsed_session.start_time_utc,
                end_time_utc=parsed_session.end_time_utc,
                total_sleep_s=parsed_session.total_sleep_s,
                sleep_score=parsed_session.sleep_score,
                source=source,
            )
        )
        assert insert_result.inserted_primary_key is not None
        session_id = insert_result.inserted_primary_key[0]
        result.sleep_sessions_new += 1
        result.affected_local_dates.add(parsed_session.local_date)

        for stage in parsed_session.stages:
            conn.execute(
                sleep_stage.insert().values(
                    athlete_id=athlete_id,
                    sleep_session_id=session_id,
                    stage=stage.stage,
                    start_time_utc=stage.start_time_utc,
                    end_time_utc=stage.end_time_utc,
                )
            )

    return result
