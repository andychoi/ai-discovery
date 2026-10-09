"""Snapshot pointer and garbage collection (roadmap item M1.5, ADR-0002).

Every scan is a snapshot keyed by ``scan_runs.id``. Readers (status, serve,
MCP, ask) use ``current_scan``, flipped atomically only when a scan completes,
so a running or failed scan never disturbs what readers see.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

DEFAULT_KEEP = 3


def set_current_scan(conn: sqlite3.Connection, scan_id: int) -> None:
    """Point readers at *scan_id* (single-row upsert; atomic)."""
    conn.execute(
        "INSERT INTO current_scan (id, scan_id, flipped_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET scan_id = excluded.scan_id, flipped_at = excluded.flipped_at",
        (scan_id, datetime.now(timezone.utc).isoformat()),
    )


def get_current_scan(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute("SELECT scan_id FROM current_scan WHERE id = 1").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None  # pre-0002 DB
        raise
    return int(row[0]) if row else None


def gc_snapshots(conn: sqlite3.Connection, keep: int = DEFAULT_KEEP) -> list[int]:
    """Keep the newest *keep* finished scans plus the current and any running scan; delete the rest.

    Child rows go via ``ON DELETE CASCADE`` (requires ``PRAGMA foreign_keys=ON``,
    which ``db.get_conn`` sets). Returns the deleted scan ids.
    """
    if keep < 1:
        raise ValueError("keep must be >= 1")
    current = get_current_scan(conn)
    running = {r[0] for r in conn.execute("SELECT id FROM scan_runs WHERE status = 'running'")}
    finished = [r[0] for r in conn.execute("SELECT id FROM scan_runs ORDER BY id DESC").fetchall()
                if r[0] not in running]
    keep_ids = set(finished[:keep]) | running
    if current is not None:
        keep_ids.add(current)
    doomed = [i for i in finished if i not in keep_ids]
    for scan_id in doomed:
        conn.execute("DELETE FROM scan_runs WHERE id = ?", (scan_id,))
    return doomed
