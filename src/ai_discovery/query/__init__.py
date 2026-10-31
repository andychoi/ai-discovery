"""Query service (roadmap item M5.1): one read API for MCP, the HTTP API and the CLI.

Every function returns the ``Answer`` envelope as a dict (``query/answer.py``)::

    from ai_discovery.query import Project, overview, search, context
    project = Project.open("/path/to/repo")
    context(project, "OrderService.cancel")["data"]["callers"]
"""

from .answer import CONTRACT_VERSION, STATUSES, TRI_STATES, Answer
from .impact import change_impact, impact
from .search import search
from .session import Project
from .views import context, entity, flow, overview, screen, table

__all__ = ["Answer", "CONTRACT_VERSION", "STATUSES", "TRI_STATES", "Project",
           "overview", "search", "context", "screen", "entity", "flow", "table", "impact", "change_impact"]
