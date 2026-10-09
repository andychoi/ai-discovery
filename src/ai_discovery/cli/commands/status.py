"""`discover status` — what is indexed, how fresh it is, what it cost (roadmap item M2.5).

Exit 0 when an index exists, 5 (``not_indexed``) when none does.
Stale-page and degradation counts arrive with the page model / stage runner
(M1.5, M1.8, M3.5); until then status reports what the legacy tables hold.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Optional

import typer

from ...core.errors import DiscoveryError
from ..context import get_globals
from ..output import emit, handle_errors
from ._index import IndexRef, find_index


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def freshness(repo: Path, indexed_commit: Optional[str]) -> dict[str, Any]:
    """HEAD vs the indexed commit: behind-by count and dirty working tree."""
    head = _git(repo, "rev-parse", "HEAD")
    if head is None:
        return {"git": False}
    info: dict[str, Any] = {"git": True, "head": head, "indexed": indexed_commit,
                            "dirty": bool(_git(repo, "status", "--porcelain"))}
    if indexed_commit and indexed_commit != head:
        count = _git(repo, "rev-list", "--count", f"{indexed_commit}..HEAD")
        info["behind"] = int(count) if count and count.isdigit() else None  # None: commit not in history
    else:
        info["behind"] = 0 if indexed_commit else None
    return info


_RUN_COLS = "id, status, commit_sha, branch, started_at, finished_at, repo_path"


def _latest_run(conn: sqlite3.Connection) -> Optional[dict]:
    """The scan readers see (``current_scan``, M1.5); the newest run otherwise."""
    from ...store.scans import get_current_scan

    current = get_current_scan(conn)
    if current is not None:
        row = conn.execute(f"SELECT {_RUN_COLS} FROM scan_runs WHERE id = ?", (current,)).fetchone()
        if row:
            return {**dict(row), "current": True}
    row = conn.execute(f"SELECT {_RUN_COLS} FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
    return {**dict(row), "current": False} if row else None


def _costs(conn: sqlite3.Connection, scan_id: int) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT tier, calls, est_usd FROM llm_costs WHERE scan_id = ?", (scan_id,)).fetchall()
    total = sum(r["est_usd"] or 0 for r in rows)
    return {"usd": round(total, 4), "calls": sum(r["calls"] or 0 for r in rows),
            "by_tier": {r["tier"]: round(r["est_usd"] or 0, 4) for r in rows}}


def _counts(conn: sqlite3.Connection, scan_id: int) -> dict[str, int]:
    out = {}
    for table in ("code_nodes", "call_edges", "domains", "screen_specs", "generated_docs"):
        try:
            out[table] = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE scan_id = ?", (scan_id,)).fetchone()[0]
        except sqlite3.OperationalError:
            out[table] = 0
    return out


def _encoding_fallbacks(conn: sqlite3.Connection, scan_id: int) -> int:
    rows = conn.execute(
        "SELECT DISTINCT file_path FROM code_nodes WHERE scan_id = ? AND framework_hints LIKE '%source_encoding%'",
        (scan_id,)).fetchall()
    return len(rows)


def collect(index: IndexRef) -> dict[str, Any]:
    from ...core.lock import lock_status

    conn = sqlite3.connect(f"file:{index.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        run = _latest_run(conn)
        result: dict[str, Any] = {"index": {"db": str(index.db_path), "layout": index.kind}, "run": run}
        if run:
            result["counts"] = _counts(conn, run["id"])
            result["costs"] = _costs(conn, run["id"])
            result["degradations"] = {"encoding_fallback_files": _encoding_fallbacks(conn, run["id"])}
    finally:
        conn.close()
    result["freshness"] = freshness(index.repo, run["commit_sha"] if run else None)
    state, holder = lock_status(index.lock_path)
    result["lock"] = {"state": state, "pid": holder.get("pid"), "since": holder.get("started_at")}
    return result


def _render(res: dict[str, Any]):
    def human(console) -> None:
        idx, run, fr = res["index"], res.get("run"), res["freshness"]
        console.print(f"[bold]Index[/]   {idx['db']} ({idx['layout']} layout)", highlight=False)
        if run:
            colour = "green" if run["status"] == "completed" else "yellow"
            console.print(f"[bold]Last run[/] #{run['id']} [{colour}]{run['status']}[/] "
                          f"at {run.get('finished_at') or run['started_at']} "
                          f"commit {(run['commit_sha'] or '')[:10]}", highlight=False)
        if fr.get("git"):
            behind = fr.get("behind")
            fresh = ("up to date" if behind == 0 else
                     f"{behind} commit(s) behind HEAD" if behind else "indexed commit not in history")
            console.print(f"[bold]Fresh[/]   {fresh}" + ("; working tree has uncommitted changes" if fr["dirty"] else ""),
                          highlight=False)
        if "counts" in res:
            c = res["counts"]
            console.print(f"[bold]Content[/] {c['code_nodes']} nodes, {c['call_edges']} edges, "
                          f"{c['domains']} domains, {c['screen_specs']} screens, {c['generated_docs']} docs",
                          highlight=False)
            console.print(f"[bold]Spend[/]   ${res['costs']['usd']:.4f} over {res['costs']['calls']} LLM call(s)",
                          highlight=False)
            enc = res["degradations"]["encoding_fallback_files"]
            if enc:
                console.print(f"[bold]Notes[/]   {enc} file(s) decoded with a legacy encoding", highlight=False)
        lock = res["lock"]
        if lock["state"] != "none":
            console.print(f"[bold]Lock[/]    {lock['state']} (pid {lock['pid']} since {lock['since']})"
                          + ("  → discover doctor --repair" if lock["state"] in ("stale", "corrupt", "foreign") else ""),
                          highlight=False)
    return human


def register(app: typer.Typer) -> None:
    @app.command("status")
    @handle_errors
    def status(ctx: typer.Context) -> None:
        """Show what is indexed for this repo, how fresh it is, and what it cost."""
        g = get_globals(ctx)
        index, layout = find_index(g)
        if index is None:
            raise DiscoveryError(
                f"no index for {layout.repo or g.repo}", "not_indexed",
                hint="run `discover scan` (v1 index: <repo>/.discovery/discovery.db)")
        res = collect(index)
        emit(g, json.loads(json.dumps(res, default=str)), _render(res))
