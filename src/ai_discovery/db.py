"""SQLite database module for the Discovery CLI.

Production-grade connection helpers (WAL, busy_timeout, mmap) and schema
management.  All pipeline phases write to this DB; Tier 1 summarization
runs 10+ concurrent LLM callbacks, so WAL mode + retry_on_locked handle
contention.

Pattern lifted from app/shared/sdlc_db.py.
"""

import functools
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 14

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
    file_hash       TEXT DEFAULT '',
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
    confidence  REAL DEFAULT 1.0,
    resolved_by TEXT DEFAULT '',
    -- P1-a: prevent duplicate edges when phase 7 re-runs on the same scan_id.
    UNIQUE(scan_id, caller_id, callee_name, edge_type)
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
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id             INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    domain              TEXT,
    doc_type            TEXT NOT NULL,
    doc_id              TEXT,
    title               TEXT,
    content_md          TEXT,
    confidence          REAL,
    unverified_claims   INTEGER DEFAULT 0,
    verified_row_count  INTEGER DEFAULT 0,
    push_status         TEXT DEFAULT 'local',
    push_url            TEXT,
    created_at          TEXT NOT NULL,
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
    structured_steps_json TEXT,
    input_json      TEXT,
    process_json    TEXT,
    output_json     TEXT,
    data_flow_json  TEXT,
    interfaces_json TEXT,
    mermaid             TEXT,
    mermaid_flowchart   TEXT,
    bpmn_xml            TEXT,
    ipo_md          TEXT,
    confidence      REAL DEFAULT 1.0,
    created_at      TEXT NOT NULL,
    UNIQUE(scan_id, scenario_id)
);
CREATE INDEX IF NOT EXISTS idx_scenario_flows_scan ON scenario_flows(scan_id);

CREATE TABLE IF NOT EXISTS screens (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id             INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    screen_id           TEXT NOT NULL,
    menu_path_json      TEXT,
    label               TEXT,
    path                TEXT,
    fe_component        TEXT,
    crud_profile        TEXT,
    interaction_mode    TEXT,
    permissions_json    TEXT,
    related_screens_json TEXT,
    metadata_json       TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(scan_id, screen_id)
);
CREATE INDEX IF NOT EXISTS idx_screens_scan ON screens(scan_id);
CREATE INDEX IF NOT EXISTS idx_screens_id ON screens(screen_id);

CREATE TABLE IF NOT EXISTS screen_mappings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    screen_id           INTEGER NOT NULL REFERENCES screens(id) ON DELETE CASCADE,
    fe_api_calls_json   TEXT,
    be_controllers_json TEXT,
    be_services_json    TEXT,
    db_tables_json      TEXT,
    batch_jobs_json     TEXT,
    external_interfaces_json TEXT,
    source_files_json   TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_screen_mappings_screen ON screen_mappings(screen_id);

CREATE TABLE IF NOT EXISTS screen_source_hashes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    screen_id   INTEGER NOT NULL REFERENCES screens(id) ON DELETE CASCADE,
    file_path   TEXT NOT NULL,
    sha256_hash TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(screen_id, file_path)
);
CREATE INDEX IF NOT EXISTS idx_source_hashes_screen ON screen_source_hashes(screen_id);

CREATE TABLE IF NOT EXISTS screen_specs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id     INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    screen_id   TEXT NOT NULL,
    spec_json   TEXT NOT NULL,
    confidence  REAL DEFAULT 0.8,
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    model       TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(scan_id, screen_id)
);
CREATE INDEX IF NOT EXISTS idx_screen_specs_scan ON screen_specs(scan_id);
CREATE INDEX IF NOT EXISTS idx_screen_specs_id ON screen_specs(screen_id);

CREATE TABLE IF NOT EXISTS screen_review_claims (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    screen_spec_id INTEGER NOT NULL REFERENCES screen_specs(id) ON DELETE CASCADE,
    claim_text     TEXT NOT NULL,
    status         TEXT,
    evidence       TEXT,
    source_file    TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_screen_claims_spec ON screen_review_claims(screen_spec_id);

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

CREATE TABLE IF NOT EXISTS state_transitions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id           INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    entity            TEXT NOT NULL,
    -- Phase 2d: unique qualified key. Two same-named classes in different
    -- modules have distinct entity_id (e.g. billing.Order vs ecommerce.Order).
    entity_id         TEXT NOT NULL DEFAULT '',
    field             TEXT,
    from_state        TEXT,
    to_state          TEXT,
    trigger_function  TEXT,
    guard_expr        TEXT,
    confidence        REAL DEFAULT 1.0,
    entry_points_json TEXT,
    metadata_json     TEXT,
    UNIQUE(scan_id, entity_id, field, from_state, to_state, trigger_function)
);
CREATE INDEX IF NOT EXISTS idx_transitions_scan_entity    ON state_transitions(scan_id, entity);
CREATE INDEX IF NOT EXISTS idx_transitions_scan_entity_id ON state_transitions(scan_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_transitions_trigger        ON state_transitions(trigger_function);

CREATE TABLE IF NOT EXISTS entity_state_machines (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id           INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    entity            TEXT NOT NULL,
    -- Phase 2d: unique grouping key matching state_transitions.entity_id.
    entity_id         TEXT NOT NULL DEFAULT '',
    states_json       TEXT,
    fields_json       TEXT,
    source_files_json TEXT,
    confidence        REAL DEFAULT 1.0,
    metadata_json     TEXT,
    UNIQUE(scan_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_fsm_scan ON entity_state_machines(scan_id);

CREATE TABLE IF NOT EXISTS db_relationship (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id       INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    from_entity   TEXT NOT NULL,
    to_entity     TEXT NOT NULL,
    from_field    TEXT DEFAULT '',
    to_field      TEXT DEFAULT '',
    cardinality   TEXT DEFAULT '',
    source        TEXT DEFAULT '',     -- sql | jpa | ef
    source_file   TEXT DEFAULT '',
    source_line   INTEGER DEFAULT 0,
    confidence    REAL DEFAULT 1.0,
    inferred      INTEGER DEFAULT 0,   -- 1 = name-convention guess, not a declared FK
    UNIQUE(scan_id, from_entity, from_field, to_entity, to_field)
);
CREATE INDEX IF NOT EXISTS idx_db_rel_scan ON db_relationship(scan_id);
CREATE INDEX IF NOT EXISTS idx_db_rel_from ON db_relationship(scan_id, from_entity);
CREATE INDEX IF NOT EXISTS idx_db_rel_to   ON db_relationship(scan_id, to_entity);

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
    _migrate_schema(conn)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, now_iso()),
    )
    conn.commit()
    conn.close()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply forward-only schema migrations on top of the live DB.

    Each migration block is keyed by the SCHEMA_VERSION it brings the DB up
    to, and is gated on the highest version already recorded in
    `schema_version`. New DBs start at SCHEMA_VERSION and short-circuit out
    of every block.
    """
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = (row["v"] if row else None) or 0

    if current < 5:
        # Phase numbering migrated from decimal sub-phases (8.5, 12.5, 13.5,
        # 13.6) to contiguous integers 5..19. Existing rows in
        # phase_checkpoints carry the old numbers — rewrite them so resume
        # logic and the checkpoint display continue to work. Order matters:
        # rewrite from highest old value to lowest so new values can't
        # collide with rows that haven't been migrated yet.
        renumber = [
            (16, 19),    # finalise
            (15, 18),    # render_markdown
            (14, 17),    # self_review
            (13.6, 16),  # process_mining
            (13.5, 15),  # visual_artifacts
            (13, 14),    # tier3_doc_rollup
            (12.5, 13),  # scenario_flow_inference
            (8.5, 8),    # execution_slices
        ]
        for old, new in renumber:
            conn.execute(
                "UPDATE phase_checkpoints SET phase_num = ? "
                "WHERE ABS(phase_num - ?) < 0.01",
                (new, old),
            )

    if current < 6:
        # Track 4: per-claim confidence + AST-row counting.
        # `verified_row_count` records how many AST-extracted rows (verified
        # endpoints, entities) shipped in each doc, so doc-level confidence
        # can be blended deterministically rather than taking the LLM's
        # self-asserted score at face value.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(generated_docs)").fetchall()}
        if "verified_row_count" not in cols:
            conn.execute(
                "ALTER TABLE generated_docs ADD COLUMN verified_row_count INTEGER DEFAULT 0"
            )

    if current < 9:
        # Activity diagrams are emitted as inline Mermaid flowcharts; ensure
        # the column exists on databases created before it was added. Diagram
        # source from earlier formats is not preserved; the flowchart is
        # regenerated on the next scan (or via scripts/regen_pf_diagrams.py).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(scenario_flows)").fetchall()}
        if "mermaid_flowchart" not in cols:
            conn.execute("ALTER TABLE scenario_flows ADD COLUMN mermaid_flowchart TEXT")

    if current < 10:
        # FK-aware table docs (Phase 1): db_relationship stores foreign-key edges
        # between entities. The table is created by the CREATE TABLE IF NOT EXISTS
        # in _SCHEMA_SQL (run before this migration), so existing DBs gain it
        # automatically; this block only records the version bump.
        pass

    if current < 11:
        # Processing-logic level structuring: scenario_flows gains a hierarchical
        # `structured_steps_json` (phases → steps → conditional branch arms). The
        # legacy flat `steps_json` is derived from it, so existing DBs keep
        # rendering from flat steps until the next scan repopulates the hierarchy.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(scenario_flows)").fetchall()}
        if "structured_steps_json" not in cols:
            conn.execute("ALTER TABLE scenario_flows ADD COLUMN structured_steps_json TEXT")

    if current < 12:
        # Screen-spec claim audit trail (CRIT-3 parity): screen_review_claims
        # stores per-claim verdicts for screen specs the way review_claims does
        # for rollup docs, so triage can inspect contradicted/unverified screen
        # claims. The table is created by the CREATE TABLE IF NOT EXISTS in
        # _SCHEMA_SQL (run before this migration), so existing DBs gain it
        # automatically; this block only records the version bump.
        pass

    if current < 13:
        # A-2 incremental re-scan: code_nodes gains a per-file content hash
        # stamped at parse time. Pre-migration rows keep '' — blank hashes are
        # excluded from cross-scan summary reuse, so old scans simply don't
        # contribute reusable summaries (no false matches).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(code_nodes)").fetchall()}
        if "file_hash" not in cols:
            conn.execute("ALTER TABLE code_nodes ADD COLUMN file_hash TEXT DEFAULT ''")

    if current < 14:
        # A-4 export-graph: persist resolution-stage provenance on call edges
        # (CallEdge.metadata["resolved_by"]) so the canonical JSON export and
        # triage queries carry it. Pre-migration rows keep '' (unknown stage).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(call_edges)").fetchall()}
        if "resolved_by" not in cols:
            conn.execute("ALTER TABLE call_edges ADD COLUMN resolved_by TEXT DEFAULT ''")


# ---------------------------------------------------------------------------
# Entity relationship (foreign-key) helpers — FK-aware table docs, Phase 1
# ---------------------------------------------------------------------------

def persist_relationships(db_path: Path, scan_id: int, relationships) -> int:
    """Upsert EntityRelationship edges for a scan. Returns the count written.

    Idempotent: re-persisting the same scan replaces prior edges (the UNIQUE
    constraint dedups by endpoints + columns, keeping the latest values).
    """
    conn = get_conn(db_path)
    try:
        conn.execute("DELETE FROM db_relationship WHERE scan_id = ?", (scan_id,))
        rows = [
            (scan_id, r.from_entity, r.to_entity, r.from_field, r.to_field,
             r.cardinality, r.source, r.source_file, r.source_line,
             float(r.confidence), 1 if r.inferred else 0)
            for r in relationships
        ]
        conn.executemany(
            """INSERT OR REPLACE INTO db_relationship
               (scan_id, from_entity, to_entity, from_field, to_field,
                cardinality, source, source_file, source_line, confidence, inferred)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_relationships(db_path: Path, scan_id: int, *, from_entity: str | None = None):
    """Return EntityRelationship edges for a scan (optionally filtered by source
    entity), ordered for stable rendering."""
    from .graph.models import EntityRelationship

    conn = get_conn(db_path)
    try:
        if from_entity is not None:
            cur = conn.execute(
                "SELECT * FROM db_relationship WHERE scan_id = ? AND from_entity = ? "
                "ORDER BY from_entity, from_field, to_entity",
                (scan_id, from_entity),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM db_relationship WHERE scan_id = ? "
                "ORDER BY from_entity, from_field, to_entity",
                (scan_id,),
            )
        return [
            EntityRelationship(
                from_entity=row["from_entity"], to_entity=row["to_entity"],
                from_field=row["from_field"], to_field=row["to_field"],
                cardinality=row["cardinality"], source=row["source"],
                source_file=row["source_file"], source_line=row["source_line"],
                confidence=row["confidence"], inferred=bool(row["inferred"]),
            )
            for row in cur.fetchall()
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase checkpoint helpers
# ---------------------------------------------------------------------------

def record_phase_start(db_path: Path, scan_id: int, phase_num: int, phase_name: str) -> None:
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
    phase_num: int,
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
    phase_num: int,
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


def get_last_complete_phase(db_path: Path, scan_id: int) -> int | None:
    """Return the highest phase_num that completed successfully, or None.

    Phases are integers; the column is REAL for historical reasons, so coerce
    the stored value to int on the way out.
    """
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(phase_num) as last_phase FROM phase_checkpoints WHERE scan_id = ? AND status = 'complete'",
            (scan_id,),
        ).fetchone()
        return int(row['last_phase']) if row and row['last_phase'] is not None else None
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
