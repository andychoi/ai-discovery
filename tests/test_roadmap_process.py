"""Process guards (docs/roadmap/README.md): the main-branch pre-commit hook and scripts/roadmap_land.py."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "githooks" / "pre-commit"
G = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([*G, *args], cwd=repo, capture_output=True, text=True, env={**os.environ, **(env or {})})


@pytest.fixture()
def repo(tmp_path) -> Path:
    r = tmp_path / "r"
    (r / "docs" / "roadmap" / "milestones").mkdir(parents=True)
    (r / "docs" / "roadmap" / "milestones" / "M9-x.md").write_text(
        "# M9\n\n### M9.1 Thing\n- **Status:** Planned\n")
    (r / "src").mkdir()
    (r / "src" / "a.py").write_text("x = 1\n")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    _git(r, "config", "core.hooksPath", str(HOOK.parent))
    return r


def test_hook_blocks_code_on_main_but_allows_bookkeeping(repo) -> None:
    (repo / "src" / "a.py").write_text("x = 2\n")
    _git(repo, "add", "-A")
    blocked = _git(repo, "commit", "-qm", "code on main")
    assert blocked.returncode == 1 and "refusing to commit code directly on main" in blocked.stderr
    _git(repo, "reset", "-q", "--hard")
    ms = repo / "docs" / "roadmap" / "milestones" / "M9-x.md"
    ms.write_text(ms.read_text().replace("Planned", "In progress (rm/M9.1-thing, 2026-10-31)"))
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-qm", "claim").returncode == 0
    (repo / "src" / "a.py").write_text("x = 3\n")
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-qm", "override", env={"ROADMAP_ALLOW_MAIN": "1"}).returncode == 0


def test_hook_allows_branches_and_squash_merges(repo) -> None:
    _git(repo, "checkout", "-qb", "rm/M9.1-thing")
    (repo / "src" / "a.py").write_text("x = 4\n")
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-qm", "feat: on a branch").returncode == 0
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--squash", "rm/M9.1-thing")
    assert _git(repo, "commit", "-qm", "feat: landed").returncode == 0


def _land_module(repo: Path):
    spec = importlib.util.spec_from_file_location("roadmap_land", ROOT / "scripts" / "roadmap_land.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = repo
    return mod


def _branch_with_item(repo: Path, *, done: bool) -> None:
    _git(repo, "checkout", "-qb", "rm/M9.1-thing")
    (repo / "src" / "b.py").write_text("y = 1\n")
    (repo / "src" / "a.py").unlink()
    ms = repo / "docs" / "roadmap" / "milestones" / "M9-x.md"
    ms.write_text(ms.read_text().replace("Planned", "Done (abc123)" if done else "In progress (x)"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feat(x): the thing\n\nbody line")
    _git(repo, "checkout", "-q", "main")


def test_land_happy_path(repo) -> None:
    _branch_with_item(repo, done=True)
    land = _land_module(repo)
    out = land.land("rm/M9.1-thing", push=False, dry_run=False, skip_tests=True)
    assert out.startswith("landed rm/M9.1-thing as ")
    shown = _git(repo, "show", "--name-status", "--format=%s", "HEAD").stdout
    assert "feat(x): the thing" in shown and "D\tsrc/a.py" in shown and "A\tsrc/b.py" in shown   # deletion kept
    assert _git(repo, "status", "--porcelain").stdout == ""
    assert "rm/M9.1-thing" not in _git(repo, "branch").stdout


def test_land_refusals(repo) -> None:
    _branch_with_item(repo, done=False)
    land = _land_module(repo)
    with pytest.raises(land.LandError, match="close it"):
        land.land("rm/M9.1-thing", push=False, dry_run=False, skip_tests=True)
    (repo / "dirty.txt").write_text("x")
    with pytest.raises(land.LandError, match="uncommitted"):
        land.land("rm/M9.1-thing", push=False, dry_run=True, skip_tests=True)
    (repo / "dirty.txt").unlink()
    head = _git(repo, "rev-parse", "HEAD").stdout
    with pytest.raises(land.LandError, match="no branch"):
        land.land("rm/nope", push=False, dry_run=False, skip_tests=True)
    assert _git(repo, "rev-parse", "HEAD").stdout == head                    # nothing changed on main
