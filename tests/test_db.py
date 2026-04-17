"""Tests for app.db — schema creation, PRAGMAs, retry logic."""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_discovery.db import get_conn, get_read_conn, init_db, retry_on_locked

EXPECTED_TABLES = {
    "scan_runs",
    "code_nodes",
    "call_edges",
    "domains",
    "node_summaries",
    "business_flows",
    "generated_docs",
    "review_claims",
    "llm_costs",
    "schema_version",
}


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Return a temporary DB path and initialise the schema."""
    p = tmp_path / "test.db"
    init_db(p)
    return p


def test_init_db_creates_tables(db_path: Path) -> None:
    """All 10 tables should exist after init_db."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = {r["name"] for r in rows}
    conn.close()
    assert EXPECTED_TABLES.issubset(tables), f"Missing tables: {EXPECTED_TABLES - tables}"


def test_get_conn_returns_wal_mode(db_path: Path) -> None:
    """Writer connection should use WAL journal mode."""
    conn = get_conn(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_get_read_conn_has_mmap(db_path: Path) -> None:
    """Reader connection should have mmap_size > 0."""
    conn = get_read_conn(db_path)
    mmap = conn.execute("PRAGMA mmap_size").fetchone()[0]
    conn.close()
    assert mmap > 0


def test_retry_on_locked_decorator() -> None:
    """retry_on_locked should retry 3 times then succeed on the last attempt."""
    call_count = 0

    @retry_on_locked
    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    with patch("app.db.time.sleep"):  # skip real sleeps
        result = flaky()

    assert result == "ok"
    assert call_count == 3
