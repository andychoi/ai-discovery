"""0003 — edge kind and evidence kind on call edges (roadmap item M1.12.a, ADR-0004).

``call_edges.resolved_by`` holds the edge origin (closed enum in
``graph/origins.py``); these two columns record what the origin implies, so
consumers can filter edges by kind (``calls``, ``dispatches_to``, …) and by
evidence (``static``, ``framework``, ``scip``, …) without knowing the origin
table. Rows written before this migration default to ``calls``/``static``.
"""

from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(call_edges)")}


def up(conn: sqlite3.Connection) -> None:
    cols = _columns(conn)
    if "edge_kind" not in cols:
        conn.execute("ALTER TABLE call_edges ADD COLUMN edge_kind TEXT NOT NULL DEFAULT 'calls'")
    if "evidence_kind" not in cols:
        conn.execute("ALTER TABLE call_edges ADD COLUMN evidence_kind TEXT NOT NULL DEFAULT 'static'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_unresolved_reason ON unresolved_calls(scan_id, reason)")
