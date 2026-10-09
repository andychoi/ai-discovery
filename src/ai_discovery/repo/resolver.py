"""Resolve a git repo URL or local path to a ResolvedRepo."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core.redact import redact_text
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


_GIT_TIMEOUT = int(os.environ.get("DISCOVERY_GIT_TIMEOUT", "900"))


def _token_for(repo: str) -> tuple[str, str] | None:
    """(username, token) for private https repos, or None.

    Checks GITHUB_TOKEN / GH_TOKEN (github.com) and DISCOVERY_GITEA_TOKEN /
    GITEA_TOKEN (other hosts).
    """
    if not repo.startswith("https://"):
        return None
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if gh_token and "github.com" in repo:
        return "x-access-token", gh_token
    gitea_token = os.environ.get("DISCOVERY_GITEA_TOKEN") or os.environ.get("GITEA_TOKEN")
    if gitea_token and "github.com" not in repo:
        return "git", gitea_token
    return None


def _git_env(repo: str | None = None) -> dict[str, str]:
    """Environment for git subprocesses (M0.8).

    Credentials go in an ``http.extraHeader`` supplied through
    ``GIT_CONFIG_COUNT``/``KEY``/``VALUE`` env vars (git >= 2.31): never in the
    clone URL (which git persists to .git/config) and never on the command line
    (visible in the process list). Prompts are disabled so a missing credential
    fails fast instead of hanging.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    cred = _token_for(repo) if repo else None
    if cred:
        basic = base64.b64encode(f"{cred[0]}:{cred[1]}".encode()).decode()
        env.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        })
    return env


def _git(args: list[str], *, cwd: Path | None = None, repo: str | None = None,
         check: bool = False) -> subprocess.CompletedProcess:
    """Run git with a timeout and credential env; redact secrets in errors."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, env=_git_env(repo),
            capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {args[0]} timed out after {_GIT_TIMEOUT}s") from exc
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {redact_text(result.stderr.strip())}")
    return result


def _clean_url(url: str) -> str:
    """Strip any user:token@ credentials from an https URL."""
    return re.sub(r"^(https?://)[^/@]+@", r"\1", url)


def _scrub_remote(clone_path: Path) -> None:
    """Remove credentials an older version embedded in origin's URL (M0.8)."""
    current = _git(["remote", "get-url", "origin"], cwd=clone_path)
    url = current.stdout.strip()
    if current.returncode == 0 and _clean_url(url) != url:
        _git(["remote", "set-url", "origin", _clean_url(url)], cwd=clone_path, check=True)


def _get_commit_sha(repo_path: Path) -> str:
    """Get HEAD commit SHA via git rev-parse."""
    return _git(["rev-parse", "HEAD"], cwd=repo_path, check=True).stdout.strip()


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
        clean_repo = _clean_url(repo)

        if clone_path.exists() and (clone_path / ".git").exists():
            _scrub_remote(clone_path)
            # Already cloned — fetch, then checkout the requested branch with
            # fallback to the remote's actual default branch.
            _git(["fetch", "origin"], cwd=clone_path, repo=clean_repo, check=True)
            result = _git(["checkout", branch], cwd=clone_path)
            if result.returncode != 0:
                # Requested branch doesn't exist — resolve the remote default.
                # Try origin/HEAD first; if not set, ask the remote to auto-set it.
                head = _git(["rev-parse", "--abbrev-ref", "origin/HEAD"], cwd=clone_path)
                if head.returncode != 0:
                    _git(["remote", "set-head", "origin", "--auto"], cwd=clone_path, repo=clean_repo)
                    head = _git(["rev-parse", "--abbrev-ref", "origin/HEAD"], cwd=clone_path)
                branch = head.stdout.strip().removeprefix("origin/") if head.returncode == 0 else "master"
                _git(["checkout", branch], cwd=clone_path, check=True)
            _git(["pull", "origin", branch], cwd=clone_path, repo=clean_repo, check=True)
        else:
            # Fresh clone — try requested branch first, fall back to default.
            # The persisted remote URL is credential-free (M0.8).
            result = _git(["clone", "--branch", branch, clean_repo, str(clone_path)], repo=clean_repo)
            if result.returncode != 0:
                fallback = _git(["clone", clean_repo, str(clone_path)], repo=clean_repo)
                if fallback.returncode != 0:
                    hint = redact_text(fallback.stderr.strip() or result.stderr.strip())
                    raise RuntimeError(
                        f"git clone failed for {clean_repo}\n{hint}"
                    )
                # Resolve actual default branch
                head = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=clone_path, check=True)
                branch = head.stdout.strip()

        commit_sha = _get_commit_sha(clone_path)
        return ResolvedRepo(
            repo_path=clone_path,
            is_local=False,
            url=clean_repo,
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
