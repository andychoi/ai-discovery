"""Tests for Phase 2.5.1 raw-SQL entity extractor."""

from __future__ import annotations

from ai_discovery.extractors.sql_extractor import extract_sql_entities
from ai_discovery.graph.models import CodeNode


def _node(src: str, name: str = "fn", file: str = "a.py") -> CodeNode:
    return CodeNode(
        file_path=file,
        language="python",
        node_type="function",
        name=name,
        qualified_name=f"mod.{name}",
        source_code=src,
        line_start=1,
        line_end=1,
    )


def test_create_table_extracts_columns():
    sql = '''
    """CREATE TABLE orders (
        id INT PRIMARY KEY,
        status VARCHAR(32) NOT NULL,
        total DECIMAL(10, 2)
    );"""
    '''
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    node = out[0]
    assert node.name == "orders"
    assert node.qualified_name == "sql::orders"
    assert node.node_type == "sql_table"
    assert set(node.fields) == {"id", "status", "total"}


def test_create_table_skips_table_constraints():
    """PRIMARY KEY (id), FOREIGN KEY (user_id) — not columns."""
    sql = '''CREATE TABLE items (
        id INT,
        name TEXT,
        user_id INT,
        PRIMARY KEY (id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );'''
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert set(out[0].fields) == {"id", "name", "user_id"}


def test_insert_into_extracts_columns():
    sql = 'conn.execute("INSERT INTO users (id, email, created_at) VALUES (?, ?, ?)")'
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert set(out[0].fields) == {"id", "email", "created_at"}


def test_update_set_extracts_columns():
    sql = '''
    conn.execute("""UPDATE orders SET status = ?, updated_at = ? WHERE id = ?""")
    '''
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert set(out[0].fields) == {"status", "updated_at"}


def test_select_from_extracts_named_columns():
    sql = 'SELECT id, name, email FROM users WHERE active = 1'
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert set(out[0].fields) == {"id", "name", "email"}


def test_select_star_emits_no_fields():
    """SELECT * leaks no column info — synthetic node suppressed."""
    sql = 'SELECT * FROM orders'
    out = extract_sql_entities([_node(sql)])
    assert out == []


def test_multiple_statements_union_columns():
    """Two different statements on the same table merge their column sets."""
    sql = '''
    """CREATE TABLE orders (id INT, status VARCHAR);"""
    """INSERT INTO orders (id, status, total) VALUES (?, ?, ?)"""
    """UPDATE orders SET updated_at = ?"""
    '''
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert set(out[0].fields) == {"id", "status", "total", "updated_at"}
    assert set(out[0].framework_hints["sql_ops"]) == {"CREATE", "INSERT", "UPDATE"}


def test_multiple_nodes_same_table_merged():
    """SQL strings in different functions/files still produce one synthetic node."""
    n1 = _node('INSERT INTO orders (id, status) VALUES (?, ?)', name="f1", file="a.py")
    n2 = _node('UPDATE orders SET total = ?', name="f2", file="b.py")
    out = extract_sql_entities([n1, n2])
    assert len(out) == 1
    assert set(out[0].fields) == {"id", "status", "total"}


def test_schema_prefix_stripped():
    """`public.orders` normalizes to `orders` so schema-qualified + bare names merge."""
    n1 = _node('INSERT INTO public.orders (id, status) VALUES (?, ?)')
    n2 = _node('UPDATE orders SET total = ?', name="g")
    out = extract_sql_entities([n1, n2])
    assert len(out) == 1
    assert out[0].name == "orders"
    assert set(out[0].fields) == {"id", "status", "total"}


def test_quoted_identifiers_normalized():
    """Backticks, double-quotes, and brackets all strip to the same ident."""
    sql = '''
    INSERT INTO `orders` (`id`, `status`) VALUES (?, ?);
    UPDATE "orders" SET "total" = ?;
    '''
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert set(out[0].fields) == {"id", "status", "total"}


def test_aggregate_expressions_dropped():
    """COUNT(*), MAX(x) don't leak as field names."""
    sql = 'SELECT COUNT(*), MAX(total), status FROM orders'
    out = extract_sql_entities([_node(sql)])
    # Only `status` is a real column name.
    assert len(out) == 1
    assert set(out[0].fields) == {"status"}


def test_table_alias_and_column_prefix_stripped():
    sql = 'SELECT o.id, o.status FROM orders o'
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert set(out[0].fields) == {"id", "status"}


def test_ignored_system_tables():
    """sqlite_master / information_schema don't produce entities."""
    sql = '''
    SELECT name FROM sqlite_master WHERE type='table';
    SELECT table_name FROM information_schema.tables;
    '''
    out = extract_sql_entities([_node(sql)])
    assert out == []


def test_alter_table_add_column():
    sql = 'ALTER TABLE orders ADD COLUMN tracking_number VARCHAR(64)'
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert "tracking_number" in out[0].fields
    assert "ALTER" in out[0].framework_hints["sql_ops"]


def test_no_sql_returns_empty():
    out = extract_sql_entities([_node("def foo(): return 1")])
    assert out == []


def test_synthetic_node_language_and_type():
    out = extract_sql_entities([_node('INSERT INTO orders (id) VALUES (?)')])
    assert len(out) == 1
    n = out[0]
    assert n.language == "sql"
    assert n.node_type == "sql_table"
    assert n.qualified_name.startswith("sql::")


# --- T-SQL / MS SQL Server dialect -----------------------------------------

def test_tsql_bracketed_schema_and_table():
    """[dbo].[Orders] normalizes the same way `schema.table` does."""
    sql = 'INSERT INTO [dbo].[Orders] ([Id], [Status]) VALUES (?, ?)'
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert out[0].name == "Orders"
    assert set(out[0].fields) == {"Id", "Status"}


def test_tsql_three_part_name():
    """Fully-qualified `database.schema.table` resolves to the bare table."""
    sql = 'SELECT Id, Status FROM MyDb.dbo.Orders'
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert out[0].name == "Orders"


def test_tsql_select_top_modifier_stripped():
    """SELECT TOP 10 id FROM orders — `TOP`/`10` must not leak as columns."""
    sql = 'SELECT TOP 10 id, status FROM orders'
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert set(out[0].fields) == {"id", "status"}


def test_tsql_select_top_percent_stripped():
    sql = 'SELECT TOP (25) PERCENT id, status FROM orders'
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert set(out[0].fields) == {"id", "status"}


def test_tsql_identity_column():
    """IDENTITY(1,1) inside a column type shouldn't confuse the splitter."""
    sql = '''CREATE TABLE Orders (
        Id INT IDENTITY(1,1) PRIMARY KEY,
        Status NVARCHAR(32),
        Total DECIMAL(10, 2)
    );'''
    out = extract_sql_entities([_node(sql)])
    assert len(out) == 1
    assert set(out[0].fields) == {"Id", "Status", "Total"}


# --- SQL views --------------------------------------------------------------

def test_create_view_extracts_projected_columns():
    sql = 'CREATE VIEW order_summary AS SELECT id, status, total FROM orders'
    out = extract_sql_entities([_node(sql)])
    # Both the view AND the base table are extracted.
    by_name = {n.name: n for n in out}
    assert "order_summary" in by_name
    assert "orders" in by_name
    view = by_name["order_summary"]
    assert view.node_type == "sql_view"
    assert view.qualified_name == "sqlview::order_summary"
    assert set(view.fields) == {"id", "status", "total"}


def test_create_or_replace_materialized_view():
    sql = 'CREATE OR REPLACE MATERIALIZED VIEW order_daily AS SELECT id, status FROM orders'
    out = extract_sql_entities([_node(sql)])
    by_name = {n.name: n for n in out}
    assert by_name["order_daily"].node_type == "sql_view"


def test_view_kind_persists_across_statements():
    """A subsequent SELECT against a view doesn't downgrade it to a table."""
    sql = '''
    CREATE VIEW order_summary AS SELECT id, status FROM orders;
    SELECT id FROM order_summary;
    '''
    out = extract_sql_entities([_node(sql)])
    by_name = {n.name: n for n in out}
    assert by_name["order_summary"].node_type == "sql_view"
