"""Stage ``finalise``: phase 19 ``finalise`` (status, summary print), moved verbatim from ``pipeline.py`` (M1.7)."""

from __future__ import annotations

import time

from ..pipeline import (
    _budget_exhausted_fn, _display_checkpoint_status, _finalise_scan, _next_phase, _with_checkpoint, console, get_last_complete_phase,
)
from .context import RunContext


def run(ctx: RunContext) -> None:
    # inputs from earlier stages, under their old pipeline-local names
    config = ctx.config
    llm_client = ctx.llm_client
    db_path = ctx.db_path
    scan_id = ctx.scan_id
    docs_dir = ctx.docs_dir
    project_slug = ctx.project_slug
    all_nodes = ctx.all_nodes
    domains = ctx.domains
    rollups = ctx.rollups
    # ------------------------------------------------------------------
    # 19. Finalise (offline-only — no push)
    # ------------------------------------------------------------------
    # M0.5: a budget hit mid-phase lets later phases run on partial LLM output;
    # record it so the scan is resumable and the CLI exits non-zero.
    _exhausted = _budget_exhausted_fn(llm_client, config)
    final_status = "budget_exceeded" if (_exhausted is not None and _exhausted()) else "completed"
    with _with_checkpoint(db_path, scan_id, 19, "finalise"):
        _finalise_scan(scan_id, db_path, final_status, llm_client,
                       keep_snapshots=config.store.keep_snapshots)

        # Print summary
        cost = llm_client.total_cost_usd()
        total_elapsed = time.perf_counter() - ctx.pipeline_start
        console.print()
        console.rule("[bold green]Pipeline complete")
        console.print(f"  Scan ID:     [bold]{scan_id}[/]")
        console.print(f"  Code nodes:  {len(all_nodes)}")
        console.print(f"  Domains:     {len(domains)}")
        console.print(f"  Documents:   {len(rollups)}")
        console.print(f"  LLM cost:    [bold]${cost:.4f}[/]")
        console.print(f"  Total time:  [bold]{total_elapsed:.1f}s[/]")
        console.print(f"  Output:      {docs_dir}")

        # Display final checkpoint status
        console.print()
        console.print("[bold cyan]Phase completion summary:[/]")
        _display_checkpoint_status(db_path, scan_id)

        console.print()
        console.print(f"  [dim]Ingest:      discover ingest -p {project_slug} --target dochub[/]")
        console.print()

        # Instructions for resume
        last_complete = get_last_complete_phase(db_path, scan_id)
        if last_complete is not None:
            next_phase = _next_phase(last_complete)
            if next_phase is not None:
                console.print(f"  [dim]To resume from a specific phase: discover scan repo --resume --resume-from={int(next_phase)}[/]")
        console.print()

    if final_status == "budget_exceeded":
        console.print(
            f"[red bold]Budget limit reached during the scan[/] "
            f"(${llm_client.total_cost_usd():.2f} >= ${config.budget_limit_usd:.2f}); "
            "LLM output is partial. Raise --budget and re-run with --resume."
        )
    ctx.final_status = final_status
    ctx.final_status = final_status


execute = run
