"""Stage ``graph``: call graph, external systems, domain edges, interfaces.json
(rest of pipeline phase 7), moved verbatim (M1.6).

``execute`` keeps phase 7 as one checkpoint: ``domains.run`` then ``graph.run``.
"""

from __future__ import annotations

import logging

import json

from . import domains as domains_stage
from .context import RunContext

# same logger name as before the move, so log output/filters are unchanged
logger = logging.getLogger("ai_discovery.pipeline")


def run(ctx: RunContext) -> None:
    from ..db import get_conn
    from ..graph.call_graph import resolve_calls
    from ..pipeline import _timed, console

    resolved, all_nodes, scan_id, db_path = ctx.resolved, ctx.all_nodes, ctx.scan_id, ctx.db_path
    domains_dict, domains = ctx.domains_dict, ctx.domains

    # LSP/SCIP tier: use an authoritative symbol index if the repo ships
    # one (symbol_index.json / *.scip.json). Absent → fully heuristic.
    from ..graph.symbol_index import load_symbol_index
    symbol_index = load_symbol_index(resolved.repo_path)
    with _timed("call graph"), console.status("[bold cyan]Building call graph..."):
        resolution = resolve_calls(all_nodes, symbol_index=symbol_index)
    edges, unresolved = resolution.edges, resolution.unresolved
    n_indexed = sum(1 for e in edges if e.metadata.get("resolved_by") == "scip")
    console.print(
        f"  Call graph: [green]{len(edges)}[/] edges, [yellow]{len(unresolved)}[/] unresolved calls"
        + (f" ([green]{n_indexed}[/] via symbol index)" if symbol_index is not None else "")
    )

    # HIGH-8: promote external-client calls (axios/kafka/redis/stripe/…)
    # to first-class typed external-system nodes + edges, so external
    # dependencies are queryable rather than dropped as unresolved strings.
    from ..extractors import extract_external_systems, read_infra_files
    ext_nodes, ext_edges = extract_external_systems(all_nodes)
    # HIGH-7: merge deployment-declared backing services (docker-compose
    # / K8s images: postgres/redis/kafka/…). Same node type + identity
    # (external::token), so a system seen in both code and infra is one node.
    ext_by_qn = {n.qualified_name: n for n in ext_nodes}
    for n in read_infra_files(resolved.repo_path):
        ext_by_qn.setdefault(n.qualified_name, n)
    ext_nodes = list(ext_by_qn.values())
    if ext_nodes:
        conn = get_conn(db_path)
        try:
            for en in ext_nodes:
                conn.execute(
                    "INSERT OR IGNORE INTO code_nodes "
                    "(scan_id, file_path, language, node_type, name, qualified_name, "
                    "line_start, line_end, source_code, annotations, params, "
                    "return_type, framework_hints, domain) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (scan_id, en.file_path, en.language, en.node_type, en.name,
                     en.qualified_name, en.line_start, en.line_end, en.source_code,
                     json.dumps(en.annotations), json.dumps(en.params),
                     en.return_type, json.dumps(en.framework_hints), None),
                )
            conn.commit()
        finally:
            conn.close()
        edges = edges + ext_edges
        kinds = sorted({n.framework_hints.get("kind", "?") for n in ext_nodes})
        console.print(
            f"  External systems: [green]{len(ext_nodes)}[/] "
            f"({', '.join(kinds)}); [green]{len(ext_edges)}[/] external-call edges"
        )

    # Distribute call edges to domains
    node_domain_map = {n.qualified_name: n.domain for n in all_nodes}
    for edge in edges:
        caller_domain = node_domain_map.get(edge.caller)
        callee_domain = node_domain_map.get(edge.callee)
        if caller_domain and caller_domain in domains_dict:
            if callee_domain == caller_domain:
                domains_dict[caller_domain].internal_edges.append(edge)
            else:
                domains_dict[caller_domain].external_edges.append(edge)

    # Persist domains and edges
    conn = get_conn(db_path)
    try:
        for domain in domains:
            conn.execute(
                "INSERT OR IGNORE INTO domains (scan_id, name, node_count, entry_points, tech_stack) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    scan_id,
                    domain.name,
                    len(domain.nodes),
                    json.dumps([ep.qualified_name for ep in domain.entry_points]),
                    json.dumps(domain.tech_stack),
                ),
            )
        # Build a qualified_name -> id map for edges
        rows = conn.execute(
            "SELECT id, qualified_name FROM code_nodes WHERE scan_id = ?",
            (scan_id,),
        ).fetchall()
        qn_to_id = {r["qualified_name"]: r["id"] for r in rows}
        edge_rows = [
            (
                scan_id,
                qn_to_id.get(edge.caller),
                qn_to_id.get(edge.callee),
                edge.callee,
                edge.edge_type,
                edge.confidence,
                (edge.metadata or {}).get("resolved_by", ""),
                (edge.metadata or {}).get("edge_kind", "calls"),
                (edge.metadata or {}).get("evidence_kind", "static"),
            )
            for edge in edges
            if qn_to_id.get(edge.caller) is not None
        ]
        # P1-a: idempotent re-run. call_edges has no natural overwrite,
        # so re-running phase 7 on the same scan_id (e.g. --resume-from=7)
        # would otherwise duplicate every edge. Clear this scan's edges
        # first (same pattern as db_relationship), and use INSERT OR IGNORE
        # against the UNIQUE(scan_id, caller_id, callee_name, edge_type)
        # constraint as defense-in-depth on databases that have it.
        conn.execute("DELETE FROM call_edges WHERE scan_id = ?", (scan_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO call_edges (scan_id, caller_id, callee_id, callee_name, edge_type, confidence, "
            "resolved_by, edge_kind, evidence_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            edge_rows,
        )
        # ADR-0004: ambiguous / unbindable calls are rows here, never edges.
        conn.execute("DELETE FROM unresolved_calls WHERE scan_id = ?", (scan_id,))
        conn.executemany(
            "INSERT INTO unresolved_calls (scan_id, caller_id, callee_name, reason, candidate_count, file, line) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(scan_id, qn_to_id.get(u.caller), u.callee_name, u.reason, u.candidate_count, u.file, u.line)
             for u in unresolved],
        )
        conn.commit()
    finally:
        conn.close()
    ctx.edges = edges

    # HIGH-8: write this repo's interface surface (inbound endpoints +
    # outbound HTTP targets) so `discover federate` can correlate
    # provider→consumer integration edges across repos.
    try:
        from ..graph.integration_correlator import (
            build_repo_interfaces, interfaces_to_dict,
        )
        ri = build_repo_interfaces(ctx.project_slug, all_nodes, edges)
        (ctx.output_dir / "interfaces.json").write_text(
            json.dumps(interfaces_to_dict(ri), indent=2), encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to write interfaces.json: %s", exc)
        logger.debug("interfaces.json traceback", exc_info=True)
        ctx.degrade("graph", "interfaces_write_failed", 1, [type(exc).__name__])


def rehydrate(ctx: RunContext) -> None:
    from ..graph.call_graph import build_call_graph
    from ..graph.symbol_index import load_symbol_index

    ctx.edges = build_call_graph(ctx.all_nodes, symbol_index=load_symbol_index(ctx.resolved.repo_path))


def execute(ctx: RunContext) -> None:
    """Phase 7 (``domain_classify``): one checkpoint, domains first, then the graph."""
    from ..pipeline import _with_checkpoint, console

    if ctx.phase_runs(7):
        with _with_checkpoint(ctx.db_path, ctx.scan_id, 7, "domain_classify"):
            domains_stage.run(ctx)
            run(ctx)
    else:
        console.print("[dim]Phase 7 (domain_classify): loading from DB...[/]")
        domains_stage.rehydrate(ctx)
        rehydrate(ctx)
        console.print(f"  Loaded [green]{len(ctx.domains)}[/] domains, [green]{len(ctx.edges)}[/] edges from DB")
