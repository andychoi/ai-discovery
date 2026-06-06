#!/usr/bin/env python
"""Fetch the pinned external corpus repos into .cache/ (gitignored).

Idempotent: a repo already at its pinned SHA is left untouched; a repo at a
different SHA is re-pinned (fetch --depth 1 <sha> + checkout). Run from
anywhere:

    python tests/corpus/external/fetch.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = ROOT / ".cache"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def fetch_repo(repo: dict) -> None:
    dest = CACHE / repo["name"]
    sha = repo["sha"]
    if dest.is_dir():
        head = _git("-C", str(dest), "rev-parse", "HEAD")
        if head == sha:
            print(f"  ok        {repo['name']} @ {sha[:12]}")
            return
        print(f"  re-pin    {repo['name']} {head[:12]} -> {sha[:12]}")
        _git("-C", str(dest), "fetch", "--depth", "1", "origin", sha)
        _git("-C", str(dest), "checkout", "-q", sha)
        return
    print(f"  fetching  {repo['name']} @ {sha[:12]}")
    CACHE.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", str(dest))
    _git("-C", str(dest), "remote", "add", "origin", repo["url"])
    _git("-C", str(dest), "fetch", "--depth", "1", "origin", sha)
    _git("-C", str(dest), "checkout", "-q", "FETCH_HEAD")


def main() -> int:
    manifest = json.loads((ROOT / "manifest.json").read_text())
    for repo in manifest["repos"]:
        fetch_repo(repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
