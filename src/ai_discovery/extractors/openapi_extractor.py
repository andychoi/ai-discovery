"""OpenAPI / Swagger ingestion (HIGH-7).

An OpenAPI/Swagger document is a *declared contract* — often a more complete and
more authoritative endpoint inventory than the AST (it includes routes that are
generated, proxied, or defined outside the scanned handlers). This reads spec
files (`openapi.{json,yaml}`, `swagger.{json,yaml}`, `*api-docs*`) and emits
endpoint CodeNodes with route+method in `framework_hints`, so they flow into the
same verified-facts API table as parser-extracted endpoints — no separate path.

Conservative: a file must actually parse and carry the `openapi`/`swagger` marker
plus a `paths` object before any endpoint is emitted.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..graph.models import CodeNode

logger = logging.getLogger(__name__)

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})
# Filename hints that make a JSON/YAML file a spec candidate (cheap pre-filter).
_NAME_HINTS = ("openapi", "swagger", "api-docs", "apidocs")


def extract_openapi_endpoints(spec: dict, source: str = "openapi") -> list[CodeNode]:
    """Emit endpoint CodeNodes from a parsed OpenAPI/Swagger document.

    Composes the optional `basePath` (Swagger 2) / first server path prefix
    (OpenAPI 3) onto each path, and names the node from `operationId` when present.
    """
    if not isinstance(spec, dict):
        return []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return []

    prefix = _base_prefix(spec)
    out: list[CodeNode] = []
    for raw_path, item in paths.items():
        if not isinstance(item, dict):
            continue
        full_path = _join(prefix, str(raw_path))
        for method, op in item.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            verb = method.upper()
            op = op if isinstance(op, dict) else {}
            op_id = op.get("operationId") or f"{method.lower()}_{full_path}"
            summary = op.get("summary") or op.get("description") or ""
            out.append(CodeNode(
                file_path=source, language="openapi", node_type="endpoint",
                name=str(op_id), qualified_name=f"openapi::{verb}:{full_path}",
                source_code="", line_start=0, line_end=0,
                framework_hints={
                    "method": verb, "route": full_path,
                    "source": "openapi", "operation_id": str(op_id),
                    "summary": str(summary)[:200],
                },
            ))
    return out


def read_openapi_files(repo_path) -> list[CodeNode]:
    """Find and parse OpenAPI/Swagger specs under *repo_path*, returning endpoint
    nodes. Spec files aren't tree-sitter languages, so the normal walk skips them."""
    from ..repo.lang_detector import _SKIP_DIRS

    repo_path = Path(repo_path)
    out: list[CodeNode] = []
    for path in sorted(repo_path.rglob("*")):
        if path.suffix.lower() not in (".json", ".yaml", ".yml") or not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        # Cheap pre-filter: spec-ish filename, or a small file we can sniff.
        name = path.name.lower()
        if not any(h in name for h in _NAME_HINTS):
            try:
                if path.stat().st_size > 2_000_000:  # don't sniff huge data files
                    continue
                head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
            except OSError:
                continue
            if "openapi" not in head and "swagger" not in head:
                continue
        spec = _load(path)
        if not isinstance(spec, dict) or not ("openapi" in spec or "swagger" in spec):
            continue
        endpoints = extract_openapi_endpoints(spec, source=str(path))
        if endpoints:
            out.extend(endpoints)
    return out


def _load(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    try:
        if path.suffix.lower() == ".json":
            return json.loads(text)
        import yaml
        return yaml.safe_load(text)
    except Exception as exc:  # malformed spec — skip, don't abort the scan
        logger.debug("Failed to parse candidate OpenAPI file %s: %s", path, exc)
        return None


def _base_prefix(spec: dict) -> str:
    # Swagger 2.0 basePath
    bp = spec.get("basePath")
    if isinstance(bp, str) and bp.strip("/"):
        return "/" + bp.strip("/")
    # OpenAPI 3 servers[0].url path component
    servers = spec.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        url = str(servers[0].get("url", ""))
        # keep only a leading path (ignore scheme/host)
        if url.startswith("/"):
            return "/" + url.strip("/")
        if "://" in url:
            tail = url.split("://", 1)[1]
            if "/" in tail:
                p = "/" + tail.split("/", 1)[1].strip("/")
                return p if p != "/" else ""
    return ""


def _join(prefix: str, path: str) -> str:
    a = (prefix or "").strip("/")
    b = (path or "").strip("/")
    parts = [p for p in (a, b) if p]
    return "/" + "/".join(parts) if parts else "/"
