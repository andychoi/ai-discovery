"""Stored procedures (roadmap item M6.10).

- **Procedure nodes** from `CREATE [OR ALTER | OR REPLACE] PROC | PROCEDURE | FUNCTION name` in `.sql` files, keyed
  `sqlproc::<name>` with schema, brackets and case removed (T-SQL compares procedure names case-insensitively).
- **Procedure → table**: the body (after `AS`/`IS`) is handed to the table-access pass as a `sql_statement`;
  statements sqlglot parses give `sql_parsed`, procedural T-SQL / PL-SQL falls back to the keyword regex
  (`sql_regex`).
- **Procedure → procedure**: `EXEC[UTE] name` (dynamic `EXEC (@sql)` / `sp_executesql` are not names).
- **Code → procedure**: a callable whose source sets `CommandType.StoredProcedure` (ADO.NET, Dapper `commandType:`)
  names the procedure in a bare-identifier literal; JDBC escapes `{call x(?)}` / `{? = call x}`; Spring
  `withProcedureName("x")` / `withFunctionName("x")`; `EXEC x` literals; MyBatis `statementType="CALLABLE"`.
  Edges use origin `proc_call` (edge kind `calls_proc`).
"""

from __future__ import annotations

import re

from ...graph.models import CallEdge, CodeNode

_CREATE_PROC = re.compile(r"\bCREATE\s+(?:OR\s+(?:ALTER|REPLACE)\s+)?(?:PROC(?:EDURE)?|FUNCTION)\s+([\[\]\"`\w.]+)",
                          re.I)
_BODY_START = re.compile(r"\b(?:AS|IS)\b", re.I)
_GO = re.compile(r"^\s*GO\s*;?\s*$", re.I | re.M)
_EXEC = re.compile(r"\bEXEC(?:UTE)?\s+(?:@\w+\s*=\s*)?([\[\]\"`\w.]+)", re.I)
_CALL_ESCAPE = re.compile(r"\{\s*(?:\?\s*=\s*)?call\s+([\[\]\"`\w.]+)", re.I)
_SPRING_CALL = re.compile(r"\bwith(?:Procedure|Function)Name\s*\(\s*\"([\w.]+)\"")
_STORED_PROC_FLAG = re.compile(r"CommandType\s*\.\s*StoredProcedure")
_BARE_NAME_LITERAL = re.compile(r"\"(\[?[A-Za-z_][\w]*\]?(?:\.\[?[A-Za-z_][\w]*\]?){0,2})\"")
_DYNAMIC = frozenset({"sp_executesql", "sp_execute"})
# procedural framing lines become statement breaks, so the DML between them parses as plain SQL
_PROC_FRAME = re.compile(r"^\s*(?:BEGIN(?:\s+TRAN(?:SACTION)?)?|END|COMMIT(?:\s+TRAN(?:SACTION)?)?|"
                         r"SET\s+NOCOUNT\s+(?:ON|OFF))\s*;?\s*$", re.I | re.M)


def proc_key(name: str) -> str:
    """`[dbo].[usp_PlaceOrder]` / `billing.CloseInvoice` → `usp_placeorder` / `closeinvoice`."""
    return re.sub(r"[\[\]\"`]", "", name).rsplit(".", 1)[-1].strip().lower()


def procedures_in(text: str) -> list[tuple[str, str, int]]:
    """(declared name, body, 1-based line) for each procedure / function defined in SQL *text*."""
    out = []
    matches = list(_CREATE_PROC.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        go = _GO.search(text, m.end(), end)
        chunk = text[m.end(): go.start() if go else end]
        body_at = _BODY_START.search(chunk)
        out.append((m.group(1), chunk[body_at.end():] if body_at else chunk, text.count("\n", 0, m.start()) + 1))
    return out


def exec_targets(sql: str) -> list[str]:
    return [n for n in (m.group(1) for m in _EXEC.finditer(sql)) if proc_key(n) not in _DYNAMIC and not n.startswith("(")]


def code_proc_targets(source: str) -> list[str]:
    """Procedure names a piece of application code calls."""
    names = [m.group(1) for m in _CALL_ESCAPE.finditer(source)] + _SPRING_CALL.findall(source)
    if _STORED_PROC_FLAG.search(source):
        names += _BARE_NAME_LITERAL.findall(source)
    from .literals import sql_literals
    for lit in sql_literals(source) + re.findall(r"\"\s*(EXEC(?:UTE)?\s+[^\"]+)\"", source, re.I):
        names += exec_targets(lit)
    return list(dict.fromkeys(names))


def extract_procedures(sql_files: list[CodeNode], code_nodes: list[CodeNode],
                       statements: list[CodeNode] = ()) -> tuple[list[CodeNode], list[CallEdge], dict]:
    """(procedure + table nodes, edges, stats) — see module docstring."""
    from ...graph.call_graph import table_edge
    from ..sql_extractor import _NOT_CALLERS, extract_table_access

    procs: dict[str, CodeNode] = {}
    bodies: list[CodeNode] = []
    for f in sql_files:
        for name, body, line in procedures_in(f.source_code or ""):
            key = proc_key(name)
            qn = f"sqlproc::{key}"
            node = CodeNode(file_path=f.file_path, language="sql", node_type="stored_procedure",
                            name=re.sub(r"[\[\]\"`]", "", name), qualified_name=qn, source_code=body.strip(),
                            line_start=line, line_end=line + body.count("\n"),
                            framework_hints={"defined": True, "procedure": key})
            procs.setdefault(qn, node)
            bodies.append(CodeNode(file_path=f.file_path, language="sql", node_type="sql_statement", name=key,
                                   qualified_name=qn, source_code=_PROC_FRAME.sub(";", body), line_start=line,
                                   line_end=node.line_end))

    edges: list[CallEdge] = []
    seen: set[tuple[str, str]] = set()

    def _call(caller: str, target: str, file_path: str, line: int) -> None:
        key = proc_key(target)
        if not key or key in _DYNAMIC:
            return
        qn = f"sqlproc::{key}"
        if qn not in procs:   # called but not defined in the repo: the procedure lives only in the database
            procs[qn] = CodeNode(file_path=file_path, language="sql", node_type="stored_procedure",
                                 name=re.sub(r"[\[\]\"`]", "", target), qualified_name=qn, source_code="",
                                 line_start=line, line_end=line, framework_hints={"defined": False, "procedure": key})
        if (caller, qn) not in seen and caller != qn:
            seen.add((caller, qn))
            edge = table_edge(caller, qn, "proc_call", write=False)
            edge.edge_type = edge.metadata["edge_kind"] = "calls_proc"
            edges.append(edge)

    for b in bodies:                                    # procedure → procedure
        for target in exec_targets(b.source_code):
            _call(b.qualified_name, target, b.file_path, b.line_start)
    for n in code_nodes:                                # code → procedure
        if n.language == "sql" or n.node_type in _NOT_CALLERS or not n.source_code:
            continue
        for target in code_proc_targets(n.source_code):
            _call(n.qualified_name, target, n.file_path, n.line_start)
    for s in statements:                                # MyBatis statementType="CALLABLE" / {call x}
        for m in _CALL_ESCAPE.finditer(s.source_code or ""):
            _call(s.qualified_name, m.group(1), s.file_path, s.line_start)
    tables, table_edges, stats = extract_table_access(bodies)   # procedure → table
    return list(procs.values()) + tables, edges + table_edges, stats
