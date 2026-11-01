"""Statement facts from SQL text with sqlglot (roadmap item M6.8).

`parse_sql(text, dialects)` splits *text* into statements and returns one `StatementFacts` per statement: the
tables it reads and writes, the columns it names per table, and the foreign keys it declares (`CREATE TABLE`
constraints, inline `REFERENCES`, `ALTER TABLE … ADD … FOREIGN KEY`). Dialects are tried in order; a statement
no dialect can parse comes back with ``ok=False`` and its raw text, for the regex fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

try:
    import sqlglot
    from sqlglot import exp
except ImportError:  # pragma: no cover - declared dependency; the regex path still works without it
    sqlglot = None  # type: ignore[assignment]
    exp = None  # type: ignore[assignment]

from .literals import DYN

import logging as _logging

# sqlglot warns per unsupported statement; the regex fallback already counts those (degradation sql_regex_fallback)
_logging.getLogger("sqlglot").setLevel(_logging.ERROR)

DIALECTS = ("tsql", "postgres", "mysql", "oracle")
WRITE_KINDS = {"Insert": "insert", "Update": "update", "Delete": "delete", "Merge": "merge"}
DDL_KINDS = {"Create": "create", "Alter": "alter", "Drop": "drop"}
# procedural blocks swallow the statements after them (`IF … THROW …; INSERT …` parses as one IfBlock):
# never trust them for table facts — the statement goes to the regex fallback instead
_PROCEDURAL = frozenset({"IfBlock", "WhileBlock", "Block"})
_GO = re.compile(r"^\s*GO\s*;?\s*$", re.I | re.M)


@dataclass
class StatementFacts:
    kind: str                                   # select | insert | update | delete | merge | create | alter | …
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    columns: dict[str, set[str]] = field(default_factory=dict)
    fks: list[tuple[str, str, list[str], list[str]]] = field(default_factory=list)   # (from, to, cols, ref cols)
    view: Optional[str] = None
    dynamic: bool = False                       # a table name was interpolated (skipped)
    ok: bool = True
    text: str = ""


def _name(table) -> str:
    """`[dbo].[Orders]` / `"SHOP"."ORDERS"` / `orders` → the bare table name; '' for a computed one."""
    if table is None:
        return ""
    if isinstance(table, exp.Schema):
        table = table.this
    name = table.name if isinstance(table, exp.Table) else getattr(table, "name", "")
    return "" if not name or DYN in name else name


def _statements(text: str) -> list[str]:
    text = _GO.sub(";", text)
    return [s.strip() for s in re.split(r";\s*(?:\n|$)", text) if s.strip()]


def parse_sql(text: str, dialects: Iterable[str] = DIALECTS) -> list[StatementFacts]:
    dialects = tuple(dict.fromkeys(dialects))
    text = _GO.sub(";", text)
    if sqlglot is not None:   # whole text first: sqlglot splits statements itself
        for d in dialects:
            try:
                trees = [t for t in sqlglot.parse(text, read=d) if t is not None]
            except Exception:
                continue
            if trees and not any(isinstance(t, exp.Command) or type(t).__name__ in _PROCEDURAL for t in trees):
                return [_facts(t, t.sql(dialect=d)) for t in trees]
    out: list[StatementFacts] = []
    for stmt_text in _statements(text):
        tree = None
        if sqlglot is not None:
            for d in dialects:
                try:
                    trees = sqlglot.parse(stmt_text, read=d)
                except Exception:
                    continue
                if trees and trees[0] is not None and not isinstance(trees[0], exp.Command) \
                        and type(trees[0]).__name__ not in _PROCEDURAL:
                    tree = trees[0]
                    break
        if tree is None:
            out.append(StatementFacts(kind="unknown", ok=False, text=stmt_text))
            continue
        out.append(_facts(tree, stmt_text))
    return out


def _facts(tree, text: str) -> StatementFacts:
    kind_name = type(tree).__name__
    kind = WRITE_KINDS.get(kind_name) or DDL_KINDS.get(kind_name) or kind_name.lower()
    f = StatementFacts(kind=kind, text=text)
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    tables = [t for t in tree.find_all(exp.Table) if t.name]
    f.dynamic = any(DYN in (t.name or "") for t in tables)
    aliases = {t.alias: t.name for t in tables if t.alias}
    target = None
    if kind in ("insert", "update", "delete", "merge", "create", "alter", "drop"):
        target = tree.this
        if isinstance(target, exp.Schema):
            target = target.this
        tname = _name(target)
        tname = aliases.get(tname, tname)        # `UPDATE o SET … FROM dbo.Orders o`
        if kind in WRITE_KINDS.values() and tname:
            f.writes.add(tname)
        if kind == "create" and isinstance(tree, exp.Create) and (tree.args.get("kind") or "").upper() == "VIEW":
            f.view = tname
    written_node = target if isinstance(target, exp.Table) else None
    for t in tables:
        n = _name(t)
        if not n or n in ctes or n in aliases and t.alias != n and False:
            continue
        if t is written_node or (kind in WRITE_KINDS.values() and n in f.writes and t.find_ancestor(exp.Join) is None
                                 and t.parent is tree):
            continue
        if kind in ("select", "insert", "update", "delete", "merge", "union", "with") or kind == "create" and f.view:
            if n not in f.writes and n not in aliases:
                f.reads.add(n)
    # columns per table: CREATE column defs, INSERT column list, UPDATE SET targets
    if kind == "create" and isinstance(tree.this, exp.Schema) and not f.view:
        tname = _name(tree.this)
        cols = {c.name for c in tree.this.expressions if isinstance(c, exp.ColumnDef) and c.name}
        if tname and cols:
            f.columns[tname] = cols
    if kind == "insert" and isinstance(tree.this, exp.Schema):
        tname = _name(tree.this)
        cols = {c.name for c in tree.this.expressions if getattr(c, "name", None)}
        if tname and cols:
            f.columns[tname] = cols
    if kind == "update":
        tname = next(iter(f.writes), "")
        cols = {e.this.name for e in tree.expressions if isinstance(e, exp.EQ) and isinstance(e.this, exp.Column)}
        if tname and cols:
            f.columns[tname] = cols
    # foreign keys: table constraints, inline column REFERENCES, ALTER TABLE ADD CONSTRAINT
    if kind in ("create", "alter"):
        owner = _name(tree.this)
        for fk in tree.find_all(exp.ForeignKey):
            ref = fk.args.get("reference")
            ref_schema = ref.this if ref is not None else None
            to = _name(ref_schema)
            cols = [c.name for c in fk.expressions]
            ref_cols = [c.name for c in ref_schema.expressions] if isinstance(ref_schema, exp.Schema) else []
            if owner and to:
                f.fks.append((owner, to, cols, ref_cols))
        for col in tree.find_all(exp.ColumnDef):
            for cons in col.args.get("constraints") or []:
                ref = cons.args.get("kind")
                if isinstance(ref, exp.Reference):
                    to = _name(ref.this)
                    ref_cols = [c.name for c in ref.this.expressions] if isinstance(ref.this, exp.Schema) else []
                    if owner and to:
                        f.fks.append((owner, to, [col.name], ref_cols))
    return f


def dialects_for(language: str, text: str = "") -> tuple[str, ...]:
    """Dialect order for a source: T-SQL first for .NET code and bracketed SQL, MySQL for backticks."""
    if "`" in text:
        first = "mysql"
    elif language in ("csharp", "vbnet") or re.search(r"\[\w+\]\.\[", text):
        first = "tsql"
    elif re.search(r"\bVARCHAR2\b|\bNUMBER\s*\(", text, re.I):
        first = "oracle"
    else:
        first = "postgres"
    return (first,) + tuple(d for d in DIALECTS if d != first)
