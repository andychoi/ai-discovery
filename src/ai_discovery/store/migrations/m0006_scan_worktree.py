"""0006 — scan_runs.worktree: what the working tree held besides HEAD (roadmap item M2.8).

'' for a clean checkout (or a non-git folder / remote clone); otherwise a sha256 over the working tree's
uncommitted state (status, diff against HEAD, untracked files' content). A scan of the same commit is
"already up to date" only when this matches too — uncommitted edits, and reverting them, both rescan.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(scan_runs)")}
    if "worktree" not in cols:
        conn.execute("ALTER TABLE scan_runs ADD COLUMN worktree TEXT NOT NULL DEFAULT ''")
