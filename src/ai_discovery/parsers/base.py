from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..graph.models import CodeNode

_GUARD_WALK_MAX_HOPS = 25


def find_enclosing_guard(ast_node) -> str | None:
    """Walk up the AST from `ast_node` to find the nearest enclosing conditional's
    condition expression, and return it as normalized source text.

    Recognizes any ancestor whose `child_by_field_name("condition")` returns a
    non-None node. This covers `if_statement` across Python/Java/C#/JS and
    `elif_clause` in Python. Stops after _GUARD_WALK_MAX_HOPS ancestors to avoid
    drifting out of the containing function. Outer parens (e.g. Java/C# style
    `(x > 5)`) are stripped.

    Returns None if no conditional ancestor is found within the hop limit.
    """
    if ast_node is None:
        return None
    hops = 0
    cur = ast_node.parent
    while cur is not None and hops < _GUARD_WALK_MAX_HOPS:
        cond = cur.child_by_field_name("condition")
        if cond is not None:
            text = cond.text.decode()
            if cond.type == "parenthesized_expression":
                text = text.strip()
                if text.startswith("(") and text.endswith(")"):
                    text = text[1:-1].strip()
            return text
        cur = cur.parent
        hops += 1
    return None


class LanguageParser(ABC):
    """Abstract base class that all language parsers must implement."""

    @property
    @abstractmethod
    def language(self) -> str:
        ...

    @property
    @abstractmethod
    def extensions(self) -> frozenset[str]:
        ...

    @abstractmethod
    def parse_file(self, file_path: Path) -> list[CodeNode]:
        ...

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.extensions
