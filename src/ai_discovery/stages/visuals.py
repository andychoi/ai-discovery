"""Stage ``visuals``: phases 15 ``visual_artifacts`` (+ onboarding) and 16 flow statistics, tier-3 unload, ``llm_complete`` mark, moved verbatim from ``pipeline.py`` (M1.7)."""

from __future__ import annotations

import logging

from ..pipeline import (
    _budget_ok, _finalise_scan, _llm_skipped, _mine_processes, _timed, _with_checkpoint, console, get_conn, now_iso,
)
from .context import RunContext

# same logger name as before the move, so log output/filters are unchanged
logger = logging.getLogger("ai_discovery.pipeline")


def run(ctx: RunContext) -> None:
    # inputs from earlier stages, under their old pipeline-local names
    config = ctx.config
    llm_client = ctx.llm_client
    db_path = ctx.db_path
    scan_id = ctx.scan_id
    docs_dir = ctx.docs_dir
    output_dir = ctx.output_dir
    project_slug = ctx.project_slug
    domains = ctx.domains
    edges = ctx.edges
    summaries_dict = ctx.summaries_dict
    scenario_flows = ctx.scenario_flows
    scenario_artifacts = ctx.scenario_artifacts
    mining_results = ctx.mining_results
    # 15: Generate Visual Artifacts (BPMN + Mermaid sequence/flowchart) and persist
    from ..generators.bpmn_generator import BPMNGenerator
    from ..ai.flow_analyzer import persist_scenario_flows

    with _with_checkpoint(db_path, scan_id, 15, "visual_artifacts"):
        bpmn_gen = BPMNGenerator()
        console.print("[bold cyan]Phase 15: Generating visual artifacts...[/]")

        scenario_artifacts: dict[str, dict] = {}
        for flow in scenario_flows:
            scenario_artifacts[flow.scenario_id] = {
                "mermaid": bpmn_gen.generate_mermaid_sequence(flow),
                "mermaid_flowchart": bpmn_gen.generate_mermaid_flowchart(flow),
                "bpmn": bpmn_gen.generate_bpmn_xml(flow),
                "ipo": bpmn_gen.generate_ipo_markdown(flow),
            }
        persist_scenario_flows(scenario_flows, scenario_artifacts, scan_id, db_path)
        console.print(f"  Visual artifacts: [green]{len(scenario_artifacts)}[/] scenarios persisted")

        # A-7: tour/onboarding guides — deterministic call-graph learning path
        # per domain + ONE Tier-2 narrative call each. Budget-gated; a domain
        # too small to tour costs nothing.
        if not _llm_skipped(config, "Onboarding guides") and _budget_ok(llm_client, config, "Onboarding guides"):
            from ..generators.onboarding_generator import generate_onboarding_docs

            onboard_docs = generate_onboarding_docs(
                domains, edges, summaries_dict, llm_client, docs_dir, project_slug
            )
            if onboard_docs:
                conn = get_conn(db_path)
                try:
                    for d in onboard_docs:
                        conn.execute(
                            "INSERT OR REPLACE INTO generated_docs "
                            "(scan_id, domain, doc_type, doc_id, title, content_md, "
                            "confidence, verified_row_count, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                scan_id, d["domain"], d["doc_type"], d["doc_id"],
                                d["title"], d["content_md"], d["confidence"],
                                d["verified_row_count"], now_iso(),
                            ),
                        )
                    conn.commit()
                finally:
                    conn.close()
                console.print(f"  Onboarding guides: [green]{len(onboard_docs)}[/] domains")

    # ------------------------------------------------------------------
    # 16 OPTIONAL: Flow statistics (per-scenario edge frequencies)
    # ------------------------------------------------------------------
    mining_results: dict = {}
    mining_cfg = getattr(config, 'process_mining', None)
    mining_on = bool(mining_cfg and getattr(mining_cfg, 'enabled', False))
    if mining_on:
        with _with_checkpoint(db_path, scan_id, 16, "process_mining"):
            console.print("[bold cyan]Phase 16: Flow statistics...[/]")
            with _timed("flow statistics"), console.status("[bold cyan]Computing flow statistics..."):
                try:
                    mining_results = _mine_processes(scenario_flows, output_dir)
                    console.print(
                        f"  Flow statistics: [green]{len(mining_results)}[/] scenarios analysed"
                    )
                except Exception as e:
                    logger.error(f"Flow statistics failed (continuing): {e}")
                    logger.debug("flow statistics traceback", exc_info=True)
                    console.print(f"  [yellow]Flow statistics skipped:[/] {e}")
                    ctx.degrade("visuals", "flow_statistics_failed", 1, [type(e).__name__])
    else:
        # No checkpoint written — disabled runs shouldn't leave a "16 complete"
        # marker that misleads resume logic.
        console.print("[dim]Phase 16: Flow statistics disabled (set process_mining.enabled=true to enable)[/]")

    # Free tier3 (~20 GB) so self-review (tier1) has headroom.
    if config.provider == "ollama" and not getattr(config, "no_llm", False):
        console.print(f"[dim]Unloading tier3 ({config.get_model('deep')})...[/]")
        llm_client.unload("tier3")

    # Checkpoint: LLM tiers done — resume can skip here on rerun
    _finalise_scan(scan_id, db_path, "llm_complete")
    ctx.scenario_artifacts = scenario_artifacts
    ctx.mining_results = mining_results


execute = run
