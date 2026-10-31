"""``get_verification_plan`` (opt-in) — how to check a change, and whether it stayed inside its plan."""

from __future__ import annotations

from ... import query
from ..tool import DEPTH, Budget, ToolSpec, schema

TOOLS = [
    ToolSpec(
        name="get_verification_plan",
        rank=35,
        title="Verification plan for a change",
        description=(
            "After editing: pass `working_tree: true` (or `range` / `diff`) and, if you planned the change, "
            "`plan_path` to its IMPLEMENTATION_PLAN.md. Returns build and test commands for the stacks touched "
            "(nearest build file per changed file, wrapper scripts preferred), targeted tests (reached through the "
            "graph or found by naming convention), endpoints and screens to smoke-test, and — with a plan — changed "
            "files not pinned in it and impacted nodes it never mentions. `verdict: stop` lists the reasons. "
            "ai-discovery does not run anything: run the commands yourself and report the results."),
        input_schema=schema({
            "working_tree": {"type": "boolean"},
            "range": {"type": "string", "maxLength": 200},
            "diff": {"type": "string", "maxLength": 2_000_000},
            "plan_path": {"type": "string", "maxLength": 1000,
                          "description": "Path to IMPLEMENTATION_PLAN.md (inside the repo or .discovery/)."},
            "depth": DEPTH,
        }),
        handler=lambda project, args: query.verification_plan(
            project, diff=args.get("diff"), range=args.get("range"), working_tree=bool(args.get("working_tree")),
            plan_path=args.get("plan_path"), depth=args.get("depth", 3)),
        budget=Budget(max_tokens=6000, shed=("unknowns", "plan.unexpected_impact", "targeted_tests",
                                             "smoke_targets", "changed_symbols", "changed_files")),
        profiles=frozenset(),
    ),
]
