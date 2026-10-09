"""Persistent store: numbered schema migrations (roadmap item M1.4)."""

from .migrate import MigrationError, current_version, discover_migrations, migrate

__all__ = ["MigrationError", "current_version", "discover_migrations", "migrate"]
