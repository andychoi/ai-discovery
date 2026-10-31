"""``get_path`` (opt-in) — shortest call chains between two symbols."""

from __future__ import annotations

from ... import query
from ..tool import INCLUDE, Budget, ToolSpec, schema

TOOLS = [
    ToolSpec(
        name="get_path",
        rank=70,
        title="Call path A → B",
        description=(
            "Shortest call chains from `source` to `target` (both symbols), following only edges the impact walk "
            "trusts. `found: no` is not proof of independence: unresolved calls may still connect them."),
        input_schema=schema({
            "source": {"type": "string", "minLength": 1, "maxLength": 500},
            "target": {"type": "string", "minLength": 1, "maxLength": 500},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 6, "default": 4},
            "include": INCLUDE,
        }, required=("source", "target")),
        handler=lambda project, args: query.call_path(project, args["source"], args["target"],
                                                      max_depth=args.get("max_depth", 4),
                                                      include=args.get("include")),
        budget=Budget(max_tokens=5000, shed=("paths",)),
        profiles=frozenset(),
    ),
]
