"""Stable public Python API (roadmap item M5.1).

Bots, CI jobs and notebooks import this module instead of the server or CLI;
MCP tools and ``discover ask`` return the same answers. Names exported here
follow semantic versioning with the package; anything not listed in
``__all__`` is internal.

    from ai_discovery import api
    project = api.Project.open(".")
    print(api.overview(project)["data"]["summary"])

Every call returns the ``Answer`` envelope (``status`` ∈ ok | not_found |
not_indexed | unknown; ``data``; ``totals``; ``truncated``; ``omitted``;
``meta`` with freshness and ``content_trust``).
"""

from .query import (CONTRACT_VERSION, STATUSES, TRI_STATES, Answer, Project, call_path, change_impact, context,
                    entity, flow, impact, overview, screen, search, symbol_source, table, verification_plan)

__all__ = ["CONTRACT_VERSION", "STATUSES", "TRI_STATES", "Answer", "Project",
           "overview", "search", "context", "screen", "entity", "flow", "table", "impact", "change_impact",
           "symbol_source", "call_path", "verification_plan"]
