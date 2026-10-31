"""``get_impact`` and ``get_change_impact`` — what may break."""

from __future__ import annotations

from ... import query
from ..tool import DEPTH, INCLUDE, MAX_ITEMS, Budget, ToolSpec, schema

_HOW = ("Items are ranked by score and say `may_break` with `certainty` likely|possible — they are leads to "
        "confirm by reading the cited code, not proof. Work through `unknowns` too (unresolved or dynamic calls, "
        "hubs not walked): they are where the graph cannot see. Co-change history is never listed as an item.")

TOOLS = [
    ToolSpec(
        name="get_impact",
        rank=30,
        title="Impact of changing a target",
        description=(
            "Who depends on a target, walking callers up to `depth` levels and ending at business entry points "
            "(endpoints, screens, batch jobs, tables written, entity state machines). Target: symbol, `file:`, "
            "`table:`, `screen:`, `entity:` or `endpoint:` id. " + _HOW),
        input_schema=schema({"target": {"type": "string", "minLength": 1, "maxLength": 500},
                             "depth": DEPTH, "include": INCLUDE, "max_items": MAX_ITEMS}, required=("target",)),
        handler=lambda project, args: query.impact(project, args["target"], depth=args.get("depth", 3),
                                                   include=args.get("include"),
                                                   max_items=args.get("max_items", 50)),
        budget=Budget(max_tokens=8000, shed=("unknowns", "items")),
        profiles=frozenset({"default", "lean"}),
    ),
    ToolSpec(
        name="get_change_impact",
        rank=31,
        title="Impact of a change",
        description=(
            "Impact of a change set: pass exactly one of `working_tree: true` (uncommitted and committed changes "
            "since the indexed commit), `range` (git range such as `main..HEAD`) or `diff` (unified diff text). "
            "Returns changed files and symbols, the impact of those symbols, affected screens, and tests reached "
            "(`tests_to_run.status` stays unknown until coverage is imported). " + _HOW),
        input_schema=schema({
            "working_tree": {"type": "boolean"},
            "range": {"type": "string", "maxLength": 200},
            "diff": {"type": "string", "maxLength": 2_000_000},
            "depth": DEPTH, "include": INCLUDE, "max_items": MAX_ITEMS,
        }),
        handler=lambda project, args: query.change_impact(
            project, diff=args.get("diff"), range=args.get("range"), working_tree=bool(args.get("working_tree")),
            depth=args.get("depth", 3), include=args.get("include"), max_items=args.get("max_items", 50)),
        budget=Budget(max_tokens=8000, shed=("impact.unknowns", "changed_files", "impact.items", "changed_symbols")),
        profiles=frozenset({"default", "lean"}),
    ),
]
