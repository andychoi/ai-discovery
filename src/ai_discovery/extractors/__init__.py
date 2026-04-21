"""Non-class field-source extractors (Phase 2.5).

Each extractor scans parsed code for a specific off-the-AST source of entity
fields — raw SQL strings, SQLAlchemy Core `Table(...)` calls, migrations,
GraphQL schemas, etc. — and produces synthetic classless CodeNodes that
plug into the same Phase 2.4 consolidator as regular class definitions.
"""

from .sql_extractor import extract_sql_entities

__all__ = ["extract_sql_entities"]
