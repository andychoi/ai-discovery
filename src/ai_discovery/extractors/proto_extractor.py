"""Protocol Buffers / gRPC ingestion (HIGH-7).

A `.proto` file is a declared contract: `message` types are entities and
`service` RPCs are operations. This emits db_model entities (messages + their
fields) and endpoint nodes (RPCs, as `/Service/Method`), feeding the same
verified-facts machinery as parser-extracted entities/endpoints.

Regex-based: proto3 is a small grammar and we only need message/field names and
service/rpc names.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..graph.models import CodeNode

_MESSAGE_RE = re.compile(r"\bmessage\s+(\w+)\s*\{([^}]*)\}", re.DOTALL)
# `[repeated|optional] Type name = N;` → capture the field name (token before `=`).
_FIELD_RE = re.compile(
    r"^\s*(?:repeated\s+|optional\s+|required\s+)?[\w.<>, ]+?\s+(\w+)\s*=\s*\d+\s*;",
    re.MULTILINE,
)
_SERVICE_RE = re.compile(r"\bservice\s+(\w+)\s*\{([^}]*)\}", re.DOTALL)
_RPC_RE = re.compile(r"\brpc\s+(\w+)\s*\(", re.MULTILINE)


def extract_proto(text: str, source: str = "proto") -> list[CodeNode]:
    out: list[CodeNode] = []
    text = text or ""
    for m in _MESSAGE_RE.finditer(text):
        name, body = m.group(1), m.group(2)
        fields = _FIELD_RE.findall(body)
        if fields:
            out.append(CodeNode(
                file_path=source, language="proto", node_type="db_model",
                name=name, qualified_name=f"proto::{name}", source_code="",
                line_start=0, line_end=0, fields=fields,
                framework_hints={"table": name, "source": "proto"},
            ))
    for m in _SERVICE_RE.finditer(text):
        svc, body = m.group(1), m.group(2)
        for rpc in _RPC_RE.findall(body):
            out.append(CodeNode(
                file_path=source, language="proto", node_type="endpoint",
                name=rpc, qualified_name=f"proto::{svc}.{rpc}", source_code="",
                line_start=0, line_end=0,
                framework_hints={"method": "RPC", "route": f"/{svc}/{rpc}", "source": "proto"},
            ))
    return out


def read_proto_files(repo_path) -> list[CodeNode]:
    from ..repo.lang_detector import _SKIP_DIRS

    repo_path = Path(repo_path)
    out: list[CodeNode] = []
    for path in sorted(repo_path.rglob("*.proto")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out.extend(extract_proto(text, source=str(path)))
    return out
