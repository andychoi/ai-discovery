"""SQLite database module for the Discovery CLI.

Production-grade connection helpers (WAL, busy_timeout, mmap) and schema
management.  All pipeline phases write to this DB; Phase 3 runs 10+
concurrent GenAI callbacks, so WAL mode + retry_on_locked handle contention.

Pattern lifted from app/shared/sdlc_db.py.
"""

import functools
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_url      TEXT,
    repo_path     TEXT,
    commit_sha    TEXT,
    branch        TEXT,
    project_slug  TEXT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    config_json   TEXT
);

CREATE TABLE IF NOT EXISTS code_nodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL,
    language        TEXT,
    node_type       TEXT NOT NULL,
    name            TEXT NOT NULL,
    qualified_name  TEXT NOT NULL,
    line_start      INTEGER,
    line_end        INTEGER,
    source_code     TEXT,
    annotations     TEXT,
    params          TEXT,
    return_type     TEXT,
    framework_hints TEXT,
    domain          TEXT,
    UNIQUE(scan_id, qualified_name)
);
CREATE INDEX IF NOT EXISTS idx_nodes_scan_domain ON code_nodes(scan_id, domain);
CREATE INDEX IF NOT EXISTS idx_nodes_type        ON code_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified   ON code_nodes(qualified_name);

CREATE TABLE IF NOT EXISTS call_edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id     INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    caller_id   INTEGER NOT NULL REFERENCES code_nodes(id) ON DELETE CASCADE,
    callee_id   INTEGER REFERENCES code_nodes(id) ON DELETE SET NULL,
    callee_name TEXT,
    edge_type   TEXT,
    confidence  REAL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_edges_caller ON call_edges(caller_id);
CREATE INDEX IF NOT EXISTS idx_edges_callee ON call_edges(callee_id);
CREATE INDEX IF NOT EXISTS idx_edges_scan_caller ON call_edges(scan_id, caller_id);
CREATE INDEX IF NOT EXISTS idx_edges_scan_callee ON call_edges(scan_id, callee_id);

CREATE TABLE IF NOT EXISTS domains (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id      INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    node_count   INTEGER,
    entry_points TEXT,
    tech_stack   TEXT,
    UNIQUE(scan_id, name)
);

CREATE TABLE IF NOT EXISTS node_summaries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id          INTEGER NOT NULL UNIQUE REFERENCES code_nodes(id) ON DELETE CASCADE,
    tier             TEXT,
    model_used       TEXT,
    purpose          TEXT,
    business_rules   TEXT,
    io_summary       TEXT,
    tech_debt_signals TEXT,
    raw_response     TEXT,
    tokens_in        INTEGER,
    tokens_out       INTEGER,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summaries_node ON node_summaries(node_id);

CREATE TABLE IF NOT EXISTS business_flows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id     INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    domain      TEXT,
    flow_type   TEXT,
    name        TEXT NOT NULL,
    description TEXT,
    node_ids    TEXT,
    model_used  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flows_domain ON business_flows(domain);

CREATE TABLE IF NOT EXISTS generated_docs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id           INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    domain            TEXT,
    doc_type          TEXT NOT NULL,
    doc_id            TEXT,
    title             TEXT,
    content_md        TEXT,
    confidence        REAL,
    unverified_claims INTEGER DEFAULT 0,
    push_status       TEXT DEFAULT 'local',
    push_url          TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE(scan_id, domain, doc_type)
);
CREATE INDEX IF NOT EXISTS idx_docs_scan ON generated_docs(scan_id);

CREATE TABLE IF NOT EXISTS review_claims (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      INTEGER NOT NULL REFERENCES generated_docs(id) ON DELETE CASCADE,
    claim_text  TEXT NOT NULL,
    status      TEXT,
    evidence    TEXT,
    source_file TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_doc ON review_claims(doc_id);

CREATE TABLE IF NOT EXISTS scenario_flows (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    scenario_id     TEXT NOT NULL,
    domain          TEXT,
    steps_json      TEXT,
    input_json      TEXT,
    process_json    TEXT,
    output_json     TEXT,
    data_flow_json  TEXT,
    interfaces_json TEXT,
    mermaid         TEXT,
    plantuml        TEXT,
    bpmn_xml        TEXT,
    ipo_md          TEXT,
    confidence      REAL DEFAULT 1.0,
    created_at      TEXT NOT NULL,
    UNIQUE(scan_id, scenario_id)
);
CREATE INDEX IF NOT EXISTS idx_scenario_flows_scan ON scenario_flows(scan_id);

CREATE TABLE IF NOT EXISTS llm_costs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id    INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    tier       TEXT,
    model      TEXT,
    calls      INTEGER DEFAULT 0,
    tokens_in  INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    est_usd    REAL DEFAULT 0.0,
    UNIQUE(scan_id, tier)
);

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phase_checkpoints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    phase_num       REAL NOT NULL,
    phase_name      TEXT NOT NULL,
    status          TEXT DEFAULT 'running',
    started_at      TEXT,
    completed_at    TEXT,
    error_msg       TEXT,
    metadata_json   TEXT,
    UNIQUE(scan_id, phase_num)
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_scan ON phase_checkpoints(scan_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_phase ON phase_checkpoints(scan_id, phase_num);
"""

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply production-grade PRAGMAs to a connection."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-8000")
    conn.execute("PRAGMA temp_store=MEMORY")


def get_conn(db_path: Path) -> sqlite3.Connection:
    """Get a SQLite connection with production PRAGMAs (read-write)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


def get_read_conn(db_path: Path) -> sqlite3.Connection:
    """Get a read-optimised SQLite connection with mmap for faster reads.

    Safe for concurrent use under WAL mode -- multiple read connections
    can coexist with the single writer thread.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    conn.execute("PRAGMA mmap_size=268435456")  # 256 MB mmap for read perf
    return conn


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def retry_on_locked(fn=None, *, max_retries: int = 3, backoff: float = 0.5):
    """Decorator: retry a function on SQLite 'database is locked' errors.

    Usage::

        @retry_on_locked
        def do_work(): ...

        @retry_on_locked(max_retries=5, backoff=1.0)
        def do_work(): ...
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "locked" in str(e) and attempt < max_retries - 1:
                        logger.warning(
                            "SQLite locked on %s, retry %d/%d",
                            func.__name__,
                            attempt + 1,
                            max_retries,
                        )
                        time.sleep(backoff * (2 ** attempt))
                        continue
                    raise
        return wrapper

    if fn is not None:
        return decorator(fn)
    return decorator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db(db_path: Path) -> None:
    """Create all tables if they don't exist.  Idempotent."""
    conn = get_conn(db_path)
    conn.executescript(_SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, now_iso()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Phase checkpoint helpers
# ---------------------------------------------------------------------------

def record_phase_start(db_path: Path, scan_id: int, phase_num: float, phase_name: str) -> None:
    """Record the start of a phase checkpoint."""
    conn = get_conn(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO phase_checkpoints
               (scan_id, phase_num, phase_name, status, started_at)
               VALUES (?, ?, ?, ?, ?)""",
            (scan_id, phase_num, phase_name, 'running', now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def record_phase_complete(
    db_path: Path,
    scan_id: int,
    phase_num: float,
    phase_name: str,
    metadata: dict = None,
) -> None:
    """Record successful completion of a phase checkpoint.

    Uses UPDATE if a row already exists (preserving started_at from
    record_phase_start), otherwise inserts a new row.
    """
    import json
    conn = get_conn(db_path)
    try:
        # Try UPDATE first (preserves started_at from record_phase_start)
        result = conn.execute(
            """UPDATE phase_checkpoints
               SET status = ?, completed_at = ?, metadata_json = ?
               WHERE scan_id = ? AND phase_num = ?""",
            (
                'complete',
                now_iso(),
                json.dumps(metadata) if metadata else None,
                scan_id,
                phase_num,
            ),
        )
        if result.rowcount == 0:
            # No existing row (e.g. called without record_phase_start)
            conn.execute(
                """INSERT INTO phase_checkpoints
                   (scan_id, phase_num, phase_name, status, started_at, completed_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan_id,
                    phase_num,
                    phase_name,
                    'complete',
                    now_iso(),
                    now_iso(),
                    json.dumps(metadata) if metadata else None,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def record_phase_error(
    db_path: Path,
    scan_id: int,
    phase_num: float,
    phase_name: str,
    error_msg: str,
) -> None:
    """Record failure of a phase checkpoint.

    Error messages are truncated to 500 chars to avoid storing
    sensitive data (API keys, connection strings) from exception traces.
    """
    safe_msg = str(error_msg)[:500]
    conn = get_conn(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO phase_checkpoints
               (scan_id, phase_num, phase_name, status, completed_at, error_msg)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (scan_id, phase_num, phase_name, 'failed', now_iso(), safe_msg),
        )
        conn.commit()
    finally:
        conn.close()


def get_last_complete_phase(db_path: Path, scan_id: int) -> float | None:
    """Return the highest phase_num that completed successfully, or None."""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(phase_num) as last_phase FROM phase_checkpoints WHERE scan_id = ? AND status = 'complete'",
            (scan_id,),
        ).fetchone()
        return row['last_phase'] if row and row['last_phase'] is not None else None
    finally:
        conn.close()


def get_all_checkpoints(db_path: Path, scan_id: int) -> list[dict]:
    """Return all checkpoints for a scan, ordered by phase_num."""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM phase_checkpoints WHERE scan_id = ? ORDER BY phase_num ASC",
            (scan_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
