"""Tests for the canonical knowledge-graph JSON export (assessment 07 A-4).

`discover export-graph` produces one portable JSON view of a scan — nodes,
edges (confidence + resolution stage), domains, FK relationships, FSMs, and a
doc index — so teammates/viewers/federation can consume results without
shipping SQLite. The export is a VIEW of the DB, never a migration.
"""

import json
import sqlite3

import pytest

from ai_discovery.db import init_db
from ai_discovery.generators.graph_export import build_graph_export, write_graph_export


def _seed(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO scan_runs (id, commit_sha, branch, project_slug, started_at, status) "
        "VALUES (1, 'abc123', 'main', 'demo', '2026-01-01', 'completed')"
    )
    conn.execute(
        "INSERT INTO code_nodes (id, scan_id, file_path, language, node_type, name, "
        "qualified_name, line_start, line_end, domain, file_hash) "
        "VALUES (1, 1, 'src/orders.py', 'python', 'function', 'create', "
        "'orders.create', 1, 5, 'orders', 'h1')"
    )
    conn.execute(
        "INSERT INTO code_nodes (id, scan_id, file_path, language, node_type, name, "
        "qualified_name, line_start, line_end, domain, file_hash) "
        "VALUES (2, 1, 'src/repo.py', 'python', 'function', 'save', "
        "'repo.save', 1, 5, 'orders', 'h2')"
    )
    conn.execute(
        "INSERT INTO call_edges (scan_id, caller_id, callee_id, callee_name, edge_type, "
        "confidence, resolved_by) VALUES (1, 1, 2, 'repo.save', 'direct_call', 0.95, 'import_scope')"
    )
    conn.execute(
        "INSERT INTO domains (scan_id, name, node_count, entry_points, tech_stack) "
        "VALUES (1, 'orders', 2, '[\"orders.create\"]', '[\"python\"]')"
    )
    conn.execute(
        "INSERT INTO db_relationship (scan_id, from_entity, to_entity, from_field, "
        "to_field, cardinality, source, confidence, inferred) "
        "VALUES (1, 'orders', 'customers', 'customer_id', 'id', 'N:1', 'sql', 1.0, 0)"
    )
    conn.execute(
        "INSERT INTO generated_docs (scan_id, domain, doc_type, doc_id, title, content_md, "
        "confidence, unverified_claims, verified_row_count, created_at) "
        "VALUES (1, 'orders', 'as-is', 'demo-orders-asis', 'Orders AS-IS', '# body', "
        "0.9, 1, 12, '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO screen_specs (scan_id, screen_id, spec_json, confidence, created_at) "
        "VALUES (1, 'order-list', '{}', 0.8, '2026-01-01')"
    )
    conn.commit()
    conn.close()


@pytest.fixture
def seeded_db(tmp_path):
    db = tmp_path / "discovery-demo.db"
    init_db(db)
    _seed(db)
    return db


def test_export_shape_and_content(seeded_db):
    graph = build_graph_export(seeded_db, "demo")

    assert graph["schema_version"] == 1
    assert graph["project"] == "demo"
    assert graph["scan"]["commit_sha"] == "abc123"

    nodes = {n["id"]: n for n in graph["nodes"]}
    assert set(nodes) == {"orders.create", "repo.save"}
    assert nodes["orders.create"]["domain"] == "orders"
    assert nodes["orders.create"]["file_hash"] == "h1"
    # content stays out of the export — it's an index/topology view, not a dump
    assert "source_code" not in nodes["orders.create"]

    (edge,) = graph["edges"]
    assert edge["caller"] == "orders.create"
    assert edge["callee"] == "repo.save"
    assert edge["confidence"] == 0.95
    assert edge["resolved_by"] == "import_scope"  # resolution-stage provenance

    (domain,) = graph["domains"]
    assert domain["name"] == "orders"
    assert domain["entry_points"] == ["orders.create"]

    (rel,) = graph["relationships"]
    assert (rel["from_entity"], rel["to_entity"], rel["cardinality"]) == ("orders", "customers", "N:1")

    (doc,) = graph["docs"]
    assert doc["doc_id"] == "demo-orders-asis"
    assert doc["confidence"] == 0.9
    assert doc["verified_row_count"] == 12
    assert "content_md" not in doc  # index only

    (screen,) = graph["screens"]
    assert screen == {"screen_id": "order-list", "confidence": 0.8}

    assert graph["fsms"] == []  # none seeded


def test_export_uses_latest_scan(seeded_db):
    conn = sqlite3.connect(str(seeded_db))
    conn.execute(
        "INSERT INTO scan_runs (id, commit_sha, started_at, status) "
        "VALUES (2, 'def456', '2026-02-01', 'completed')"
    )
    conn.execute(
        "INSERT INTO code_nodes (scan_id, file_path, language, node_type, name, "
        "qualified_name) VALUES (2, 'src/new.py', 'python', 'function', 'f', 'new.f')"
    )
    conn.commit(); conn.close()

    graph = build_graph_export(seeded_db, "demo")
    assert graph["scan"]["commit_sha"] == "def456"
    assert [n["id"] for n in graph["nodes"]] == ["new.f"]


def test_export_no_scan_raises(tmp_path):
    db = tmp_path / "empty.db"
    init_db(db)
    with pytest.raises(ValueError, match="[Nn]o scan"):
        build_graph_export(db, "demo")


def test_write_graph_export_round_trips(seeded_db, tmp_path):
    out = tmp_path / "kg.json"
    path = write_graph_export(seeded_db, "demo", out)
    assert path == out
    data = json.loads(out.read_text())
    assert data["project"] == "demo"
    assert data["edges"][0]["resolved_by"] == "import_scope"


def test_call_edges_has_resolved_by_column(tmp_path):
    """Migration v14: resolution-stage provenance persists to the DB so the
    export (and triage) can carry it."""
    db = tmp_path / "d.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(call_edges)").fetchall()}
    conn.close()
    assert "resolved_by" in cols
