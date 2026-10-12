"""Run a keyless ai-discovery scan of one pinned repo and collect its metrics."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from . import metrics
from .repos import ROOT, Repo, Source


def discover_cmd() -> list[str]:
    exe = Path(sys.executable).with_name("discover")
    return [str(exe)] if exe.exists() else [sys.executable, "-c", "from ai_discovery.cli import main; main()"]


def scan_command(source: Path, state_dir: Path, *, prose: bool = False,
                 budget: Optional[float] = None) -> list[str]:
    # --state-dir keeps every write out of the scanned checkout (sample apps stay untouched)
    cmd = [*discover_cmd(), "-q", "--json", "-C", str(source), "--state-dir", str(state_dir),
           "scan", "--full", "--prose" if prose else "--no-prose"]
    if prose:
        cmd += ["--yes"] + (["--budget", str(budget)] if budget is not None else [])
    return cmd


def run(repo: Repo, source: Source, *, keep_state: Optional[Path] = None, prose: bool = False,
        budget: Optional[float] = None) -> dict:
    assert source.path is not None
    state = keep_state or Path(tempfile.mkdtemp(prefix=f"bench-{repo.id}-"))
    try:
        started = time.monotonic()
        proc = subprocess.run(scan_command(source.path, state, prose=prose, budget=budget),
                              capture_output=True, text=True)
        wall = round(time.monotonic() - started, 2)
        try:
            scan = json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            scan = {"ok": False, "error": {"message": (proc.stderr or proc.stdout)[-2000:]}}
        result = {"repo": repo.id, "sha": repo.sha, "stack": repo.stack, "tool": "ai-discovery",
                  "mode": "prose" if prose else "keyless", "source": source.how, "wall_seconds": wall,
                  "ok": bool(scan.get("ok"))}
        if not scan.get("ok"):
            result["error"] = scan.get("error")
            return result
        db = Path(scan["db"])
        result["cost_usd"] = scan.get("spend", {}).get("usd", 0)
        result["screens"] = scan.get("counts", {}).get("screens")
        result["scan_status"] = scan.get("status")
        result["docs"] = scan.get("counts", {}).get("docs")
        result["pages_hook"] = next((h.get("detail") for h in scan.get("hooks", []) if h.get("name") == "pages"), None)
        result.update(metrics.collect(db, scan["scan_id"], source.path))
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            gt_path = ROOT / repo.ground_truth if repo.ground_truth else None
            result["ground_truth"] = metrics.ground_truth_recall(
                conn, scan["scan_id"], gt_path, repo.sha, repo.ground_truth_sha)
        finally:
            conn.close()
        # Rule 6: an arm that indexed nothing is a void cell, not a zero score.
        result["void"] = result["files"]["indexed"] == 0
        return result
    finally:
        if keep_state is None:
            shutil.rmtree(state, ignore_errors=True)
