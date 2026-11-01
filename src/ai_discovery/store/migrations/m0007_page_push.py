"""0007 — page_push: what `discover push` delivered, per page and target (roadmap item M2.6).

Not scan-scoped: a page keeps its delivery record across rescans, so `push --only-failed` retries exactly the
pages whose last delivery failed, and an unchanged page (same content hash) can be recognised.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS page_push (
            page_id      TEXT NOT NULL,
            target       TEXT NOT NULL,
            status       TEXT NOT NULL CHECK (status IN ('pushed', 'failed')),
            url          TEXT,
            error        TEXT,
            content_sha  TEXT,
            scan_id      INTEGER,
            pushed_at    TEXT NOT NULL,
            PRIMARY KEY (page_id, target)
        )""")
