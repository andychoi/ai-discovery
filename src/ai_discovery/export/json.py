"""Versioned knowledge-graph JSON (roadmap item M3.10; supersedes ``generators/graph_export``).

Portable and diffable: file paths are repo-relative, no source bodies, no timestamps of the export itself
(two exports of the same scan are byte-identical). ``schema`` names the document and ``schema_version``
changes only with a breaking change to the shape below.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any, Optional

SCHEMA = "ai-discovery/knowledge-graph"
SCHEMA_VERSION = 2          # v1 was `discover export-graph` (absolute paths, generated_at, legacy docs index)


def _rel(path: Optional[str], repo: Optional[Path]) -> Optional[str]:
    if not path:
        return path
    p = Path(path)
    if repo is not None and p.is_absolute():
        try:
            return PurePosixPath(*p.resolve().relative_to(repo.resolve()).parts).as_posix()
        except (ValueError, OSError):
            return None                           # outside the repo: never leak a local path
    return path.replace("\\", "/")


def _loads(raw: Optional[str], default: Any) -> Any:
    try:
        value = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def build(conn: sqlite3.Connection, scan_id: int, *, project: str, repo: Optional[Path]) -> dict:
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(scan_runs)")}
    worktree = "worktree" if "worktree" in cols else "'' AS worktree"     # read-only: stores before 0006
    scan = conn.execute(f"SELECT id, commit_sha, branch, status, finished_at, {worktree} FROM scan_runs WHERE id = ?",
                        (scan_id,)).fetchone()
    nodes = [{"id": r["qualified_name"], "name": r["name"], "type": r["node_type"], "language": r["language"],
              "file": _rel(r["file_path"], repo), "line_start": r["line_start"], "line_end": r["line_end"],
              "domain": r["domain"]}
             for r in conn.execute("SELECT qualified_name, name, node_type, language, file_path, line_start, "
                                   "line_end, domain FROM code_nodes WHERE scan_id = ? "
                                   "ORDER BY qualified_name, file_path, line_start", (scan_id,))]
    edges = [{"from": r["caller"], "to": r["callee"], "call": r["callee_name"], "kind": r["edge_kind"],
              "origin": r["resolved_by"] or "", "evidence": r["evidence_kind"], "confidence": r["confidence"]}
             for r in conn.execute(
                 "SELECT a.qualified_name AS caller, b.qualified_name AS callee, e.callee_name, e.edge_kind, "
                 "e.resolved_by, e.evidence_kind, e.confidence FROM call_edges e "
                 "JOIN code_nodes a ON a.id = e.caller_id LEFT JOIN code_nodes b ON b.id = e.callee_id "
                 "WHERE e.scan_id = ? ORDER BY caller, callee, e.callee_name, e.edge_kind", (scan_id,))]
    try:
        unresolved = [{"from": r[0], "call": r[1], "reason": r[2]} for r in conn.execute(
            "SELECT n.qualified_name, u.callee_name, u.reason FROM unresolved_calls u "
            "JOIN code_nodes n ON n.id = u.caller_id WHERE u.scan_id = ? ORDER BY 1, 2, 3", (scan_id,))]
    except sqlite3.OperationalError:
        unresolved = []
    domains = [{"name": r["name"], "symbols": r["node_count"], "entry_points": _loads(r["entry_points"], [])}
               for r in conn.execute("SELECT name, node_count, entry_points FROM domains WHERE scan_id = ? "
                                     "ORDER BY name", (scan_id,))]
    relationships = [{"from": r["from_entity"], "to": r["to_entity"], "from_field": r["from_field"],
                      "to_field": r["to_field"], "cardinality": r["cardinality"], "source": r["source"],
                      "inferred": bool(r["inferred"])}
                     for r in conn.execute("SELECT * FROM db_relationship WHERE scan_id = ? "
                                           "ORDER BY from_entity, to_entity, from_field", (scan_id,))]
    state_machines = []
    for r in conn.execute("SELECT entity, entity_id, states_json, fields_json, confidence FROM "
                          "entity_state_machines WHERE scan_id = ? ORDER BY entity_id, entity", (scan_id,)):
        transitions = [{"field": t[0], "from": t[1], "to": t[2], "trigger": t[3], "guard": t[4]}
                       for t in conn.execute(
                           "SELECT field, from_state, to_state, trigger_function, guard_expr FROM state_transitions "
                           "WHERE scan_id = ? AND entity_id = ? ORDER BY field, from_state, to_state, "
                           "trigger_function", (scan_id, r["entity_id"]))]
        state_machines.append({"entity": r["entity"], "id": r["entity_id"], "states": sorted(_loads(r["states_json"], [])),
                               "fields": sorted(_loads(r["fields_json"], [])), "confidence": r["confidence"],
                               "transitions": transitions})
    pages = [{"id": r["page_id"], "type": r["type"], "title": r["title"], "confidence": r["confidence"],
              "freshness": r["freshness"], "prose": bool(r["prose_ref"]),
              "links": [{"to": x["dst"], "kind": x["kind"]} for x in _loads(r["links_json"], [])]}
             for r in conn.execute("SELECT page_id, type, title, confidence, freshness, prose_ref, links_json "
                                   "FROM pages WHERE scan_id = ? ORDER BY page_id", (scan_id,))]
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "project": project,
        "scan": {"id": scan["id"], "commit": scan["commit_sha"], "branch": scan["branch"],
                 "status": scan["status"], "finished_at": scan["finished_at"],
                 "dirty": bool(scan["worktree"])},
        "counts": {"nodes": len(nodes), "edges": len(edges), "unresolved_calls": len(unresolved),
                   "domains": len(domains), "pages": len(pages)},
        "nodes": nodes, "edges": edges, "unresolved_calls": unresolved, "domains": domains,
        "relationships": relationships, "state_machines": state_machines, "pages": pages,
    }


def write(doc: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "knowledge-graph.json"
    path.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
