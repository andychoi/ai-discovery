"""ProjectLayout — the single path resolver (roadmap item M1.2, ADR-0001)."""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

from ai_discovery.core.paths import STATE_DIRNAME, ProjectLayout, find_project, repo_root

SRC = Path(__file__).resolve().parent.parent / "src" / "ai_discovery"


def _git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


# ── v1 layout ─────────────────────────────────────────────────────────────

def test_for_repo_uses_git_toplevel_from_subdir(tmp_path: Path) -> None:
    repo = _git_init(tmp_path / "shop")
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    layout = ProjectLayout.for_repo(sub)
    assert layout.repo == repo.resolve()
    assert layout.state_dir == repo.resolve() / STATE_DIRNAME
    assert layout.db_path == layout.state_dir / "discovery.db"
    assert layout.slug == "shop" and layout.layout == "v1"


def test_for_repo_non_git_falls_back_to_path(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    assert ProjectLayout.for_repo(folder).state_dir == folder.resolve() / STATE_DIRNAME


def test_worktree_resolves_to_its_own_root(tmp_path: Path) -> None:
    repo = _git_init(tmp_path / "main")
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qam", "i", "--allow-empty"],
                   cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "f"], cwd=repo, check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", str(wt)], cwd=repo, check=True)
    assert (wt / ".git").is_file()  # worktrees have a .git *file*
    assert repo_root(wt) == wt.resolve()


def test_state_dir_override_for_readonly_sources(tmp_path: Path) -> None:
    repo = _git_init(tmp_path / "ro")
    layout = ProjectLayout.for_repo(repo, state_dir=tmp_path / "elsewhere", slug="custom")
    assert layout.state_dir == (tmp_path / "elsewhere").resolve()
    assert layout.slug == "custom"
    layout.ensure()
    assert not (repo / STATE_DIRNAME).exists()


def test_ensure_creates_tree_and_gitignore(tmp_path: Path) -> None:
    layout = ProjectLayout.for_repo(_git_init(tmp_path / "r")).ensure()
    assert (layout.state_dir / ".gitignore").read_text() == "*\n!config.yaml\n!.gitignore\n"
    for d in (layout.cache_dir, layout.logs_dir, layout.exports_dir):
        assert d.is_dir()
    assert layout.lock_path.name == "lock" and layout.config_path.name == "config.yaml"


def test_find_project_walks_up(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / STATE_DIRNAME).mkdir(parents=True)
    deep = root / "a" / "b"
    deep.mkdir(parents=True)
    assert find_project(deep) == root.resolve()
    assert find_project(tmp_path) is None


# ── legacy layout (current CLI) ───────────────────────────────────────────

def test_legacy_layout_matches_pre_v1_paths(tmp_path: Path) -> None:
    layout = ProjectLayout.legacy(tmp_path / "out", "shop", tmp_path / "docs")
    assert layout.state_dir == tmp_path / "out" / "output-shop"
    assert layout.docs_dir == tmp_path / "docs" / "output-shop"
    assert layout.db_path == tmp_path / "out" / "output-shop" / "discovery-shop.db"
    assert layout.graph_export_path.name == "knowledge-graph-shop.json"
    assert layout.artifact("interfaces.json") == layout.state_dir / "interfaces.json"


def test_legacy_read_falls_back_to_pre_track3_tree(tmp_path: Path) -> None:
    (tmp_path / "out" / "shop").mkdir(parents=True)
    assert ProjectLayout.legacy(tmp_path / "out", "shop", for_read=True).state_dir == tmp_path / "out" / "shop"
    # writers never fall back
    assert ProjectLayout.legacy(tmp_path / "out", "shop").state_dir == tmp_path / "out" / "output-shop"


# ── guard: one resolver ───────────────────────────────────────────────────

def test_no_path_literals_outside_core_paths() -> None:
    pattern = re.compile(r"""["']output-|discovery-\{""")
    offenders = [
        f"{p.relative_to(SRC)}:{i}"
        for p in SRC.rglob("*.py") if p.name != "paths.py" or p.parent.name != "core"
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, f"build project paths via core.paths.ProjectLayout: {offenders}"


