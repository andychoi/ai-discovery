"""Resolve a git repo URL or local path to a ResolvedRepo."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .lang_detector import _SKIP_DIRS


@dataclass
class ResolvedRepo:
    repo_path: Path
    is_local: bool
    url: str | None
    branch: str
    commit_sha: str


def _is_url(repo: str) -> bool:
    """Check if input looks like a git URL (https://, git@, ssh://)."""
    return repo.startswith("https://") or repo.startswith("git@") or repo.startswith("ssh://")


def _auth_url(repo: str) -> str:
    """Inject a token into https URLs for private repos.

    Checks GITHUB_TOKEN (for github.com) and GITEA_TOKEN / DISCOVERY_GITEA_TOKEN
    (for Gitea hosts). Returns the URL unchanged if no matching token is found.
    """
    if not repo.startswith("https://"):
        return repo
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if gh_token and "github.com" in repo:
        return repo.replace("https://", f"https://x-access-token:{gh_token}@")
    gitea_token = os.environ.get("DISCOVERY_GITEA_TOKEN") or os.environ.get("GITEA_TOKEN")
    if gitea_token and "github.com" not in repo:
        return repo.replace("https://", f"https://git:{gitea_token}@")
    return repo


def _get_commit_sha(repo_path: Path) -> str:
    """Get HEAD commit SHA via git rev-parse."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _fingerprint_dir(root: Path) -> str:
    """Return a stable content fingerprint for a non-git directory scan.

    Uses relative path + file size + mtime_ns to avoid reading every file body
    while still changing when files are added, removed, or updated.
    """
    digest = hashlib.sha1()

    def _walk(path: Path) -> None:
        try:
            entries = sorted(path.iterdir(), key=lambda p: p.name)
        except PermissionError:
            return

        for entry in entries:
            if entry.is_dir():
                if entry.name in _SKIP_DIRS:
                    continue
                _walk(entry)
                continue

            try:
                stat = entry.stat()
            except OSError:
                continue

            rel = entry.relative_to(root).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(str(stat.st_mtime_ns).encode("ascii"))

    _walk(root)
    return digest.hexdigest()


def resolve_repo(repo: str, branch: str, work_dir: Path) -> ResolvedRepo:
    """Resolve a git repo string to a ResolvedRepo.

    For URLs: clone to work_dir/repo-name/, or pull if already cloned.
    For local paths: use git metadata when available, otherwise treat the
    directory as a plain folder scan and compute a content fingerprint.
    Returns ResolvedRepo with commit SHA.
    """
    if _is_url(repo):
        # Derive repo name from URL
        name = repo.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        clone_path = work_dir / name
        clone_url = _auth_url(repo)

        if clone_path.exists() and (clone_path / ".git").exists():
            # Already cloned — fetch, then checkout the requested branch with
            # fallback to the remote's actual default branch.
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=clone_path,
                capture_output=True,
                text=True,
                check=True,
            )
            result = subprocess.run(
                ["git", "checkout", branch],
                cwd=clone_path,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                # Requested branch doesn't exist — resolve the remote default.
                # Try origin/HEAD first; if not set, ask the remote to auto-set it.
                head = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
                    cwd=clone_path,
                    capture_output=True,
                    text=True,
                )
                if head.returncode != 0:
                    subprocess.run(
                        ["git", "remote", "set-head", "origin", "--auto"],
                        cwd=clone_path,
                        capture_output=True,
                    )
                    head = subprocess.run(
                        ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
                        cwd=clone_path,
                        capture_output=True,
                        text=True,
                    )
                branch = head.stdout.strip().removeprefix("origin/") if head.returncode == 0 else "master"
                subprocess.run(
                    ["git", "checkout", branch],
                    cwd=clone_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
            subprocess.run(
                ["git", "pull", "origin", branch],
                cwd=clone_path,
                capture_output=True,
                text=True,
                check=True,
            )
        else:
            # Fresh clone — try requested branch first, fall back to default
            result = subprocess.run(
                ["git", "clone", "--branch", branch, clone_url, str(clone_path)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                fallback = subprocess.run(
                    ["git", "clone", clone_url, str(clone_path)],
                    capture_output=True,
                    text=True,
                )
                if fallback.returncode != 0:
                    hint = fallback.stderr.strip() or result.stderr.strip()
                    raise RuntimeError(
                        f"git clone failed for {repo}\n{hint}"
                    )
                # Resolve actual default branch
                head = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=clone_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                branch = head.stdout.strip()

        commit_sha = _get_commit_sha(clone_path)
        return ResolvedRepo(
            repo_path=clone_path,
            is_local=False,
            url=repo,
            branch=branch,
            commit_sha=commit_sha,
        )

    # Local path
    local_path = Path(repo).resolve()
    if not local_path.exists() or not local_path.is_dir():
        raise ValueError(f"{local_path} is not a readable directory")

    if (local_path / ".git").exists():
        commit_sha = _get_commit_sha(local_path)
        resolved_branch = branch
    else:
        commit_sha = _fingerprint_dir(local_path)
        resolved_branch = "folder"

    return ResolvedRepo(
        repo_path=local_path,
        is_local=True,
        url=None,
        branch=resolved_branch,
        commit_sha=commit_sha,
    )
