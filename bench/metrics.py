"""Metrics from a finished keyless scan (design/benchmark-protocol.md §3).

Everything here is read from the scan DB plus a file listing of the scanned
checkout. Skipped-file reasons are classified by the harness (the walker does
not record them yet): ``skip_dir`` (inside an excluded directory),
``unsupported_extension`` (no parser for it), ``no_symbols`` (parsed but
produced no nodes).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Optional


def _repo_files(repo: Path) -> list[str]:
    """Repo-relative paths: tracked files when it's a git checkout, else every file."""
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True)
    if out.returncode == 0 and out.stdout:
        return sorted(p for p in out.stdout.decode("utf-8", "replace").split("\0") if p)
    return sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts)


def file_coverage(conn: sqlite3.Connection, scan_id: int, repo: Path) -> dict:
    from ai_discovery.repo.file_walker import _ALL_EXTENSIONS
    from ai_discovery.repo.lang_detector import _SKIP_DIRS

    repo = repo.resolve()
    indexed_abs = {r[0] for r in conn.execute("SELECT DISTINCT file_path FROM code_nodes WHERE scan_id = ?",
                                              (scan_id,))}
    indexed = set()
    for p in indexed_abs:
        try:
            indexed.add(Path(p).resolve().relative_to(repo).as_posix())
        except ValueError:
            indexed.add(Path(p).as_posix())
    skipped: Counter = Counter()
    unsupported_ext: Counter = Counter()
    n_indexed = 0
    for rel in _repo_files(repo):
        parts = Path(rel).parts
        ext = Path(rel).suffix.lower()
        if rel in indexed:
            n_indexed += 1
        elif any(part in _SKIP_DIRS for part in parts[:-1]):
            skipped["skip_dir"] += 1
        elif ext not in _ALL_EXTENSIONS:
            skipped["unsupported_extension"] += 1
            unsupported_ext[ext or "(none)"] += 1
        else:
            skipped["no_symbols"] += 1
    return {"indexed": n_indexed, "skipped": dict(sorted(skipped.items())),
            "top_unsupported_extensions": dict(unsupported_ext.most_common(10))}


def _edges(conn: sqlite3.Connection, scan_id: int) -> list[SimpleNamespace]:
    rows = conn.execute(
        """SELECT caller.qualified_name AS caller, COALESCE(callee.qualified_name, ce.callee_name) AS callee,
                  ce.confidence AS confidence
             FROM call_edges ce
             JOIN code_nodes caller ON caller.id = ce.caller_id
             LEFT JOIN code_nodes callee ON callee.id = ce.callee_id
            WHERE ce.scan_id = ?""", (scan_id,)).fetchall()
    return [SimpleNamespace(caller=r[0] or "", callee=r[1] or "", confidence=r[2] or 0.0) for r in rows]


def ground_truth_recall(conn: sqlite3.Connection, scan_id: int, gt_path: Optional[Path],
                        scanned_sha: str, gt_sha: Optional[str]) -> dict:
    """Recall of the curated key call edges for THIS repo (never pooled)."""
    if gt_path is None:
        return {"status": "no_ground_truth"}
    if gt_sha and scanned_sha != gt_sha:
        return {"status": "sha_mismatch", "scanned": scanned_sha, "ground_truth_sha": gt_sha}
    from tests.corpus.metrics import score_call_edges

    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    expected = gt.get("key_call_edges", [])
    score = score_call_edges(_edges(conn, scan_id), expected)
    return {"status": "ok", "call_edge_recall": round(score.recall, 3), "labelled": len(expected),
            "missing": [f"{m.get('caller_suffix', '*')} -> {m['callee_suffix']}" if isinstance(m, dict) else str(m)
                        for m in score.missing]}


def collect(db: Path, scan_id: int, repo: Path) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        nodes = dict(conn.execute("SELECT node_type, COUNT(*) FROM code_nodes WHERE scan_id = ? GROUP BY 1 "
                                  "ORDER BY 1", (scan_id,)).fetchall())
        by_origin = dict(conn.execute("SELECT COALESCE(NULLIF(resolved_by, ''), '(none)'), COUNT(*) "
                                      "FROM call_edges WHERE scan_id = ? GROUP BY 1 ORDER BY 1",
                                      (scan_id,)).fetchall())
        unresolved_edges = conn.execute("SELECT COUNT(*) FROM call_edges WHERE scan_id = ? AND callee_id IS NULL",
                                        (scan_id,)).fetchone()[0]
        try:
            unresolved_calls = conn.execute("SELECT COUNT(*) FROM unresolved_calls WHERE scan_id = ?",
                                            (scan_id,)).fetchone()[0]
        except sqlite3.OperationalError:
            unresolved_calls = None
        out = {
            "files": file_coverage(conn, scan_id, repo),
            "nodes_by_kind": nodes,
            "edges_total": sum(by_origin.values()),
            "edges_by_resolved_by": by_origin,
            "unresolved": {"edges_without_target": unresolved_edges, "unresolved_calls_rows": unresolved_calls},
        }
        return out
    finally:
        conn.close()
