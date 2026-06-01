"""Raw-SQL entity extractor (Phase 2e-1).

Scans parsed CodeNode source for SQL statement literals and emits one
synthetic classless CodeNode per distinct table, with `fields` populated
from the union of columns observed across all statements.

These synthetic nodes flow into the Phase 2d consolidator unchanged:
the classless pass merges `sql::orders` (fields={id, status, total}) into
`Order` / `OrderEntity` if the field set overlaps and stems match; otherwise
it emits a standalone FSM so SQL-first codebases still surface entities.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from ..graph.models import CodeNode, EntityRelationship

# ---------------------------------------------------------------------------
# SQL statement patterns
# ---------------------------------------------------------------------------
# All regexes are case-insensitive and allow whitespace / newlines between
# tokens. Table and column tokens accept backticks, double-quotes, and
# schema.table forms; `_clean_ident` strips quoting and schema qualifiers.
#
# Intentionally conservative: patterns match only the canonical SQL shape.
# When parsing fails (complex expressions, dialect-specific syntax, etc.)
# the extractor silently yields nothing for that statement — better to miss
# a table than to emit garbage into the consolidator's fingerprint.

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([`\"\[\]\w\.]+)\s*\((.+?)\)\s*(?:;|$)",
    re.IGNORECASE | re.DOTALL,
)
_INSERT_INTO_RE = re.compile(
    r"INSERT\s+INTO\s+([`\"\[\]\w\.]+)\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_UPDATE_SET_RE = re.compile(
    r"UPDATE\s+([`\"\[\]\w\.]+)\s+SET\s+(.+?)(?:\s+WHERE\b|\s*;|\s*$)",
    re.IGNORECASE | re.DOTALL,
)
_SELECT_FROM_RE = re.compile(
    r"SELECT\s+(.+?)\s+FROM\s+([`\"\[\]\w\.]+)",
    re.IGNORECASE | re.DOTALL,
)
_ALTER_ADD_COL_RE = re.compile(
    r"ALTER\s+TABLE\s+([`\"\[\]\w\.]+)\s+ADD\s+(?:COLUMN\s+)?([`\"\[\]\w]+)",
    re.IGNORECASE,
)
# CREATE [OR REPLACE] [MATERIALIZED] VIEW … AS SELECT … FROM base_table
_CREATE_VIEW_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?([`\"\[\]\w\.]+)\s+AS\s+SELECT\s+(.+?)\s+FROM\b",
    re.IGNORECASE | re.DOTALL,
)
# T-SQL `SELECT TOP N [PERCENT]` / `SELECT TOP (N)` — strip modifier so we
# don't mistake `TOP` or the count literal for a column name.
_TSQL_TOP_RE = re.compile(
    r"^\s*TOP\s*\(?\s*\d+\s*\)?\s*(?:PERCENT\s+)?",
    re.IGNORECASE,
)

# Reserved table names we refuse to emit a synthetic for. Matches bare table.
_IGNORED_TABLES = {
    "dual",          # Oracle pseudo-table
    "sqlite_master",
    "sqlite_sequence",
}

# Schemas whose tables are always catalog-level; any `<schema>.<table>` match
# here is suppressed regardless of the bare table name.
_IGNORED_SCHEMAS = {
    "information_schema",
    "pg_catalog",
    "sys",
}

# Tokens that look like columns in a SELECT list but aren't field names.
# Includes T-SQL `TOP N` / `PERCENT` modifiers and aggregate names.
_NON_COLUMN_TOKENS = {
    "*",
    "distinct",
    "all",
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "null",
    "true",
    "false",
    "top",
    "percent",
}


@dataclass
class _TableAccumulator:
    """Mutable aggregation state for one table or view, across all statements."""

    table: str
    kind: str = "table"  # "table" | "view"
    fields: set[str] = field(default_factory=set)
    first_file: str = ""
    first_line: int = 0
    ops: set[str] = field(default_factory=set)  # {"CREATE", "INSERT", ...}
    evidence: list[str] = field(default_factory=list)


def extract_sql_entities(nodes: list[CodeNode]) -> list[CodeNode]:
    """Return synthetic classless CodeNodes for tables mentioned in SQL strings.

    One node per distinct table, with fields = union of columns seen across
    all statements touching that table. Nodes use `node_type="sql_table"`
    and a stable `qualified_name` of `sql::{table}` so the consolidator sees
    a single node per table regardless of how many files touched it.
    """
    acc: dict[str, _TableAccumulator] = defaultdict(
        lambda: _TableAccumulator(table="")
    )

    for node in nodes:
        if not node.source_code:
            continue
        for qualified_table, fields, op, kind, snippet in _mine_statements(node.source_code):
            if _is_ignored(qualified_table):
                continue
            table = _strip_schema(qualified_table)
            entry = acc.setdefault(table, _TableAccumulator(table=table))
            # A VIEW declaration upgrades the kind; a later table-op doesn't
            # downgrade it (view kind wins so consumers can filter).
            if kind == "view":
                entry.kind = "view"
            entry.fields.update(fields)
            entry.ops.add(op)
            if not entry.first_file:
                entry.first_file = node.file_path
                entry.first_line = node.line_start
            # Cap evidence to keep synthetic source_code bounded.
            if len(entry.evidence) < 5:
                entry.evidence.append(snippet.strip())

    return [_to_code_node(e) for e in acc.values() if e.fields]


# ---------------------------------------------------------------------------
# Foreign-key relationship extraction (FK-aware table docs, Phase 1)
# ---------------------------------------------------------------------------
# Both table-level `FOREIGN KEY (...) REFERENCES tbl (...)` (also when prefixed
# by `CONSTRAINT name`) and inline `col TYPE REFERENCES tbl(col)`. Multi-column
# FKs are paired positionally.

_FK_CONSTRAINT_RE = re.compile(
    r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+([`\"\[\]\w\.]+)\s*(?:\(([^)]+)\))?",
    re.IGNORECASE,
)
_INLINE_REF_RE = re.compile(
    r"REFERENCES\s+([`\"\[\]\w\.]+)\s*(?:\(([^)]+)\))?",
    re.IGNORECASE,
)


def extract_sql_relationships(nodes: list[CodeNode]) -> list[EntityRelationship]:
    """Return foreign-key edges declared in CREATE TABLE statements.

    Each FK is `N:1` from the table that holds the key to the referenced table
    (confidence 1.0 — these are declared constraints, not guesses). Inline and
    table-level / CONSTRAINT-prefixed forms are both handled; multi-column FKs
    yield one edge per column pair.
    """
    rels: list[EntityRelationship] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _emit(from_t: str, to_t_raw: str, from_c: str, to_c: str, node: CodeNode) -> None:
        to_t = _strip_schema(_clean_qualified(to_t_raw))
        if not from_t or not to_t or _is_ignored(to_t_raw):
            return
        rel = EntityRelationship(
            from_entity=from_t, to_entity=to_t,
            from_field=from_c, to_field=to_c,
            cardinality="N:1", source="sql",
            source_file=node.file_path, source_line=node.line_start,
            confidence=1.0, inferred=False,
        )
        if rel.key() not in seen:
            seen.add(rel.key())
            rels.append(rel)

    for node in nodes:
        if not node.source_code or "REFERENCES" not in node.source_code.upper():
            continue
        for m in _CREATE_TABLE_RE.finditer(node.source_code):
            from_table = _strip_schema(_clean_qualified(m.group(1)))
            if not from_table or _is_ignored(m.group(1)):
                continue
            for piece in _split_top_level(m.group(2), ","):
                piece = piece.strip()
                upper = piece.upper()
                if not piece or "REFERENCES" not in upper:
                    continue
                if "FOREIGN KEY" in upper:
                    fk = _FK_CONSTRAINT_RE.search(piece)
                    if not fk:
                        continue
                    from_cols = [_clean_ident(c) for c in fk.group(1).split(",")]
                    to_cols = [_clean_ident(c) for c in fk.group(3).split(",")] if fk.group(3) else []
                    for i, fc in enumerate(from_cols):
                        _emit(from_table, fk.group(2), fc,
                              to_cols[i] if i < len(to_cols) else "", node)
                elif not upper.startswith(("PRIMARY KEY", "UNIQUE", "CHECK", "INDEX", "KEY ")):
                    # Inline column reference: `customer_id INT REFERENCES customers(id)`.
                    ref = _INLINE_REF_RE.search(piece)
                    if not ref:
                        continue
                    from_col = _clean_ident(piece.split(None, 1)[0])
                    to_col = _clean_ident(ref.group(2).split(",")[0]) if ref.group(2) else ""
                    _emit(from_table, ref.group(1), from_col, to_col, node)
    return rels


def read_sql_file_nodes(repo_path) -> list[CodeNode]:
    """Read standalone ``.sql`` files (including ``migrations/``) as synthetic
    CodeNodes so their DDL feeds the SQL entity + relationship extractors.

    ``.sql`` is not a tree-sitter language, so ``walk_repo`` never yields it and
    ``migrations/`` is in ``_SKIP_DIRS`` — yet migrations are the most reliable
    schema + FK source. We walk plainly here, skipping only vendor/build noise.
    """
    from pathlib import Path
    from ..repo.lang_detector import _SKIP_DIRS

    repo_path = Path(repo_path)
    skip = _SKIP_DIRS - {"migrations"}  # migrations carry the canonical schema
    out: list[CodeNode] = []
    for path in sorted(repo_path.rglob("*.sql")):
        if any(part in skip for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not text.strip():
            continue
        out.append(CodeNode(
            file_path=str(path), language="sql", node_type="sql_source",
            name=path.name, qualified_name=f"sqlfile::{path.name}",
            source_code=text, line_start=1, line_end=text.count("\n") + 1,
        ))
    return out


def _mine_statements(text: str):
    """Yield `(qualified_table, fields, op, kind, raw_snippet)` for each SQL
    statement found. `qualified_table` is `schema.table`; `kind` is `"table"`
    or `"view"` — views get their own node_type downstream.
    """
    # VIEWs first, so a view match suppresses a later CREATE TABLE lookalike.
    for m in _CREATE_VIEW_RE.finditer(text):
        name = _clean_qualified(m.group(1))
        if not name:
            continue
        cols = _parse_select_list_columns(m.group(2))
        if cols:
            yield name, cols, "CREATE_VIEW", "view", m.group(0)

    for m in _CREATE_TABLE_RE.finditer(text):
        table = _clean_qualified(m.group(1))
        if not table:
            continue
        cols = _parse_create_table_columns(m.group(2))
        if cols:
            yield table, cols, "CREATE", "table", m.group(0)

    for m in _INSERT_INTO_RE.finditer(text):
        table = _clean_qualified(m.group(1))
        if not table:
            continue
        cols = [_clean_ident(c) for c in m.group(2).split(",")]
        cols = [c for c in cols if c and c.lower() not in _NON_COLUMN_TOKENS]
        if cols:
            yield table, cols, "INSERT", "table", m.group(0)

    for m in _UPDATE_SET_RE.finditer(text):
        table = _clean_qualified(m.group(1))
        if not table:
            continue
        cols = _parse_update_set_columns(m.group(2))
        if cols:
            yield table, cols, "UPDATE", "table", m.group(0)

    for m in _SELECT_FROM_RE.finditer(text):
        select_list = m.group(1)
        table = _clean_qualified(m.group(2))
        if not table:
            continue
        cols = _parse_select_list_columns(select_list)
        if cols:
            yield table, cols, "SELECT", "table", m.group(0)

    for m in _ALTER_ADD_COL_RE.finditer(text):
        table = _clean_qualified(m.group(1))
        col = _clean_ident(m.group(2))
        if table and col:
            yield table, [col], "ALTER", "table", m.group(0)


# ---------------------------------------------------------------------------
# Column-list parsers
# ---------------------------------------------------------------------------

def _parse_create_table_columns(body: str) -> list[str]:
    """Extract column names from a CREATE TABLE body.

    The body looks like `id INT PRIMARY KEY, status VARCHAR(32), total DECIMAL`.
    We split on top-level commas (ignoring commas inside parentheses, which
    appear in things like `DECIMAL(10, 2)`) and keep the first token of each
    piece as the column name. Table-level constraints (`PRIMARY KEY (...)`,
    `CONSTRAINT ... FOREIGN KEY`, etc.) are filtered out.
    """
    cols: list[str] = []
    for piece in _split_top_level(body, ","):
        piece = piece.strip()
        if not piece:
            continue
        upper = piece.upper()
        if upper.startswith(
            (
                "PRIMARY KEY",
                "FOREIGN KEY",
                "UNIQUE",
                "CONSTRAINT",
                "CHECK",
                "INDEX",
                "KEY ",
            )
        ):
            continue
        first_token = piece.split(None, 1)[0]
        col = _clean_ident(first_token)
        if col and col.lower() not in _NON_COLUMN_TOKENS:
            cols.append(col)
    return cols


def _parse_update_set_columns(body: str) -> list[str]:
    """Extract column names from an UPDATE SET clause.

    Body shape: `col1 = ?, col2 = expr, col3 = :named`.
    """
    cols: list[str] = []
    for piece in _split_top_level(body, ","):
        if "=" not in piece:
            continue
        lhs = piece.split("=", 1)[0]
        col = _clean_ident(lhs.strip())
        if col and col.lower() not in _NON_COLUMN_TOKENS:
            cols.append(col)
    return cols


def _parse_select_list_columns(body: str) -> list[str]:
    """Extract column names from a SELECT list.

    Conservative: only keeps simple identifiers. `SELECT *` yields nothing;
    aggregate expressions like `COUNT(*)` yield nothing; aliased columns
    (`name AS display`) keep the source column, not the alias; T-SQL
    `TOP N [PERCENT]` and `TOP (N)` modifiers are stripped before parsing.
    """
    body = _TSQL_TOP_RE.sub("", body, count=1)
    cols: list[str] = []
    for piece in _split_top_level(body, ","):
        piece = piece.strip()
        if not piece or piece == "*":
            continue
        # Strip alias: `foo AS bar` or `foo bar` → `foo`.
        token = re.split(r"\s+AS\s+|\s+", piece, maxsplit=1, flags=re.IGNORECASE)[0]
        # Drop function-call expressions like `COUNT(id)` — no clean field name.
        if "(" in token:
            continue
        # Strip `table.` / `alias.` prefix.
        if "." in token:
            token = token.rsplit(".", 1)[-1]
        col = _clean_ident(token)
        if col and col.lower() not in _NON_COLUMN_TOKENS:
            cols.append(col)
    return cols


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split on `sep` but only at paren-depth 0."""
    out: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == sep and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return out


def _clean_ident(token: str) -> str:
    """Strip quoting, bracketing, and schema qualifier from an identifier."""
    if not token:
        return ""
    token = token.strip().strip("`\"[]")
    # Drop schema qualifier: `public.orders` → `orders`.
    if "." in token:
        token = token.rsplit(".", 1)[-1]
    token = token.strip("`\"[]")
    # Identifiers must start with letter/underscore, then word chars only.
    if not re.match(r"^[A-Za-z_][\w]*$", token):
        return ""
    return token


def _clean_qualified(token: str) -> str:
    """Like `_clean_ident` but preserves `schema.table` so catalog-level
    filters can still see the schema. Returns empty string on garbage."""
    if not token:
        return ""
    parts = [p.strip().strip("`\"[]") for p in token.split(".") if p.strip()]
    # Each part must be a bare identifier; bail if any fails.
    cleaned: list[str] = []
    for p in parts:
        if not re.match(r"^[A-Za-z_][\w]*$", p):
            return ""
        cleaned.append(p)
    return ".".join(cleaned) if cleaned else ""


def _is_ignored(qualified: str) -> bool:
    """Catalog / system-table filter that operates on `schema.table`."""
    if "." in qualified:
        schema, table = qualified.rsplit(".", 1)
        if schema.lower() in _IGNORED_SCHEMAS:
            return True
    else:
        table = qualified
    return table.lower() in _IGNORED_TABLES


def _strip_schema(qualified: str) -> str:
    """Drop any `schema.` prefix, leaving just the table name."""
    return qualified.rsplit(".", 1)[-1] if "." in qualified else qualified


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

def _to_code_node(entry: _TableAccumulator) -> CodeNode:
    """Build a synthetic CodeNode representing a SQL table or view.

    Views get `node_type="sql_view"` and `qualified_name="sqlview::<name>"`
    so downstream consumers can distinguish them from base tables. Both feed
    the consolidator identically — the Phase 2d projection rule links a
    view onto its base table when field coverage is high enough.
    """
    if entry.kind == "view":
        return CodeNode(
            file_path=entry.first_file or "",
            language="sql",
            node_type="sql_view",
            name=entry.table,
            qualified_name=f"sqlview::{entry.table}",
            source_code="\n".join(entry.evidence),
            line_start=entry.first_line,
            line_end=entry.first_line,
            fields=sorted(entry.fields),
            framework_hints={"sql_ops": sorted(entry.ops)},
        )
    return CodeNode(
        file_path=entry.first_file or "",
        language="sql",
        node_type="sql_table",
        name=entry.table,
        qualified_name=f"sql::{entry.table}",
        source_code="\n".join(entry.evidence),
        line_start=entry.first_line,
        line_end=entry.first_line,
        fields=sorted(entry.fields),
        framework_hints={"sql_ops": sorted(entry.ops)},
    )
