"""GraphQL SDL ingestion (HIGH-7).

A GraphQL schema is a declared contract: object types are entities and the
Query/Mutation/Subscription root fields are operations. This reads `.graphql` /
`.gql` (and `.graphqls`) schema files and emits db_model entities (object types
+ their fields) and endpoint nodes (root operations), so they flow into the same
verified-facts machinery as parser-extracted entities/endpoints.

Regex-based: GraphQL SDL is a small, regular grammar and we only need type names,
field names, and root-operation names — not a full parse.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..graph.models import CodeNode

_ROOT_TYPES = {"Query", "Mutation", "Subscription"}
_OP_METHOD = {"Query": "QUERY", "Mutation": "MUTATION", "Subscription": "SUBSCRIPTION"}
# `type Name [implements ...] { body }`  (also matches `input` blocks)
_TYPE_RE = re.compile(
    r"\b(?:type|input)\s+(\w+)\s*(?:implements[^{]+)?\{([^}]*)\}", re.DOTALL
)
# A field line: `name(args): Type` or `name: Type` → capture the name.
_FIELD_RE = re.compile(r"^\s*(\w+)\s*(?:\([^)]*\))?\s*:", re.MULTILINE)


def extract_graphql(text: str, source: str = "graphql") -> list[CodeNode]:
    out: list[CodeNode] = []
    for m in _TYPE_RE.finditer(text or ""):
        name, body = m.group(1), m.group(2)
        fields = _FIELD_RE.findall(body)
        if name in _ROOT_TYPES:
            method = _OP_METHOD[name]
            for op in fields:
                out.append(CodeNode(
                    file_path=source, language="graphql", node_type="endpoint",
                    name=op, qualified_name=f"graphql::{method}:{op}", source_code="",
                    line_start=0, line_end=0,
                    framework_hints={"method": method, "route": f"/{op}", "source": "graphql"},
                ))
        elif fields:
            out.append(CodeNode(
                file_path=source, language="graphql", node_type="db_model",
                name=name, qualified_name=f"graphql::{name}", source_code="",
                line_start=0, line_end=0, fields=fields,
                framework_hints={"table": name, "source": "graphql"},
            ))
    return out


def read_graphql_files(repo_path) -> list[CodeNode]:
    from ..repo.lang_detector import _SKIP_DIRS

    repo_path = Path(repo_path)
    out: list[CodeNode] = []
    for path in sorted(repo_path.rglob("*")):
        if path.suffix.lower() not in (".graphql", ".gql", ".graphqls") or not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out.extend(extract_graphql(text, source=str(path)))
    return out
