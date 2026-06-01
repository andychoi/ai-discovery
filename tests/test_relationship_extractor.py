"""Tests for FK-aware table docs Phase 1–2: relationship extraction + storage.

Covers SQL FOREIGN KEY/inline REFERENCES, JPA association extraction via the
Java parser, the cross-source coordinator, .sql file reading (incl. migrations),
and db_relationship persistence round-trip.
"""

import tempfile
from pathlib import Path

from ai_discovery.db import get_relationships, init_db, persist_relationships
from ai_discovery.extractors import (
    extract_relationships,
    extract_sql_relationships,
    read_sql_file_nodes,
)
from ai_discovery.graph.models import CodeNode, EntityRelationship
from ai_discovery.parsers.java import JavaParser


def _sql_node(sql: str) -> CodeNode:
    return CodeNode(
        file_path="schema.sql", language="sql", node_type="sql_source",
        name="schema.sql", qualified_name="sqlfile::schema.sql",
        source_code=sql, line_start=1, line_end=sql.count("\n") + 1,
    )


# ---------------------------------------------------------------------------
# SQL FK extraction
# ---------------------------------------------------------------------------

def test_sql_table_level_foreign_key():
    sql = """
    CREATE TABLE order_items (
        id INT PRIMARY KEY,
        order_id INT,
        product_id INT,
        FOREIGN KEY (order_id) REFERENCES orders(id)
    );
    """
    rels = extract_sql_relationships([_sql_node(sql)])
    assert any(
        r.from_entity == "order_items" and r.to_entity == "orders"
        and r.from_field == "order_id" and r.to_field == "id"
        and r.cardinality == "N:1" and r.source == "sql" and not r.inferred
        for r in rels
    )


def test_sql_inline_reference():
    sql = """
    CREATE TABLE orders (
        id INT PRIMARY KEY,
        customer_id INT REFERENCES customers(id)
    );
    """
    rels = extract_sql_relationships([_sql_node(sql)])
    assert any(
        r.from_entity == "orders" and r.to_entity == "customers"
        and r.from_field == "customer_id" and r.to_field == "id"
        for r in rels
    )


def test_sql_constraint_prefixed_fk():
    sql = """
    CREATE TABLE line_items (
        id INT,
        order_id INT,
        CONSTRAINT fk_order FOREIGN KEY (order_id) REFERENCES orders(id)
    );
    """
    rels = extract_sql_relationships([_sql_node(sql)])
    assert any(r.from_entity == "line_items" and r.to_entity == "orders" for r in rels)


def test_sql_no_false_relationship_without_references():
    sql = "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(50));"
    assert extract_sql_relationships([_sql_node(sql)]) == []


# ---------------------------------------------------------------------------
# JPA extraction via the Java parser + coordinator
# ---------------------------------------------------------------------------

def test_jpa_relationships_extracted_and_resolved_to_table_names():
    parser = JavaParser()
    nodes = []
    entity_dir = Path("tests/fixtures/projects/spring-boot-app/src/main/java/com/example/entity")
    for f in entity_dir.glob("*.java"):
        nodes += parser.parse_file(f)

    rels = extract_relationships(nodes)
    edges = {(r.from_entity, r.to_entity, r.cardinality) for r in rels}
    # Target class names (Customer, OrderItem, Product) resolve to table names.
    assert ("orders", "customers", "N:1") in edges
    assert ("orders", "order_items", "1:N") in edges
    assert ("order_items", "products", "N:1") in edges
    assert all(r.source == "jpa" and not r.inferred for r in rels)


def test_coordinator_dedupes_across_sources():
    # Same edge from SQL and from a stamped ORM node → one row.
    sql = "CREATE TABLE orders (id INT, customer_id INT REFERENCES customers(id));"
    orm_node = CodeNode(
        file_path="Order.java", language="java", node_type="db_model",
        name="Order", qualified_name="com.example.Order", source_code="",
        line_start=1, line_end=1,
        framework_hints={"table": "orders", "relationships": [
            {"from_field": "customer_id", "to_entity": "Customer", "cardinality": "N:1"},
        ]},
    )
    customer_node = CodeNode(
        file_path="Customer.java", language="java", node_type="db_model",
        name="Customer", qualified_name="com.example.Customer", source_code="",
        line_start=1, line_end=1, framework_hints={"table": "customers"},
    )
    rels = extract_relationships([_sql_node(sql), orm_node, customer_node])
    matches = [r for r in rels if r.from_entity == "orders" and r.to_entity == "customers"
               and r.from_field == "customer_id"]
    assert len(matches) == 1  # deduped by (from, from_field, to, to_field)


# ---------------------------------------------------------------------------
# .sql file reading (incl. migrations/, which file_walker skips)
# ---------------------------------------------------------------------------

def test_read_sql_file_nodes_includes_migrations_skips_vendor():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "migrations").mkdir()
        (root / "migrations" / "001_init.sql").write_text(
            "CREATE TABLE orders (id INT, customer_id INT REFERENCES customers(id));"
        )
        (root / "node_modules").mkdir()
        (root / "node_modules" / "junk.sql").write_text("CREATE TABLE junk (id INT);")

        nodes = read_sql_file_nodes(root)
        names = {n.name for n in nodes}
        assert "001_init.sql" in names          # migrations are read
        assert "junk.sql" not in names           # vendor dirs skipped

        rels = extract_sql_relationships(nodes)
        assert any(r.from_entity == "orders" and r.to_entity == "customers" for r in rels)


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

def test_persist_and_get_relationships_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "d.db"
        init_db(db_path)
        conn = __import__("sqlite3").connect(str(db_path))
        conn.execute(
            "INSERT INTO scan_runs (id, started_at, status) VALUES (1, '2026-01-01', 'running')"
        )
        conn.commit()
        conn.close()

        rels = [
            EntityRelationship("order_items", "orders", "order_id", "id", "N:1", "sql", "s.sql", 3),
            EntityRelationship("order_items", "products", "product_id", "id", "N:1", "sql", "s.sql", 4),
        ]
        n = persist_relationships(db_path, 1, rels)
        assert n == 2

        got = get_relationships(db_path, 1)
        assert {(r.from_entity, r.to_entity) for r in got} == {
            ("order_items", "orders"), ("order_items", "products")
        }
        # Filter by source entity.
        only = get_relationships(db_path, 1, from_entity="order_items")
        assert len(only) == 2
        # Re-persist replaces (idempotent), not appends.
        assert persist_relationships(db_path, 1, rels[:1]) == 1
        assert len(get_relationships(db_path, 1)) == 1
