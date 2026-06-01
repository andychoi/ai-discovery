"""Non-class field-source extractors (Phase 2e).

Each extractor scans parsed code for a specific off-the-AST source of entity
fields — raw SQL strings, SQLAlchemy Core `Table(...)` calls, migrations,
GraphQL schemas, etc. — and produces synthetic classless CodeNodes that
plug into the same Phase 2d consolidator as regular class definitions.
"""

from .sql_extractor import (
    extract_sql_entities,
    extract_sql_relationships,
    read_sql_file_nodes,
)
from .relationship_extractor import extract_relationships
from .external_system_extractor import extract_external_systems

__all__ = [
    "extract_sql_entities",
    "extract_sql_relationships",
    "read_sql_file_nodes",
    "extract_relationships",
    "extract_external_systems",
]
