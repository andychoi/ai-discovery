"""SQL via sqlglot (roadmap item M6.8): literals, statement facts, table access edges, FKs, fallback."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.extractors.sql import parse_sql, sql_literals
from ai_discovery.extractors.sql_extractor import (extract_sql_entities, extract_sql_relationships,
                                                   extract_table_access, read_sql_file_nodes)
from ai_discovery.graph.models import CodeNode


def _node(src: str, lang: str = "csharp", qn: str = "Repo.Run", node_type: str = "method") -> CodeNode:
    return CodeNode(file_path="Repo.cs", language=lang, node_type=node_type, name=qn.rsplit(".", 1)[-1],
                    qualified_name=qn, source_code=src, line_start=1, line_end=10)


def test_literals_concatenation_interpolation_and_prose() -> None:
    src = ('var a = "SELECT o.Id FROM Orders o " + "WHERE o.Id = @id";\n'
           'var b = $"DELETE FROM {table} WHERE Id = 1";\n'
           'var c = "SELECT * FROM " + tableName + " WHERE x = 1";\n'
           'var d = "Create a catalog item"; var e = "Select a product from the list";\n'
           'var f = @"UPDATE [dbo].[Orders]\n   SET [Status] = 1";\n')
    lits = sql_literals(src)
    assert lits[0] == "SELECT o.Id FROM Orders o WHERE o.Id = @id"
    assert "__dyn__" in lits[1] and "__dyn__" in lits[2] and len(lits) == 4
    assert lits[3].startswith("UPDATE [dbo].[Orders]")


def test_jpql_is_not_sql() -> None:
    java = ('@Query("SELECT p FROM PetType p ORDER BY p.name") List<PetType> a();\n'
            '@Query(value = "SELECT * FROM types", nativeQuery = true) List<PetType> b();\n'
            'em.createQuery("SELECT o FROM Owner o");\n')
    assert sql_literals(java) == ["SELECT * FROM types"]


def test_statement_facts() -> None:
    facts = parse_sql("WITH x AS (SELECT * FROM a) INSERT INTO b (id, n) SELECT * FROM x;\n"
                      "UPDATE o SET o.Status = 1 FROM dbo.Orders o JOIN Customers c ON c.Id = o.CId;\n"
                      "MERGE INTO Stock t USING Incoming s ON t.Id = s.Id WHEN MATCHED THEN UPDATE SET t.Qty = s.Qty;\n"
                      "CREATE VIEW v AS SELECT * FROM a JOIN c ON 1 = 1", ("tsql",))
    got = [(f.kind, sorted(f.reads), sorted(f.writes)) for f in facts]
    assert got == [("insert", ["a"], ["b"]), ("update", ["Customers"], ["Orders"]),
                   ("merge", ["Incoming"], ["Stock"]), ("create", ["a", "c"], [])]
    assert facts[0].columns == {"b": {"id", "n"}} and facts[3].view == "v"


def test_table_access_edges_and_regex_fallback() -> None:
    nodes = [_node('db.Execute("UPDATE Orders SET Status = @s WHERE Id = @id");\n'
                   'db.Query("SELECT c.Name FROM Customers c JOIN Orders o ON o.CustomerId = c.Id");\n'),
             _node('db.Execute($"DELETE FROM {tbl} WHERE Id = 1");', qn="Repo.Purge"),
             _node('db.Query("SELECT TOP 5 * FROM Orders WHERE Id IN (SELECT FROM WHERE)");', qn="Repo.Broken"),
             _node('void Run() { db.Execute("UPDATE Orders SET Status = 1"); }', qn="Repo", node_type="class")]
    tables, edges, stats = extract_table_access(nodes)
    got = {(e.caller, e.callee, e.metadata["edge_kind"], e.metadata["resolved_by"]) for e in edges}
    assert ("Repo.Run", "sql::Orders", "writes_table", "sql_parsed") in got
    assert ("Repo.Run", "sql::Customers", "reads_table", "sql_parsed") in got
    assert ("Repo.Run", "sql::Orders", "reads_table", "sql_parsed") in got
    assert not [g for g in got if g[0] == "Repo.Purge"] and stats["sql_dynamic_table"] == 1
    assert ("Repo.Broken", "sql::Orders", "reads_table", "sql_regex") in got and stats["sql_regex_fallback"] == 1
    assert not [g for g in got if g[0] == "Repo"]                       # a class is not a caller
    assert {t.qualified_name for t in tables} == {"sql::Orders", "sql::Customers"}
    assert {e.edge_type for e in edges} == {"reads_table", "writes_table"}


def test_tsql_alter_and_bracketed_constraints_give_relationships() -> None:
    src = ('migrationBuilder.Sql(@"CREATE TABLE [dbo].[Orders] ([Id] INT NOT NULL, [CustomerId] INT NOT NULL);\n'
           'ALTER TABLE [dbo].[Orders] WITH CHECK ADD CONSTRAINT [FK_O_C] FOREIGN KEY([CustomerId]) '
           'REFERENCES [dbo].[Customers] ([Id]);");\n'
           'migrationBuilder.Sql("CREATE TABLE `order_items` (`id` int, `order_id` int, CONSTRAINT `fk` FOREIGN KEY '
           '(`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE)");')
    rels = {(r.from_entity, r.to_entity, r.from_field, r.to_field) for r in extract_sql_relationships([_node(src)])}
    assert ("Orders", "Customers", "CustomerId", "Id") in rels
    assert ("order_items", "orders", "order_id", "id") in rels
    entities = {n.name: set(n.fields) for n in extract_sql_entities([_node(src)])}
    assert entities["Orders"] >= {"Id", "CustomerId"}


def test_sql_files_skip_check_is_repo_relative(tmp_path) -> None:
    root = tmp_path / "build" / "repo"
    for rel in ("db/migrations/001.sql", "node_modules/x/y.sql", "dist/z.sql"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("CREATE TABLE t (id int);")
    assert [Path(n.file_path).relative_to(root).as_posix() for n in read_sql_file_nodes(root)] == ["db/migrations/001.sql"]
