"""Stage ``flows``: phases 12 ``tier2_flow_analysis`` + 13 ``scenario_flow_inference`` (+ tier-2 unload), moved verbatim from ``pipeline.py`` (M1.7)."""

from __future__ import annotations

from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..pipeline import (
    _budget_exhausted_fn, _budget_ok, _finalise_scan, _llm_skipped, _load_flows_from_db, _phase_should_run, _timed, _with_checkpoint, console, logger,
)
from .context import RunContext


def run(ctx: RunContext) -> None:
    # inputs from earlier stages, under their old pipeline-local names
    config = ctx.config
    llm_client = ctx.llm_client
    db_path = ctx.db_path
    scan_id = ctx.scan_id
    start_phase = ctx.start_phase
    skip_phases = ctx.skip_phases
    domains = ctx.domains
    scenarios = ctx.scenarios
    summaries_dict = ctx.summaries_dict
    flows_by_domain = ctx.flows_by_domain
    scenario_flows = ctx.scenario_flows
    # ------------------------------------------------------------------
    # 12. Tier 2: Flow analysis
    # ------------------------------------------------------------------
    from ..ai.flow_analyzer import analyze_all_domains, persist_flows

    if _llm_skipped(config, "Phase 12 (tier2_flow_analysis)") or not _phase_should_run(12, start_phase, skip_phases):
        console.print("[dim]Phase 12 (tier2_flow_analysis): loading from DB...[/]")
        flows_by_domain = _load_flows_from_db(db_path, scan_id)
        console.print(f"  Loaded [green]{sum(len(v) for v in flows_by_domain.values())}[/] flows from DB")
    elif not _budget_ok(llm_client, config, "Tier 2 flow analysis"):
        _finalise_scan(scan_id, db_path, "budget_exceeded", llm_client)
        return ctx.halt("budget_exceeded")
    else:
        with _with_checkpoint(db_path, scan_id, 12, "tier2_flow_analysis"):
            if config.provider == "ollama":
                tier2_model = config.get_model("tier2")
                console.print(f"[dim]Warming tier2 ({tier2_model})...[/]")
                llm_client.warm("tier2", "1h")

            console.print("[bold cyan]Tier 2: Analyzing business flows...[/]")
            with _timed("Tier 2 flow analysis"), Progress(
                SpinnerColumn(),
                TextColumn("[bold]Tier 2 flows"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                ptask = progress.add_task("Analyzing", total=len(domains))

                def _on_flow_progress(done: int, total: int):
                    progress.update(ptask, completed=done, total=total)

                flows_by_domain = analyze_all_domains(
                    domains, summaries_dict, llm_client, on_progress=_on_flow_progress,
                    db_path=db_path, scan_id=scan_id,
                    budget_exhausted=_budget_exhausted_fn(llm_client, config),
                )

            total_flows = sum(len(v) for v in flows_by_domain.values())
            # Determine model used for Tier 2
            tier2_model = config.get_model("tier2")
            persist_flows(flows_by_domain, scan_id, db_path, model_used=tier2_model)
            console.print(f"  Flows: [green]{total_flows}[/] across {len(flows_by_domain)} domains")

    # 13: Scenario Flow Inference
    from ..ai.flow_analyzer import ScenarioFlowInference
    if getattr(config, "no_llm", False):
        _llm_skipped(config, "Phase 13 (scenario_flow_inference)")
        scenario_flows = []
    elif _phase_should_run(13, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 13, "scenario_flow_inference"):
            console.print("[bold cyan]Phase 13: Reconstructing scenario flows...[/]")
            scenario_flows = []
            flow_inference = ScenarioFlowInference(llm_client)

            with _timed("scenario inference"), Progress(
                SpinnerColumn(),
                TextColumn("[bold]Scenario flow inference"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Inferring", total=len(scenarios))
                for scenario in scenarios:
                    try:
                        flow = flow_inference.infer_flow(scenario, summaries_dict)
                        # CRIT-2: prod scans verify each flow's narrative against
                        # RAG source so PF confidence is earned, not capped. Gated
                        # on --prod because it adds an LLM review pass per scenario.
                        if config.prod and _budget_ok(llm_client, config, "PF verification"):
                            flow_inference.verify_flow(flow, db_path)
                        scenario_flows.append(flow)
                    except Exception as e:
                        logger.warning(f"Failed to infer flow for {scenario.scenario_id}: {e}")
                    progress.advance(task)
            verified_n = sum(1 for f in scenario_flows if getattr(f, "verified", False))
            console.print(
                f"  Scenario flows: [green]{len(scenario_flows)}[/] reconstructed"
                + (f" ([green]{verified_n}[/] source-verified)" if verified_n else "")
            )
            # M0.6: persist now (artifacts added in phase 15) so a resume past
            # phase 13 rehydrates from the DB instead of re-calling the LLM.
            from ..ai.flow_analyzer import persist_scenario_flows as _persist_flows
            _persist_flows(scenario_flows, {}, scan_id, db_path)
    else:
        from ..ai.flow_analyzer import load_scenario_flows
        scenario_flows, _ = load_scenario_flows(scan_id, db_path)
        console.print(
            f"[dim]Phase 13 (scenario_flow_inference): loaded {len(scenario_flows)} flows from DB[/]"
        )
        if not scenario_flows and scenarios:
            console.print(
                "[yellow]No persisted scenario flows for this scan (run predates flow "
                "persistence or phase 13 was skipped). Re-run with --resume-from 13 to "
                "rebuild them.[/]"
            )

    # Free tier2 (~17 GB) before loading tier3 (~20 GB).
    tier2_model = config.get_model("tier2")
    if config.provider == "ollama" and not getattr(config, "no_llm", False):
        console.print(f"[dim]Unloading tier2 ({tier2_model})...[/]")
        llm_client.unload("tier2")
    ctx.flows_by_domain = flows_by_domain
    ctx.scenario_flows = scenario_flows


execute = run
