"""DuckDB connection setup for the read API's stream-downsampling endpoint -- the only place
this project uses DuckDB (see docs/adr/0006-phase-3-read-api-and-rollups.md decisions 3-4).

Behavior below was verified directly against the installed `duckdb` package (ATTACH syntax,
`.cursor()` semantics, the timestamp-CAST and extension-network gotchas) rather than recalled
from training data, per AGENTS.md's own standing instruction for fast-moving vendor libraries.
"""

from __future__ import annotations

from pathlib import Path

import duckdb


def make_duckdb_connection(
    db_path: Path, extension_dir: Path | None = None
) -> duckdb.DuckDBPyConnection:
    """One long-lived connection per worker process, with the app's SQLite database attached
    read-only. Verified: this connection sees fresh commits from the separate SQLite writer
    connection (the SQLAlchemy engine ingestion uses) without needing to re-attach.

    `extension_dir`, when set, points at a directory the "sqlite" extension was pre-installed
    into at Docker build time (`api.Dockerfile`) -- production must never fetch it over the
    network at request/startup time, since the NAS container has no reason to have outbound
    internet and images are never built there. Local dev (extension_dir=None) installs it
    on first use instead, which is fine off the NAS.

    `temp_directory` is set explicitly under `db_path`'s own parent (i.e. `/data` in production)
    rather than left at DuckDB's own default (the current working directory) -- Phase 9's
    container hardening (ADR 0014) makes the API container's root filesystem read-only, and
    `/data` is the one bind mount that stays writable, so a query large enough to spill to disk
    needs an explicit writable path or it would otherwise try (and fail) to write under the
    now-read-only `/app`.
    """
    config: dict[str, str | bool | int | float | list[str]] = {
        "temp_directory": str(db_path.parent / "duckdb_tmp"),
    }
    if extension_dir:
        config["extension_directory"] = str(extension_dir)
    con = duckdb.connect(":memory:", config=config)
    if extension_dir is None:
        con.execute("INSTALL sqlite")
    con.execute("LOAD sqlite")
    con.execute(f"ATTACH '{db_path.as_posix()}' AS meta (TYPE SQLITE, READ_ONLY)")
    return con
