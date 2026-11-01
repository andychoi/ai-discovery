"""Stored procedures (roadmap item M6.10)."""

from __future__ import annotations

from ai_discovery.extractors.sql.procs import code_proc_targets, extract_procedures, procedures_in, proc_key
from ai_discovery.graph.models import CodeNode


def _code(src: str, qn: str = "Repo.Run", lang: str = "csharp") -> CodeNode:
    return CodeNode(file_path="Repo.cs", language=lang, node_type="method", name=qn.rsplit(".", 1)[-1],
                    qualified_name=qn, source_code=src, line_start=1, line_end=5)


def _sql(text: str) -> CodeNode:
    return CodeNode(file_path="db/p.sql", language="sql", node_type="sql_source", name="p.sql",
                    qualified_name="sqlfile::p.sql", source_code=text, line_start=1, line_end=text.count("\n") + 1)


def test_procedure_definitions_across_dialects() -> None:
    text = ("CREATE PROCEDURE [dbo].[usp_A] @x INT AS BEGIN SELECT 1 FROM T END\nGO\n"
            "CREATE OR REPLACE PROCEDURE shop.archive_orders(p_days IN NUMBER) IS\nBEGIN\n"
            "  DELETE FROM orders WHERE created < SYSDATE - p_days;\nEND;\n/\n"
            "create or alter proc dbo.usp_B as update T set x = 1\n")
    assert [(proc_key(n), line) for n, _b, line in procedures_in(text)] == [
        ("usp_a", 1), ("archive_orders", 3), ("usp_b", 8)]


def test_code_call_forms() -> None:
    assert code_proc_targets('var cmd = new SqlCommand("dbo.usp_Get", c); cmd.CommandType = CommandType.StoredProcedure;') \
        == ["dbo.usp_Get"]
    assert code_proc_targets('conn.prepareCall("{? = call shop.calc_tax(?, ?)}")') == ["shop.calc_tax"]
    assert code_proc_targets('new SimpleJdbcCall(ds).withProcedureName("archive_orders")') == ["archive_orders"]
    assert code_proc_targets('db.Execute("EXEC dbo.usp_Purge @days", p)') == ["dbo.usp_Purge"]
    assert code_proc_targets('db.Execute("EXEC sp_executesql @sql")') == []                   # dynamic, not a name
    assert code_proc_targets('var x = "orders"; db.Query(x);') == []                         # no stored-proc flag


def test_edges_and_undefined_procedures() -> None:
    sql = _sql("CREATE PROCEDURE dbo.usp_Place @c INT AS\nBEGIN\n  INSERT INTO Orders (CustomerId) VALUES (@c);\n"
               "  EXEC dbo.usp_Audit @c;\n  EXEC (@dynamic);\nEND\nGO\n")
    code = _code('using var cmd = new SqlCommand("usp_place", c) { CommandType = CommandType.StoredProcedure };\n'
                 'conn.prepareCall("{call legacy_only(?)}");')
    statement = CodeNode(file_path="m.xml", language="sql", node_type="sql_statement", name="archive",
                         qualified_name="mybatis::shop.M.archive", source_code="{call archive_orders(?)}",
                         line_start=1, line_end=1)
    nodes, edges, _stats = extract_procedures([sql], [code], [statement])
    got = {(e.caller, e.callee, e.metadata["edge_kind"], e.metadata["resolved_by"]) for e in edges}
    assert ("Repo.Run", "sqlproc::usp_place", "calls_proc", "proc_call") in got                # case-insensitive
    assert ("sqlproc::usp_place", "sqlproc::usp_audit", "calls_proc", "proc_call") in got
    assert ("sqlproc::usp_place", "sql::Orders", "writes_table", "sql_parsed") in got
    assert ("mybatis::shop.M.archive", "sqlproc::archive_orders", "calls_proc", "proc_call") in got
    by = {n.qualified_name: n for n in nodes}
    assert by["sqlproc::usp_place"].framework_hints["defined"] is True
    assert by["sqlproc::legacy_only"].framework_hints["defined"] is False                     # lives only in the DB
    assert not [g for g in got if "dynamic" in g[1]]
