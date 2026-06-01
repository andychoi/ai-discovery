"""Tests for the dependency & interface catalog (gap-assessment #7)."""

import json
import sqlite3
import tempfile
from pathlib import Path

from ai_discovery.db import get_conn, init_db, persist_relationships
from ai_discovery.generators.dependency_catalog import (
    render_dependency_catalog, write_dependency_catalog,
)
from ai_discovery.graph.models import EntityRelationship


def _seed(db_path):
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO scan_runs (id, started_at, status) VALUES (1, '2026-01-01', 'running')")
    # external systems (code + infra) and endpoints (ast + openapi)
    for name, hints, ntype in [
        ("Redis", {"kind": "cache", "source": "code"}, "external_system"),
        ("PostgreSQL", {"kind": "database", "source": "infra"}, "external_system"),
        ("getOrder", {"method": "GET", "route": "/orders/{id}", "source": "ast"}, "endpoint"),
        ("listPets", {"method": "GET", "route": "/pets", "source": "openapi"}, "endpoint"),
    ]:
        qn = f"{ntype}::{name}"
        conn.execute(
            "INSERT INTO code_nodes (scan_id, file_path, language, node_type, name, "
            "qualified_name, line_start, line_end, source_code, framework_hints) "
            "VALUES (1, '', 'x', ?, ?, ?, 0, 0, '', ?)",
            (ntype, name, qn, json.dumps(hints)),
        )
    conn.commit(); conn.close()
    persist_relationships(db_path, 1, [
        EntityRelationship("order_items", "orders", "order_id", "id", "N:1", "sql", "s.sql", 3),
    ])


def test_catalog_inventories_systems_relationships_and_api():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "d.db"
        _seed(db)
        md = render_dependency_catalog(db, 1, "proj")
        assert "## External Systems" in md
        assert "Redis" in md and "PostgreSQL" in md
        assert "declared in infra" in md            # provenance shown
        assert "## Entity Relationships" in md
        assert "order_items" in md and "orders" in md
        assert "Inbound endpoints: **2**" in md      # ast + openapi counted


def test_write_creates_interfaces_doc():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "d.db"
        _seed(db)
        docs = Path(tmp) / "docs"
        path = write_dependency_catalog(db, 1, "proj", docs)
        assert path is not None and path.exists()
        assert path.parent.name == "INTERFACES"
        assert "doc_id: proj-dependencies" in path.read_text()


def test_empty_scan_yields_no_catalog():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "d.db"
        init_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO scan_runs (id, started_at, status) VALUES (1, '2026-01-01', 'running')")
        conn.commit(); conn.close()
        assert render_dependency_catalog(db, 1, "proj") == ""
        assert write_dependency_catalog(db, 1, "proj", Path(tmp) / "docs") is None
