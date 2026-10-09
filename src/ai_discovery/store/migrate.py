"""Ordered, recorded schema migrations (roadmap item M1.4).

Migration modules live in ``store/migrations`` as ``m<NNNN>_<name>.py`` (the
``m`` prefix makes them importable) and expose ``up(conn)``. Each runs in its
own transaction and is recorded in ``schema_migrations``; a failure rolls back
and is not recorded.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from types import ModuleType

from . import migrations as _pkg

_NAME_RE = re.compile(r"^m(\d{4})_([a-z0-9_]+)$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    module: ModuleType


def discover_migrations() -> list[Migration]:
    found: list[Migration] = []
    for info in pkgutil.iter_modules(_pkg.__path__):
        match = _NAME_RE.match(info.name)
        if not match:
            continue
        module = importlib.import_module(f"{_pkg.__name__}.{info.name}")
        if not callable(getattr(module, "up", None)):
            raise MigrationError(f"migration {info.name} has no up(conn)")
        found.append(Migration(int(match.group(1)), match.group(2), module))
    found.sort(key=lambda m: m.version)
    expected = list(range(1, len(found) + 1))
    if [m.version for m in found] != expected:
        raise MigrationError(
            f"migrations must be numbered contiguously from 0001; found {[m.version for m in found]}"
        )
    return found


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )


def current_version(conn: sqlite3.Connection) -> int:
    _ensure_table(conn)
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def migrate(conn: sqlite3.Connection, target: int | None = None) -> list[int]:
    """Apply pending migrations up to *target* (default: head). Returns versions applied."""
    if conn.in_transaction:
        conn.commit()
    _ensure_table(conn)
    conn.commit()
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied: list[int] = []
    for mig in discover_migrations():
        if target is not None and mig.version > target:
            break
        if mig.version in done:
            continue
        try:
            conn.execute("BEGIN")
            mig.module.up(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (mig.version, mig.name, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise MigrationError(f"migration {mig.version:04d}_{mig.name} failed: {exc}") from exc
        applied.append(mig.version)
    return applied
