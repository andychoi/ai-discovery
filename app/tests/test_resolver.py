"""Tests for repo resolver."""

import subprocess
from pathlib import Path

import pytest

from app.repo.resolver import ResolvedRepo, _is_url, resolve_repo


def test_resolve_local_path(tmp_path: Path):
    """Local dir with .git resolves with is_local=True."""
    # Set up a real git repo in tmp_path
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    result = resolve_repo(str(tmp_path), "main", tmp_path / "work")

    assert isinstance(result, ResolvedRepo)
    assert result.is_local is True
    assert result.url is None
    assert result.repo_path == tmp_path
    assert len(result.commit_sha) == 40


def test_resolve_plain_folder(tmp_path: Path):
    """Dir without .git resolves as a folder scan with a stable fingerprint."""
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")

    result = resolve_repo(str(tmp_path), "main", tmp_path / "work")

    assert isinstance(result, ResolvedRepo)
    assert result.is_local is True
    assert result.url is None
    assert result.repo_path == tmp_path
    assert result.branch == "folder"
    assert len(result.commit_sha) == 40


def test_resolve_plain_folder_fingerprint_changes_on_edit(tmp_path: Path):
    file_path = tmp_path / "app.py"
    file_path.write_text("print('hi')\n", encoding="utf-8")
    first = resolve_repo(str(tmp_path), "main", tmp_path / "work")

    file_path.write_text("print('changed')\n", encoding="utf-8")
    second = resolve_repo(str(tmp_path), "main", tmp_path / "work")

    assert first.commit_sha != second.commit_sha


def test_resolve_url_detected():
    """_is_url returns True for https/git@ URLs, False for local paths."""
    assert _is_url("https://github.com/org/repo.git") is True
    assert _is_url("git@github.com:org/repo.git") is True
    assert _is_url("ssh://git@github.com/org/repo.git") is True
    assert _is_url("/home/user/my-repo") is False
    assert _is_url("./relative-path") is False
    assert _is_url("my-repo") is False
