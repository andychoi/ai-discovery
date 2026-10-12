"""Stage ``sql``: SQL entities (Phase 2e-1), FK relationships, SQL placeholder FSMs —
moved verbatim from the unnumbered backbone block (M1.6). No checkpoint (as before)."""

from __future__ import annotations

from .context import RunContext


def run(ctx: RunContext) -> None:
    from ..db import persist_relationships
    from ..extractors import extract_relationships, extract_sql_entities, read_sql_file_nodes
    from ..graph.fsm_rollup import build_fsms_from_sql_nodes
    from ..pipeline import _timed, console

    # Phase 2e-1: synthesize classless CodeNodes for tables/views referenced
    # in raw SQL string literals, plus placeholder FSMs for each. The Phase
    # 2d consolidator treats `sql_table` / `sql_view` as class-like, so a
    # synthetic FSM for `orders` (fields from SQL) merges with the classful
    # `Order` FSM (transitions from Python) when stems + fields overlap.
    # Standalone .sql files (incl. migrations/) are read here too — they are
    # the canonical schema + FK source but aren't tree-sitter-parsed.
    with _timed("sql extract"), console.status("[bold cyan]Extracting SQL entities..."):
        sql_file_nodes = read_sql_file_nodes(ctx.resolved.repo_path)
        nodes_for_sql = ctx.all_nodes + sql_file_nodes
        sql_nodes = extract_sql_entities(nodes_for_sql)

    # FK-aware table docs (Phase 1): extract foreign-key edges from SQL DDL and
    # ORM associations (JPA @JoinColumn, EF nav props) and persist them.
    with _timed("relationship extract"), console.status("[bold cyan]Extracting entity relationships..."):
        relationships = extract_relationships(nodes_for_sql)
        if relationships:
            persist_relationships(ctx.db_path, ctx.scan_id, relationships)
            console.print(
                f"  Entity relationships: [green]{len(relationships)}[/] FK edges"
            )
    sql_fsms = build_fsms_from_sql_nodes(sql_nodes) if sql_nodes else []
    if sql_nodes:
        view_count = sum(1 for n in sql_nodes if n.node_type == "sql_view")
        console.print(
            f"  SQL entities: [green]{len(sql_nodes)}[/] synthesized "
            f"([dim]{view_count} views[/])"
        )
    ctx.sql_nodes, ctx.relationships, ctx.sql_fsms = sql_nodes, relationships, sql_fsms


execute = run
