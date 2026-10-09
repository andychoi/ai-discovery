"""0001 — baseline: the schema as of legacy SCHEMA_VERSION 15.

Fresh databases get the full schema here. Databases created by earlier
versions already have it (via ``db.init_db``'s legacy inline migrations,
frozen at v15) and are simply recorded as being at 0001 — no hard break.
"""

from __future__ import annotations

import sqlite3

from ...db import SCHEMA_VERSION, _SCHEMA_SQL, _migrate_schema, now_iso

LEGACY_SCHEMA_VERSION = 15


def up(conn: sqlite3.Connection) -> None:
    assert SCHEMA_VERSION == LEGACY_SCHEMA_VERSION, (
        "db.SCHEMA_VERSION is frozen at 15; add a numbered migration instead"
    )
    for stmt in _split_sql(_SCHEMA_SQL):
        conn.execute(stmt)
    previous = conn.row_factory
    conn.row_factory = sqlite3.Row  # legacy _migrate_schema reads rows by name
    try:
        _migrate_schema(conn)
    finally:
        conn.row_factory = previous
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, now_iso()),
    )


def _split_sql(script: str) -> list[str]:
    """Split a schema script into statements (executescript would commit)."""
    out, buf = [], []
    for line in script.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";") and sqlite3.complete_statement("\n".join(buf)):
            stmt = "\n".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return out
