"""``get_overview`` — start here: stacks, counts, entry points, domains, freshness."""

from __future__ import annotations

from ... import query
from ..tool import Budget, ToolSpec, schema

TOOLS = [
    ToolSpec(
        name="get_overview",
        rank=0,
        title="Project overview",
        description=(
            "Call first. Summarises the indexed project: languages, counts (files, symbols, screens, endpoints, "
            "tables, entities), top entry points, domains and the share of unresolved calls per language. "
            "`_meta.behind_by`/`dirty` say how stale the index is; if it is behind, tell the user and suggest "
            "`discover scan`."),
        input_schema=schema({}),
        handler=lambda project, args: query.overview(project),
        budget=Budget(max_tokens=4000, shed=("domains", "entry_points")),
        profiles=frozenset({"default"}),
    ),
]
