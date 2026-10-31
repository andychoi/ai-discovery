"""Tool specification (roadmap item M5.3, design/mcp-and-agents.md §2–§3).

A tool is plain data plus a handler — no MCP SDK types — so the registry, the
middleware and the contract tests run without a transport. ``mcp/server.py``
adapts specs to the SDK.

Every tool **must** declare a ``Budget``: the token cap for its response and the
order in which list fields are shed when the answer is too large (most
expendable first). ``tests/mcp/test_contract.py`` fails for a tool without one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..query.session import Project

Handler = Callable[[Project, dict[str, Any]], dict]

PROFILES = ("default", "lean", "all")
DEFAULT_MAX_TOKENS = 8000


@dataclass(frozen=True)
class Budget:
    max_tokens: int = DEFAULT_MAX_TOKENS
    # Dotted paths under the answer's ``data`` holding ranked lists, most expendable first.
    shed: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Handler
    budget: Budget
    profiles: frozenset[str] = field(default_factory=lambda: frozenset({"default"}))
    title: str = ""
    rank: int = 100          # position in tools/list (lower first): overview, search, context, impact, …
    read_only: bool = True   # False for write-back tools (M5.10): annotated readOnlyHint=false
    needs_signer: bool = False   # handler(project, args, signer) — who is writing (client, session)

    def __post_init__(self) -> None:
        if not self.profiles <= set(PROFILES) - {"all"}:
            raise ValueError(f"{self.name}: unknown profile in {sorted(self.profiles)}")
        if self.input_schema.get("type") != "object":
            raise ValueError(f"{self.name}: input_schema must be a JSON-schema object")

    @property
    def opt_in(self) -> bool:
        return not self.profiles


def schema(properties: dict[str, dict], required: tuple[str, ...] = ()) -> dict:
    out: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        out["required"] = list(required)
    return out


# Shared argument schemas
DEPTH = {"type": "integer", "minimum": 1, "maximum": 6, "default": 3,
         "description": "How many caller levels to walk (default 3)."}
INCLUDE = {"type": "array", "items": {"type": "string", "enum": ["static", "framework", "dynamic_hint", "scip"]},
           "description": "Evidence kinds to follow (default static, framework, dynamic_hint)."}
MAX_ITEMS = {"type": "integer", "minimum": 1, "maximum": 100, "default": 50,
             "description": "Maximum impact items (ranked, highest score first)."}
