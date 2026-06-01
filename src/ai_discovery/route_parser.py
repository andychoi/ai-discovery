"""Parse SPA route/menu source into a generic RouteNode tree (tree-sitter).

Knows nothing about Screens — menu_detector converts RouteNode -> MenuItem.
Every public failure path returns None / [] rather than raising.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tree_sitter_javascript as tsjs
    import tree_sitter_typescript as tsts
    from tree_sitter import Language, Parser
    _JS = Language(tsjs.language())
    _TS = Language(tsts.language_typescript())
    _TSX = Language(tsts.language_tsx())
    _TS_AVAILABLE = True
except Exception:  # pragma: no cover - environment without grammars
    _TS_AVAILABLE = False

logger = logging.getLogger(__name__)

_MAX_DEPTH = 25  # guard against pathological nesting


@dataclass
class RouteNode:
    path: str | None = None
    component: str | None = None
    component_source: str | None = None
    name: str | None = None
    title: str | None = None
    roles: list[str] = field(default_factory=list)
    redirect_to: str | None = None
    is_catch_all: bool = False
    children: list["RouteNode"] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def _grammar_for_ext(path: Path):
    if not _TS_AVAILABLE:
        return None
    suffix = path.suffix.lower()
    if suffix == ".tsx":
        return _TSX
    if suffix == ".jsx":
        return _JS  # JS grammar parses JSX
    if suffix == ".ts":
        return _TS
    if suffix in (".js", ".mjs", ".cjs"):
        return _JS
    return None


def _str_value(node) -> str | None:
    """Return the text of a string node without its quote delimiters."""
    if node is None:
        return None
    if node.type in ("string", "template_string"):
        for c in node.named_children:
            if c.type in ("string_fragment", "template_substitution"):
                return c.text.decode("utf-8", "ignore")
        text = node.text.decode("utf-8", "ignore")
        return text[1:-1] if len(text) >= 2 else ""
    return node.text.decode("utf-8", "ignore")


def _collect_imports(root) -> dict[str, str]:
    """Map each imported identifier to its module specifier."""
    imports: dict[str, str] = {}
    for node in _iter(root):
        if node.type != "import_statement":
            continue
        src = node.child_by_field_name("source")
        specifier = _str_value(src) if src is not None else None
        if not specifier:
            continue
        for ident in _iter(node):
            if ident.type == "identifier":
                imports[ident.text.decode("utf-8", "ignore")] = specifier
    return imports


def _iter(node):
    """Depth-first iterator over all descendant nodes (incl. node itself)."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))
