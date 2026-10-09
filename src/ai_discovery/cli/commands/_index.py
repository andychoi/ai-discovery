"""Locate a project's index for ``status`` / ``doctor`` (roadmap item M2.5).

Order:
1. v1 layout — ``<repo>/.discovery/discovery.db`` (or ``--state-dir``).
2. Bridge until scan v1 (M2.3) writes the v1 layout: a legacy
   ``<root>/output-<slug>/discovery-<slug>.db`` whose latest ``scan_runs``
   row points at this repo. Roots searched: ``./data/discovery-output`` under
   the repo and under the current directory.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ...core.paths import ProjectLayout, legacy_db_candidates, repo_root
from ..context import Globals

LEGACY_ROOTS = ("data/discovery-output",)


@dataclass(frozen=True)
class IndexRef:
    db_path: Path
    lock_path: Path
    kind: str            # "v1" | "legacy"
    repo: Path


def _db_points_at(db: Path, repo: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT repo_path FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    if not row or not row[0]:
        return False
    try:
        return Path(row[0]).resolve() == repo
    except OSError:
        return False


def legacy_dbs(search_from: list[Path]) -> list[Path]:
    """All legacy-layout DBs under the known roots of *search_from* dirs."""
    found: list[Path] = []
    for base in search_from:
        for rel in LEGACY_ROOTS:
            found.extend(legacy_db_candidates(base / rel))
    seen: set[Path] = set()
    return [p for p in found if not (p.resolve() in seen or seen.add(p.resolve()))]


def find_index(g: Globals) -> tuple[Optional[IndexRef], ProjectLayout]:
    layout = g.layout()
    repo = layout.repo or repo_root(g.repo)
    if layout.db_path.is_file():
        return IndexRef(layout.db_path, layout.lock_path, "v1", repo), layout
    for db in legacy_dbs([repo, Path.cwd()]):
        if _db_points_at(db, repo):
            return IndexRef(db, db.parent / "lock", "legacy", repo), layout
    return None, layout
