"""Canonical knowledge-graph JSON export (assessment 07 A-4).

One portable JSON view of a scan — nodes, call edges (confidence + resolution
stage), domains, FK relationships, FSMs, and a doc index — modeled on
Understand-Anything's committed `knowledge-graph.json`: teammates, viewers,
and federation consume results without shipping SQLite.

The export is a VIEW of the working store, never a migration: SQLite stays
primary (N-3 in the assessment), and node source code / doc bodies stay out
of the file — it's an index/topology artifact, sized to be committed and
diffed in PRs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..db import get_conn, now_iso
from ..graph.fsm_persistence import load_entity_state_machines

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def _json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw) if raw else []
        return value if isinstance(value, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def build_graph_export(db_path: Path, slug: str, scan_id: int | None = None) -> dict:
    """Build the canonical graph dict for *slug*'s latest (or given) scan.

    Raises ValueError when the DB holds no scan — an empty export would be
    indistinguishable from a real empty codebase.
    """
    conn = get_conn(db_path)
    try:
        if scan_id is None:
            row = conn.execute("SELECT MAX(id) AS id FROM scan_runs").fetchone()
            scan_id = row["id"] if row else None
        if scan_id is None:
            raise ValueError(f"No scan found in {db_path} — run `discover scan` first.")

        scan = conn.execute(
            "SELECT id, commit_sha, branch, started_at, finished_at, status "
            "FROM scan_runs WHERE id = ?",
            (scan_id,),
        ).fetchone()
        if scan is None:
            raise ValueError(f"No scan with id {scan_id} in {db_path}.")

        nodes = [
            {
                "id": r["qualified_name"],
                "name": r["name"],
                "node_type": r["node_type"],
                "language": r["language"],
                "file_path": r["file_path"],
                "line_start": r["line_start"],
                "line_end": r["line_end"],
                "domain": r["domain"],
                "file_hash": r["file_hash"] or "",
            }
            for r in conn.execute(
                "SELECT qualified_name, name, node_type, language, file_path, "
                "line_start, line_end, domain, file_hash "
                "FROM code_nodes WHERE scan_id = ? ORDER BY qualified_name",
                (scan_id,),
            )
        ]

        edges = [
            {
                "caller": r["caller"],
                "callee": r["callee"],  # qualified name, or null when unresolved
                "callee_name": r["callee_name"],
                "edge_type": r["edge_type"],
                "confidence": r["confidence"],
                "resolved_by": r["resolved_by"] or "",
            }
            for r in conn.execute(
                """SELECT caller.qualified_name AS caller,
                          callee.qualified_name AS callee,
                          ce.callee_name, ce.edge_type, ce.confidence, ce.resolved_by
                     FROM call_edges ce
                     JOIN code_nodes caller ON caller.id = ce.caller_id
                LEFT JOIN code_nodes callee ON callee.id = ce.callee_id
                    WHERE ce.scan_id = ?
                 ORDER BY caller, ce.callee_name""",
                (scan_id,),
            )
        ]

        domains = [
            {
                "name": r["name"],
                "node_count": r["node_count"],
                "entry_points": _json_list(r["entry_points"]),
                "tech_stack": _json_list(r["tech_stack"]),
            }
            for r in conn.execute(
                "SELECT name, node_count, entry_points, tech_stack "
                "FROM domains WHERE scan_id = ? ORDER BY name",
                (scan_id,),
            )
        ]

        relationships = [
            dict(r)
            for r in conn.execute(
                "SELECT from_entity, to_entity, from_field, to_field, cardinality, "
                "source, confidence, inferred "
                "FROM db_relationship WHERE scan_id = ? ORDER BY from_entity, to_entity",
                (scan_id,),
            )
        ]

        docs = [
            dict(r)
            for r in conn.execute(
                "SELECT domain, doc_type, doc_id, title, confidence, "
                "unverified_claims, verified_row_count "
                "FROM generated_docs WHERE scan_id = ? ORDER BY domain, doc_type",
                (scan_id,),
            )
        ]

        screens = [
            dict(r)
            for r in conn.execute(
                "SELECT screen_id, confidence FROM screen_specs "
                "WHERE scan_id = ? ORDER BY screen_id",
                (scan_id,),
            )
        ]
    finally:
        conn.close()

    fsms = load_entity_state_machines(db_path, scan_id)

    return {
        "schema_version": SCHEMA_VERSION,
        "project": slug,
        "generated_at": now_iso(),
        "scan": {
            "id": scan["id"],
            "commit_sha": scan["commit_sha"],
            "branch": scan["branch"],
            "started_at": scan["started_at"],
            "finished_at": scan["finished_at"],
            "status": scan["status"],
        },
        "nodes": nodes,
        "edges": edges,
        "domains": domains,
        "relationships": relationships,
        "fsms": fsms,
        "docs": docs,
        "screens": screens,
    }


def write_graph_export(
    db_path: Path, slug: str, out_path: Path, scan_id: int | None = None
) -> Path:
    """Write the canonical graph JSON to *out_path* and return it."""
    graph = build_graph_export(db_path, slug, scan_id)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, indent=1, default=str), encoding="utf-8")
    logger.info(
        "Exported knowledge graph: %d nodes, %d edges -> %s",
        len(graph["nodes"]), len(graph["edges"]), out_path,
    )
    return out_path
