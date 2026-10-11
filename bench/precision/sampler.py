"""Seeded, stratified sample of emitted call edges for hand grading.

Strata are (caller language × origin), where origin is ``call_edges.resolved_by``
(``unresolved`` for edges without a target). Up to ``per_stratum`` edges are
drawn from each stratum with ``random.Random(seed)`` over a canonical ordering,
so the same scan + seed always yields the same sheet regardless of row ids.

``caller_line`` is where the calling method starts — ``call_edges`` stores no
call-site line yet — so the grader reads the call inside that method.
"""

from __future__ import annotations

import csv
import random
import sqlite3
from collections import defaultdict
from pathlib import Path

COLUMNS = ["language", "origin", "caller", "caller_file", "caller_line", "callee_name",
           "resolved_to", "resolved_file", "resolved_line", "confidence", "verdict", "reason"]
VERDICTS = ("correct", "wrong", "unknown")


def _rel(path: str | None, repo: Path | None) -> str:
    if not path:
        return ""
    if repo is not None:
        try:
            return Path(path).resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            pass
    return path


def emitted_edges(db: Path, scan_id: int, repo: Path | None = None) -> list[dict]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT caller.language AS language,
                      COALESCE(NULLIF(ce.resolved_by, ''), CASE WHEN ce.callee_id IS NULL
                               THEN 'unresolved' ELSE '(none)' END) AS origin,
                      caller.qualified_name AS caller, caller.file_path AS caller_file,
                      caller.line_start AS caller_line, ce.callee_name AS callee_name,
                      callee.qualified_name AS resolved_to, callee.file_path AS resolved_file,
                      callee.line_start AS resolved_line, ce.confidence AS confidence
                 FROM call_edges ce
                 JOIN code_nodes caller ON caller.id = ce.caller_id
                 LEFT JOIN code_nodes callee ON callee.id = ce.callee_id
                WHERE ce.scan_id = ?""", (scan_id,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["caller_file"] = _rel(d["caller_file"], repo)
        d["resolved_file"] = _rel(d["resolved_file"], repo)
        d["resolved_to"] = d["resolved_to"] or ""
        d["confidence"] = round(d["confidence"] or 0.0, 3)
        out.append(d)
    return out


def _key(e: dict) -> tuple:
    return (e["caller"], e["callee_name"] or "", e["resolved_to"], e["caller_file"], e["caller_line"] or 0)


def stratified_sample(edges: list[dict], *, seed: int, per_stratum: int = 30) -> list[dict]:
    strata: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in edges:
        strata[(e["language"] or "?", e["origin"])].append(e)
    rng = random.Random(seed)
    picked: list[dict] = []
    for stratum in sorted(strata):
        pool = sorted(strata[stratum], key=_key)
        chosen = pool if len(pool) <= per_stratum else rng.sample(pool, per_stratum)
        picked.extend(sorted(chosen, key=_key))
    return picked


def write_sheet(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({**{c: r.get(c, "") for c in COLUMNS}, "verdict": "", "reason": ""})
