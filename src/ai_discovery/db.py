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

SCHEMA_VERSION = 2

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
