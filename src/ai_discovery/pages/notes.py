"""Human notes on pages (roadmap item M3.1, ADR-0005 §5).

Notes live in ``page_notes`` keyed by page id, outside the rebuilt ``pages``
rows, so every rebuild keeps them. When a page id changes (a renamed symbol,
a moved file), record the rename with ``record_alias(old, new)``: a
``page_tombstones`` row with a successor. Notes written on the old id then
follow the chain to the live page; notes on a page removed without a successor
stay attached to its tombstone.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .model import split_page_id

MAX_ALIAS_HOPS = 32


@dataclass(frozen=True)
class Note:
    id: int
    page_id: str            # the id the note was written on
    author: str | None
    body: str
    created_at: str


def add_note(conn: sqlite3.Connection, page_id: str, body: str, author: str | None = None) -> int:
    split_page_id(page_id)
    if not body.strip():
        raise ValueError("note body must not be empty")
    cur = conn.execute(
        "INSERT INTO page_notes (page_id, author, body, created_at) VALUES (?, ?, ?, ?)",
        (page_id, author, body, datetime.now(timezone.utc).isoformat()),
    )
    return int(cur.lastrowid)


def record_alias(conn: sqlite3.Connection, old_id: str, new_id: str | None, removed_in_scan: int | None = None) -> None:
    """Mark *old_id* as gone; *new_id* is its successor (None: removed for good)."""
    split_page_id(old_id)
    if new_id is not None:
        split_page_id(new_id)
        if resolve(conn, new_id) == old_id:
            raise ValueError(f"alias {old_id} -> {new_id} would create a cycle")
    conn.execute(
        "INSERT INTO page_tombstones (page_id, removed_in_scan, successor) VALUES (?, ?, ?) "
        "ON CONFLICT(page_id) DO UPDATE SET removed_in_scan = excluded.removed_in_scan, "
        "successor = excluded.successor",
        (old_id, removed_in_scan, new_id),
    )


def resolve(conn: sqlite3.Connection, page_id: str) -> str:
    """Follow rename tombstones from *page_id* to the current id (or the last tombstone)."""
    current, seen = page_id, {page_id}
    for _ in range(MAX_ALIAS_HOPS):
        row = conn.execute("SELECT successor FROM page_tombstones WHERE page_id = ?", (current,)).fetchone()
        if not row or not row[0]:
            return current
        current = row[0]
        if current in seen:
            raise ValueError(f"alias cycle at {current}")
        seen.add(current)
    raise ValueError(f"alias chain from {page_id} longer than {MAX_ALIAS_HOPS}")


def _aliases_of(conn: sqlite3.Connection, page_id: str) -> set[str]:
    """Every id that resolves to *page_id* (including itself)."""
    ids = {page_id}
    frontier = [page_id]
    while frontier:
        nxt = frontier.pop()
        for (old,) in conn.execute("SELECT page_id FROM page_tombstones WHERE successor = ?", (nxt,)):
            if old not in ids:
                ids.add(old)
                frontier.append(old)
    return ids


def notes_for(conn: sqlite3.Connection, page_id: str) -> list[Note]:
    """Notes on *page_id* and on every older id that was renamed into it, oldest first."""
    target = resolve(conn, page_id)
    ids = sorted(_aliases_of(conn, target))
    marks = ", ".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, page_id, author, body, created_at FROM page_notes WHERE page_id IN ({marks}) "
        "ORDER BY created_at, id", ids).fetchall()
    return [Note(*r) for r in rows]


def delete_note(conn: sqlite3.Connection, note_id: int) -> bool:
    return conn.execute("DELETE FROM page_notes WHERE id = ?", (note_id,)).rowcount > 0
