"""Stage ``render``: phase 18 ``render_markdown``, moved verbatim from ``pipeline.py`` (M1.7)."""

from __future__ import annotations

from ..pipeline import (
    _compute_domain_adjacency, _timed, _with_checkpoint, console, get_conn,
)
from .context import RunContext


def run(ctx: RunContext) -> None:
    # inputs from earlier stages, under their old pipeline-local names
    db_path = ctx.db_path
    scan_id = ctx.scan_id
    resolved = ctx.resolved
    docs_dir = ctx.docs_dir
    project_slug = ctx.project_slug
    scenario_flows = ctx.scenario_flows
    rollups = ctx.rollups
    scenario_artifacts = ctx.scenario_artifacts
    mining_results = ctx.mining_results
    # ------------------------------------------------------------------
    # 18. Render markdown files (after self-review so annotations are included)
    # ------------------------------------------------------------------
    with _with_checkpoint(db_path, scan_id, 18, "render_markdown"):
        from ..generators.doc_generator import write_docs

        # Compute cross-domain call graph for Layer 2 graph links
        with _timed("domain adjacency"), console.status("[bold cyan]Computing cross-domain links..."):
            domain_adjacency = _compute_domain_adjacency(db_path, scan_id)
        if domain_adjacency:
            console.print(
                f"  Cross-domain edges: [green]{sum(len(v) for v in domain_adjacency.values())}[/] "
                f"pairs across {len(domain_adjacency)} domains"
            )

        from ..generators.doc_generator import write_scenario_docs

        with _timed("write markdown"), console.status("[bold cyan]Writing markdown files..."):
            # Track 5: thread scenario_flows into write_docs so ASD can link
            # to its PF scenarios; thread the rollup-domain set into
            # write_scenario_docs so PF only links to existing rollups.
            written = write_docs(
                rollups,
                docs_dir,
                project_slug,
                repo_url=resolved.url or str(resolved.repo_path),
                repo_commit=resolved.commit_sha,
                domain_adjacency=domain_adjacency,
                scenario_flows=scenario_flows,
            )
            scenario_written = write_scenario_docs(
                scenario_flows,
                scenario_artifacts,
                docs_dir,
                project_slug,
                repo_url=resolved.url or str(resolved.repo_path),
                repo_commit=resolved.commit_sha,
                mining_results=mining_results,
                rollup_domains={r.domain for r in rollups},
            )
            written.extend(scenario_written)

            # Dependency & interface catalog (gap-assessment #7): inventory
            # external systems + FK relationships + API surface in one doc.
            from ..generators.dependency_catalog import write_dependency_catalog
            dep_path = write_dependency_catalog(db_path, scan_id, project_slug, docs_dir)
            if dep_path is not None:
                console.print(f"  Dependency catalog: [green]{dep_path.name}[/]")
        console.print(f"  Written: [green]{len(written)}[/] markdown files to {docs_dir}")

        # Keep DB.doc_id aligned with the on-disk filenames so the legacy delivery path (removed in M2.7; `discover push` sends pages)
        # can locate every file without re-deriving paths.
        conn = get_conn(db_path)
        try:
            for doc in written:
                conn.execute(
                    "UPDATE generated_docs SET doc_id = ? WHERE scan_id = ? AND domain = ? AND doc_type = ?",
                    (doc["doc_id"], scan_id, doc["domain"], doc["doc_type"]),
                )
            conn.commit()
        finally:
            conn.close()


execute = run
