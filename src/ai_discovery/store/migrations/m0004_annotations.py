"""0004 — annotations: findings, notes and proposed decisions (roadmap item M5.10).

Written by agents over MCP (``signer_kind = 'agent'``) or by people in the wiki
(``'human'``). Not scan-scoped: rows survive rescans and are re-anchored on read
by ``anchor_qn`` (a symbol's qualified name) or ``page_id`` (following rename
tombstones). Annotations never change graph facts.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS annotations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            kind          TEXT NOT NULL CHECK (kind IN ('finding', 'note', 'decision')),
            target        TEXT NOT NULL,
            page_id       TEXT,
            anchor_qn     TEXT,
            category      TEXT,
            text          TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            status        TEXT,
            signer_kind   TEXT NOT NULL CHECK (signer_kind IN ('agent', 'human')),
            client        TEXT,
            session_id    TEXT,
            scan_id       INTEGER,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            updated_by    TEXT
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_page ON annotations(page_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_anchor ON annotations(anchor_qn)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_session ON annotations(session_id)")
