"""Tool registry and selection (roadmap item M5.3).

Tools are auto-discovered: every module in ``mcp/tools/`` exposes ``TOOLS`` (a
list of ``ToolSpec``). Adding a tool never edits a central list.

Selection spec (``--tools``), comma-separated, applied left to right:

- ``default`` | ``lean`` | ``all`` — start from that profile;
- ``name`` — allowlist entry (a spec of bare names selects exactly those);
- ``+name`` / ``-name`` — add to / remove from the current set.

``"lean,+get_table"``, ``"+get_path"`` (default + one), ``"search,get_context"``.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from functools import lru_cache

from .tool import PROFILES, ToolSpec


class ToolSelectionError(ValueError):
    pass


@lru_cache(maxsize=1)
def discover() -> dict[str, ToolSpec]:
    from . import tools as pkg

    found: dict[str, ToolSpec] = {}
    for mod in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if mod.name.startswith("_"):
            continue
        module = importlib.import_module(f"{pkg.__name__}.{mod.name}")
        for spec in getattr(module, "TOOLS", ()):
            if spec.name in found:
                raise ToolSelectionError(f"duplicate tool name {spec.name!r} ({mod.name})")
            found[spec.name] = spec
    return dict(sorted(found.items(), key=lambda kv: (kv[1].rank, kv[0])))


def profile(name: str) -> list[str]:
    tools = discover()
    if name == "all":
        return list(tools)
    if name not in PROFILES:
        raise ToolSelectionError(f"unknown profile {name!r}; use one of {', '.join(PROFILES)}")
    return [n for n, s in tools.items() if name in s.profiles]


def select(spec: str | None) -> list[ToolSpec]:
    """Resolve a ``--tools`` spec to tool specs (registry order)."""
    tools = discover()
    parts = [p.strip() for p in (spec or "").split(",") if p.strip()]
    if not parts:
        parts = ["default"]
    bare = [p for p in parts if p[0] not in "+-" and p not in PROFILES]
    has_profile = any(p in PROFILES for p in parts)
    chosen: list[str] = [] if (bare or has_profile) else profile("default")
    for p in parts:
        if p in PROFILES:
            chosen = [n for n in dict.fromkeys(chosen + profile(p))]
            continue
        op, name = (p[0], p[1:]) if p[0] in "+-" else ("+", p)
        if name not in tools:
            raise ToolSelectionError(f"unknown tool {name!r}; available: {', '.join(tools)}")
        if op == "+" and name not in chosen:
            chosen.append(name)
        elif op == "-" and name in chosen:
            chosen.remove(name)
    if not chosen:
        raise ToolSelectionError(f"tool selection {spec!r} selects no tools")
    order = list(tools)
    return [tools[n] for n in sorted(chosen, key=order.index)]


def schema_tokens(specs: list[ToolSpec]) -> int:
    """Approximate tokens the tool list costs a client (≈ 4 characters per token)."""
    payload = [{"name": s.name, "description": s.description, "inputSchema": s.input_schema} for s in specs]
    return len(json.dumps(payload, separators=(",", ":"))) // 4
