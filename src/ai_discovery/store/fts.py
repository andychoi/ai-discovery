"""Full-text index over pages (roadmap item M1.5).

``pages_fts`` is derived data: it can always be dropped and rebuilt from
``pages`` (FTS5 virtual tables can't be ALTERed, so schema changes rebuild).
"""

from __future__ import annotations

import json
import sqlite3

FTS_TABLE = "pages_fts"


def fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE temp._fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def create_fts(conn: sqlite3.Connection) -> bool:
    """Create ``pages_fts`` if FTS5 is available. Returns whether it exists."""
    if not fts5_available(conn):
        return False
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
        "page_id UNINDEXED, scan_id UNINDEXED, title, body_md, facts_text, "
        "tokenize = 'unicode61 remove_diacritics 2')"
    )
    return True


def _facts_text(facts_json: str | None) -> str:
    if not facts_json:
        return ""
    try:
        facts = json.loads(facts_json)
    except ValueError:
        return facts_json
    out: list[str] = []

    def walk(v) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif v is not None:
            out.append(str(v))
    walk(facts)
    return " ".join(out)


def rebuild_fts(conn: sqlite3.Connection, scan_id: int | None = None) -> int:
    """Drop and rebuild ``pages_fts`` from ``pages`` (one scan, or all). Returns rows indexed."""
    if not fts5_available(conn):
        raise sqlite3.OperationalError("FTS5 is not available in this SQLite build")
    conn.execute(f"DROP TABLE IF EXISTS {FTS_TABLE}")
    create_fts(conn)
    where, args = ("WHERE scan_id = ?", (scan_id,)) if scan_id is not None else ("", ())
    rows = conn.execute(f"SELECT page_id, scan_id, title, body_md, facts_json FROM pages {where}", args).fetchall()
    conn.executemany(
        f"INSERT INTO {FTS_TABLE} (page_id, scan_id, title, body_md, facts_text) VALUES (?, ?, ?, ?, ?)",
        [(r[0], r[1], r[2], r[3] or "", _facts_text(r[4])) for r in rows],
    )
    return len(rows)
