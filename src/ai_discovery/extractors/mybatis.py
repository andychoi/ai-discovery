"""MyBatis mapper XML (roadmap item M6.9).

A mapper file (`<mapper namespace="pkg.OrderMapper">`) implements the methods of the Java interface the namespace
names: statement `<select|insert|update|delete id="findByCustomer">` is the body of `pkg.OrderMapper.findByCustomer`.
This module turns each statement into a node `mybatis::<namespace>.<id>` holding its flattened SQL, plus:

- a `framework` edge from the interface method (when the scan has it) to the statement — MyBatis binds them;
- `reads_table` / `writes_table` edges from the statement to the tables its SQL touches (sqlglot, M6.8).

Dynamic SQL is flattened *conservatively*: `<include refid>` is inlined, `<where>`/`<set>`/`<trim>` become their
keyword/prefix, `<if>`/`<when>`/`<otherwise>`/`<foreach>` keep their body once (so every table any branch touches
is seen), `#{p}` becomes a bind parameter and `${p}` a dynamic placeholder — a table named by `${…}` is skipped.
Regex over the XML: mapper files are small and often carry DTDs/entities a strict parser rejects. Never raises.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from ..graph.models import CallEdge, CodeNode

_MAPPER = re.compile(r"<mapper\b[^>]*\bnamespace\s*=\s*\"([^\"]+)\"[^>]*>(.*?)</mapper>", re.S)
_STATEMENT = re.compile(r"<(select|insert|update|delete)\b([^>]*)>(.*?)</\1>", re.S | re.I)
_FRAGMENT = re.compile(r"<sql\b[^>]*\bid\s*=\s*\"([^\"]+)\"[^>]*>(.*?)</sql>", re.S)
_ATTR = re.compile(r'([\w:.-]+)\s*=\s*"([^"]*)"')
_INCLUDE = re.compile(r"<include\b[^>]*\brefid\s*=\s*\"([^\"]+)\"[^>]*?(?:/>|>.*?</include>)", re.S)
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TRIM = re.compile(r"<trim\b([^>]*)>", re.I)
_TAG = re.compile(r"</?[a-zA-Z][\w:-]*\b[^>]*?/?>")
_HASH = re.compile(r"#\{[^}]*\}")
_DOLLAR = re.compile(r"\$\{[^}]*\}")


def _attrs(text: str) -> dict[str, str]:
    return {k.lower(): v for k, v in _ATTR.findall(text)}


def flatten(body: str, fragments: dict[str, str], depth: int = 0) -> str:
    """Mapper statement body → one SQL statement (see module docstring)."""
    from .sql import DYN

    def _include(m: re.Match) -> str:
        frag = fragments.get(m.group(1)) or fragments.get(m.group(1).rsplit(".", 1)[-1], "")
        return flatten(frag, fragments, depth + 1) if depth < 8 else ""

    text = _COMMENT.sub(" ", body)
    text = _CDATA.sub(lambda m: m.group(1), text)
    text = _INCLUDE.sub(_include, text)
    text = re.sub(r"<where\b[^>]*>", " WHERE 1=1 AND ", text, flags=re.I)
    text = re.sub(r"<set\b[^>]*>", " SET ", text, flags=re.I)
    text = _TRIM.sub(lambda m: " " + (_attrs(m.group(1)).get("prefix") or "") + " ", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _HASH.sub("?", text)
    text = _DOLLAR.sub(DYN, text)
    # flattening joins branch bodies: tidy dangling conjunctions and separators it can leave behind
    text = re.sub(r"\bWHERE\s+1=1\s+AND\s+(AND|OR)\b", r"WHERE 1=1 \1", text, flags=re.I)
    text = re.sub(r"\bWHERE\s+1=1\s+AND\s*($|\)|ORDER\b|GROUP\b|LIMIT\b)", r"WHERE 1=1 \1", text, flags=re.I)
    text = re.sub(r",\s*(WHERE|FROM)\b", r" \1", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def read_mapper_statements(repo_path) -> list[CodeNode]:
    """One `sql_statement` node per mapper statement under *repo_path* (files the walker would read)."""
    from ..repo.walker import excluded_path

    root = Path(repo_path)
    out: list[CodeNode] = []
    for path in sorted(root.rglob("*.xml")):
        if excluded_path(path, root):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "<mapper" not in text:
            continue
        text = _COMMENT.sub(lambda c: "\n" * c.group(0).count("\n"), text)   # keep line numbers
        for m in _MAPPER.finditer(text):
            namespace, body = m.group(1), m.group(2)
            fragments = {fid: ftext for fid, ftext in _FRAGMENT.findall(body)}
            for st in _STATEMENT.finditer(body):
                kind, attrs, sbody = st.group(1).lower(), _attrs(st.group(2)), st.group(3)
                sid = attrs.get("id")
                if not sid:
                    continue
                line = text.count("\n", 0, m.start(2) + st.start()) + 1
                out.append(CodeNode(
                    file_path=str(path), language="sql", node_type="sql_statement", name=sid,
                    qualified_name=f"mybatis::{namespace}.{sid}", source_code=flatten(sbody, fragments),
                    line_start=line, line_end=line + sbody.count("\n"),
                    framework_hints={"framework": "mybatis", "namespace": namespace, "statement": kind,
                                     "statement_type": attrs.get("statementtype", "PREPARED").upper(),
                                     "mapper_method": f"{namespace}.{sid}"},
                ))
    return out


def mybatis_edges(statements: list[CodeNode], code_nodes: list[CodeNode]) -> tuple[list[CodeNode], list[CallEdge]]:
    """(table nodes, edges): interface method → statement (`framework`) when the method is in the scan, and
    statement → table (`sql_parsed`, or `sql_regex` when sqlglot cannot parse the flattened statement)."""
    from ..graph.call_graph import make_edge
    from .sql_extractor import extract_table_access

    by_qn = {n.qualified_name for n in code_nodes}
    edges = [make_edge(s.framework_hints["mapper_method"], s.qualified_name, "framework", binding="mybatis")
             for s in statements if s.framework_hints["mapper_method"] in by_qn]
    tables, table_edges, _stats = extract_table_access(statements)   # a statement node is SQL throughout
    return tables, edges + table_edges
