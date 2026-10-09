"""0002 — v1 store tables (roadmap item M1.5; ADR-0002, ADR-0005, design/architecture-v1.md §5).

Adds the reader pointer, stage/degradation bookkeeping, caches and the page
store. ``pages_fts`` needs SQLite FTS5; when the build lacks it the table is
skipped (``discover doctor`` reports ``sqlite_fts5``) and ``store.fts`` can
create it later — FTS is always rebuildable from ``pages``.
"""

from __future__ import annotations

import sqlite3

_TABLES = [
    """CREATE TABLE IF NOT EXISTS current_scan (
        id          INTEGER PRIMARY KEY CHECK (id = 1),
        scan_id     INTEGER NOT NULL REFERENCES scan_runs(id),
        flipped_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS stage_runs (
        scan_id     INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
        stage       TEXT NOT NULL,
        version     TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        status      TEXT NOT NULL,
        started_at  TEXT,
        ended_at    TEXT,
        PRIMARY KEY (scan_id, stage)
    )""",
    """CREATE TABLE IF NOT EXISTS unresolved_calls (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id         INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
        caller_id       INTEGER REFERENCES code_nodes(id) ON DELETE CASCADE,
        callee_name     TEXT NOT NULL,
        reason          TEXT NOT NULL,
        candidate_count INTEGER DEFAULT 0,
        file            TEXT,
        line            INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS idx_unresolved_scan ON unresolved_calls(scan_id)",
    """CREATE TABLE IF NOT EXISTS degradations (
        scan_id     INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
        stage       TEXT NOT NULL,
        kind        TEXT NOT NULL,
        count       INTEGER NOT NULL DEFAULT 0,
        sample_json TEXT,
        PRIMARY KEY (scan_id, stage, kind)
    )""",
    """CREATE TABLE IF NOT EXISTS file_parse_cache (
        path            TEXT NOT NULL,
        content_hash    TEXT NOT NULL,
        parser_version  TEXT NOT NULL,
        encoding        TEXT NOT NULL,
        payload         BLOB NOT NULL,
        created_at      TEXT NOT NULL,
        PRIMARY KEY (path, content_hash, parser_version, encoding)
    )""",
    """CREATE TABLE IF NOT EXISTS llm_cache (
        key             TEXT PRIMARY KEY,
        role            TEXT,
        model_id        TEXT,
        prompt_id       TEXT,
        prompt_version  TEXT,
        output          TEXT NOT NULL,
        usage_json      TEXT,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS pages (
        scan_id             INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
        page_id             TEXT NOT NULL,
        type                TEXT NOT NULL,
        title               TEXT NOT NULL,
        facts_json          TEXT,
        body_md             TEXT,
        links_json          TEXT,
        citations_json      TEXT,
        confidence          REAL NOT NULL DEFAULT 1.0,
        stamp_json          TEXT,
        source_hashes_json  TEXT,
        freshness           TEXT NOT NULL DEFAULT 'fresh',
        prose_ref           TEXT,
        PRIMARY KEY (scan_id, page_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pages_type ON pages(scan_id, type)",
    """CREATE TABLE IF NOT EXISTS page_links (
        scan_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
        src     TEXT NOT NULL,
        dst     TEXT NOT NULL,
        kind    TEXT NOT NULL DEFAULT 'link',
        PRIMARY KEY (scan_id, src, dst, kind)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_page_links_dst ON page_links(scan_id, dst)",
    """CREATE TABLE IF NOT EXISTS page_tombstones (
        page_id         TEXT PRIMARY KEY,
        removed_in_scan INTEGER,
        successor       TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS page_notes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        page_id     TEXT NOT NULL,
        author      TEXT,
        body        TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_page_notes_page ON page_notes(page_id)",
    """CREATE TABLE IF NOT EXISTS vec_meta (
        store           TEXT PRIMARY KEY,
        embed_model_id  TEXT NOT NULL,
        dim             INTEGER NOT NULL,
        pinned_at       TEXT NOT NULL
    )""",
]


def up(conn: sqlite3.Connection) -> None:
    for stmt in _TABLES:
        conn.execute(stmt)
    from ..fts import create_fts

    create_fts(conn)  # no-op (returns False) when FTS5 is unavailable
