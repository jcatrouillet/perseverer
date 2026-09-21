"""One interface, many implementations. `fit_folder` is the first (this phase);
`garmin_connect`, `garmin_export`, `strava_export`, `manual` follow in later phases. When a
vendor breaks, the fix stays inside one adapter file — see AGENTS.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from perseverer.fit.types import CanonicalBatch


@dataclass(frozen=True)
class AdapterHealth:
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class ObjectRef:
    """Points at one importable unit — a file path for fit_folder, a URL/id for a future
    API-based adapter."""

    locator: str
    external_id: str | None = None


@dataclass(frozen=True)
class RawPayload:
    content: bytes
    kind: str
    http_status: int | None = None


@runtime_checkable
class SourceAdapter(Protocol):
    name: str

    def health_check(self) -> AdapterHealth: ...
    def authenticate(self) -> None: ...
    def list_changed(self, since: datetime) -> list[ObjectRef]: ...
    def fetch_raw(self, ref: ObjectRef) -> RawPayload: ...
    def parse(self, raw_bytes: bytes) -> CanonicalBatch: ...
