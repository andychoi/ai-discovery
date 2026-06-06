"""Tests for viewer node drill-down + search (assessment 07 A-5).

The metrics viewer answers "is the scan good?"; these functions answer "what
does this code do?" — node → source snippet + Tier-1 summary + confidence/
resolution provenance, plus symbol/file search. Pure data functions (same
convention as test_viewer_dashboard.py); routes stay thin.
"""

import sqlite3

import pytest

from ai_discovery.db import init_db
from ai_discovery.viewer.dashboard import node_detail, search_nodes


@pytest.fixture
def seeded_db(tmp_path):
    db = tmp_path / "discovery-demo.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO scan_runs (id, started_at, status) VALUES (1, '2026-01-01', 'completed')")
    conn.execute("INSERT INTO scan_runs (id, started_at, status) VALUES (2, '2026-02-01', 'completed')")
    # stale node from scan 1 with the same qualified name — must never surface
    conn.execute(
        "INSERT INTO code_nodes (id, scan_id, file_path, language, node_type, name, "
        "qualified_name, line_start, line_end, source_code, domain) "
        "VALUES (1, 1, 'old/orders.py', 'python', 'function', 'create', "
        "'orders.create', 1, 5, 'OLD', 'orders')"
    )
    conn.execute(
        "INSERT INTO code_nodes (id, scan_id, file_path, language, node_type, name, "
        "qualified_name, line_start, line_end, source_code, domain) "
        "VALUES (2, 2, 'src/orders.py', 'python', 'function', 'create', "
        "'orders.create', 10, 20, 'def create(): ...', 'orders')"
    )
    conn.execute(
        "INSERT INTO code_nodes (id, scan_id, file_path, language, node_type, name, "
        "qualified_name, line_start, line_end, source_code, domain) "
        "VALUES (3, 2, 'src/repo.py', 'python', 'function', 'save', "
        "'repo.save', 1, 8, 'def save(): ...', 'orders')"
    )
    conn.execute(
        "INSERT INTO call_edges (scan_id, caller_id, callee_id, callee_name, edge_type, "
        "confidence, resolved_by) "
        "VALUES (2, 2, 3, 'repo.save', 'direct_call', 0.95, 'import_scope')"
    )
    conn.execute(
        "INSERT INTO node_summaries (node_id, tier, purpose, business_rules, io_summary, "
        "tech_debt_signals, created_at) "
        "VALUES (2, 'tier1', 'Creates an order', 'None detected', 'Writes orders table', "
        "'None detected', '2026-02-01')"
    )
    conn.commit()
    conn.close()
    return db


def test_search_matches_symbol_and_file(seeded_db):
    by_symbol = search_nodes(seeded_db, "orders.create")
    assert [r["qualified_name"] for r in by_symbol] == ["orders.create"]
    assert by_symbol[0]["file_path"] == "src/orders.py"  # latest scan, not OLD

    by_file = search_nodes(seeded_db, "repo.py")
    assert [r["qualified_name"] for r in by_file] == ["repo.save"]


def test_search_is_case_insensitive_and_limited(seeded_db):
    assert search_nodes(seeded_db, "ORDERS")  # matches qualified_name + file
    assert len(search_nodes(seeded_db, "s", limit=1)) == 1


def test_search_short_query_returns_empty(seeded_db):
    assert search_nodes(seeded_db, "") == []
    assert search_nodes(seeded_db, " ") == []


def test_node_detail_full_shape(seeded_db):
    detail = node_detail(seeded_db, "orders.create")
    assert detail["file_path"] == "src/orders.py"        # latest scan
    assert detail["source_code"] == "def create(): ..."
    assert detail["summary"]["purpose"] == "Creates an order"
    (edge,) = detail["edges_out"]
    assert edge["callee"] == "repo.save"
    assert edge["confidence"] == 0.95
    assert edge["resolved_by"] == "import_scope"          # provenance surfaces
    assert detail["edges_in"] == []

    callee = node_detail(seeded_db, "repo.save")
    assert callee["summary"] is None                      # unsummarized is honest
    (incoming,) = callee["edges_in"]
    assert incoming["caller"] == "orders.create"


def test_node_detail_unknown_returns_none(seeded_db):
    assert node_detail(seeded_db, "nope.missing") is None
