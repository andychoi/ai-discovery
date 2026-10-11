"""Pinned benchmark repos: load ``repos.toml``, locate or fetch sources."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError:  # pragma: no cover — bench is dev tooling
    raise SystemExit("bench needs Python >= 3.11 (tomllib)") from None

ROOT = Path(__file__).resolve().parent.parent
REPOS_TOML = Path(__file__).with_name("repos.toml")
CACHE = ROOT / "tests" / "corpus" / "external" / ".cache"
REQUIRED = ("id", "url", "sha", "stack", "license", "status")


@dataclass(frozen=True)
class Repo:
    id: str
    url: str
    sha: str
    stack: str
    license: str
    status: str
    local: Optional[str] = None
    ground_truth: Optional[str] = None
    ground_truth_sha: Optional[str] = None


def load_repos(path: Path = REPOS_TOML) -> dict[str, Repo]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    repos: dict[str, Repo] = {}
    for entry in data.get("repo", []):
        missing = [k for k in REQUIRED if not entry.get(k)]
        if missing:
            raise ValueError(f"{path.name}: repo {entry.get('id', '?')} lacks {', '.join(missing)}")
        if len(entry["sha"]) != 40:
            raise ValueError(f"{path.name}: {entry['id']} sha must be a full 40-char commit")
        if entry["id"] in repos:
            raise ValueError(f"{path.name}: duplicate id {entry['id']}")
        repos[entry["id"]] = Repo(**entry)
    return repos


def _head(path: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


@dataclass(frozen=True)
class Source:
    path: Optional[Path]      # None → must be fetched first
    how: str                  # "local" | "cache" | "fetch-needed"
    note: str = ""


def locate(repo: Repo) -> Source:
    """Where the pinned source is, without touching the network."""
    notes = []
    if repo.local:
        local = (ROOT / repo.local).resolve()
        head = _head(local) if local.is_dir() else None
        if head == repo.sha:
            return Source(local, "local")
        notes.append(f"local {repo.local} is at {head[:12] if head else 'missing'}, pinned {repo.sha[:12]}")
    cached = CACHE / repo.id
    if cached.is_dir() and _head(cached) == repo.sha:
        return Source(cached, "cache", "; ".join(notes))
    return Source(None, "fetch-needed", "; ".join(notes))


def fetch(repo: Repo) -> Path:
    """Shallow-fetch exactly the pinned commit into the cache (idempotent)."""
    dest = CACHE / repo.id
    if dest.is_dir() and _head(dest) == repo.sha:
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    git = ["git", "-C", str(dest)]
    if not (dest / ".git").is_dir():
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "remote", "add", "origin", repo.url], check=True)
    subprocess.run([*git, "fetch", "-q", "--depth", "1", "origin", repo.sha], check=True)
    subprocess.run([*git, "checkout", "-q", "FETCH_HEAD"], check=True)
    if _head(dest) != repo.sha:
        raise RuntimeError(f"{repo.id}: fetched HEAD is not {repo.sha}")
    return dest


def verify_remote(repo: Repo) -> bool:
    """True when the pinned commit is fetchable from the remote (network)."""
    probe = CACHE / f".verify-{repo.id}"
    probe.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["git", "-C", str(probe), "init", "-q"], check=True)
        res = subprocess.run(["git", "-C", str(probe), "fetch", "-q", "--depth", "1", repo.url, repo.sha],
                             capture_output=True, timeout=300)
        return res.returncode == 0
    finally:
        subprocess.run(["rm", "-rf", str(probe)], check=False)
