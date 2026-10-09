"""The one place that decides where a project's files live (roadmap item M1.2, ADR-0001).

No other module may build a project output path. ``tests/test_paths.py``
enforces this with a grep for the legacy ``output-`` / ``discovery-{`` literals.

Two layouts:

* **v1** (``ProjectLayout.for_repo``) — state lives inside the repository::

      <repo>/.discovery/
        config.yaml     committed project config (no secrets)
        .gitignore      "*" then "!config.yaml" "!.gitignore"
        discovery.db    SQLite: graph, pages, runs, caches
        cache/  logs/  exports/  docs/
        lock            single-flight run lock (M1.4)

  ``state_dir=`` relocates the whole tree for read-only or remote sources.

* **legacy** (``ProjectLayout.legacy``) — the pre-v1 CLI layout that the
  current ``-o/--docs-root/-p`` commands still use until the v1 CLI (M2)::

      <output>/output-<slug>/discovery-<slug>.db   (+ artifacts, clones)
      <docs_root>/output-<slug>/<PREFIX>/*.md

  For reads, a legacy ``<output>/<slug>/`` tree is used when only that exists.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

STATE_DIRNAME = ".discovery"
_GITIGNORE = "*\n!config.yaml\n!.gitignore\n"


def repo_root(path: Path | str) -> Path:
    """Top of the git work tree containing *path* (worktree-safe), else *path* itself."""
    p = Path(path).expanduser().resolve()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=p, capture_output=True,
            text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return p
    if out.returncode == 0 and out.stdout.strip():
        return Path(out.stdout.strip()).resolve()
    return p


def find_project(start: Path | str) -> Path | None:
    """Walk up from *start* to the first directory holding ``.discovery/``."""
    p = Path(start).expanduser().resolve()
    for candidate in (p, *p.parents):
        if (candidate / STATE_DIRNAME).is_dir():
            return candidate
    return None


@dataclass(frozen=True)
class ProjectLayout:
    slug: str
    state_dir: Path       # DB, intermediate artifacts, clones, caches
    docs_dir: Path        # rendered markdown tree
    db_path: Path
    layout: str = "v1"    # "v1" | "legacy"
    repo: Path | None = None

    # ── constructors ──────────────────────────────────────────────────────
    @classmethod
    def for_repo(cls, repo: Path | str, *, state_dir: Path | str | None = None,
                 slug: str | None = None) -> "ProjectLayout":
        """v1 layout for the repository containing *repo*."""
        root = repo_root(repo)
        state = Path(state_dir).expanduser().resolve() if state_dir else root / STATE_DIRNAME
        return cls(
            slug=slug or root.name,
            state_dir=state,
            docs_dir=state / "docs",
            db_path=state / "discovery.db",
            layout="v1",
            repo=root,
        )

    @classmethod
    def legacy(cls, output_root: Path | str, slug: str, docs_root: Path | str = "./data",
               *, for_read: bool = False) -> "ProjectLayout":
        """Pre-v1 layout used by the current CLI.

        ``for_read=True`` falls back to the older ``<root>/<slug>/`` tree when
        the ``output-<slug>`` one doesn't exist (scans from before Track 3).
        """
        state = cls._legacy_dir(Path(output_root), slug, for_read)
        docs = cls._legacy_dir(Path(docs_root), slug, for_read)
        return cls(
            slug=slug,
            state_dir=state,
            docs_dir=docs,
            db_path=state / f"discovery-{slug}.db",
            layout="legacy",
        )

    @staticmethod
    def _legacy_dir(root: Path, slug: str, for_read: bool) -> Path:
        current = root / f"output-{slug}"
        older = root / slug
        if for_read and not current.exists() and older.exists():
            return older
        return current

    # ── derived paths ─────────────────────────────────────────────────────
    @property
    def cache_dir(self) -> Path:
        return self.state_dir / "cache"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def exports_dir(self) -> Path:
        return self.state_dir / "exports"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "lock"

    @property
    def config_path(self) -> Path:
        return self.state_dir / "config.yaml"

    @property
    def graph_export_path(self) -> Path:
        return self.state_dir / f"knowledge-graph-{self.slug}.json"

    def artifact(self, name: str) -> Path:
        """Path of an intermediate artifact (e.g. ``entity_state_machines.json``)."""
        return self.state_dir / name

    # ── creation ──────────────────────────────────────────────────────────
    def ensure(self) -> "ProjectLayout":
        """Create the state tree (and, for v1, its ``.gitignore``)."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.layout == "v1":
            gi = self.state_dir / ".gitignore"
            if not gi.exists():
                gi.write_text(_GITIGNORE, encoding="utf-8")
            for d in (self.cache_dir, self.logs_dir, self.exports_dir):
                d.mkdir(exist_ok=True)
        return self
