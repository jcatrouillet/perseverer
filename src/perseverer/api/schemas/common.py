"""Shared response-model plumbing: pagination envelope and the naive-storage -> explicit-UTC
boundary. Internal storage stays naive-implicit-UTC -- that's a storage-layer
decision that must not leak into the API's external JSON contract, which should be
unambiguous ISO-8601. Every response model attaches tzinfo via `to_utc` when built from a row.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel


def to_utc(value: datetime) -> datetime:
    """Naive DB datetime -> explicit UTC-aware datetime, for response models only."""
    return value.replace(tzinfo=UTC)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int
