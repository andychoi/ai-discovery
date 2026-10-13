"""Stage ``summaries``: phase 11 ``tier1_summarize``, moved verbatim from ``pipeline.py`` (M1.7)."""

from __future__ import annotations

from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..pipeline import (
    _budget_exhausted_fn, _budget_ok, _count_tier1_targets, _finalise_scan, _llm_skipped, _load_summaries_from_db, _record_degradation, _timed, _with_checkpoint, console,
)
from .context import RunContext


def run(ctx: RunContext) -> None:
    # inputs from earlier stages, under their old pipeline-local names
    config = ctx.config
    llm_client = ctx.llm_client
    db_path = ctx.db_path
    scan_id = ctx.scan_id
    resolved = ctx.resolved
    edges = ctx.edges
    chunks = ctx.chunks
    summaries_dict = ctx.summaries_dict
    # ------------------------------------------------------------------
    # 11. Tier 1: Summarize
    # ------------------------------------------------------------------
    from ..ai.summarizer import summarize_chunks, persist_summaries, reuse_prior_summaries

    if _llm_skipped(config, "Phase 11 (tier1_summarize)") or not ctx.phase_runs(11):
        console.print("[dim]Phase 11 (tier1_summarize): loading from DB...[/]")
        summaries_dict = _load_summaries_from_db(db_path, scan_id)
        console.print(f"  Loaded [green]{len(summaries_dict)}[/] summaries from DB")
    elif not _budget_ok(llm_client, config, "Tier 1 summarization"):
        _finalise_scan(scan_id, db_path, "budget_exceeded", llm_client)
        return ctx.halt("budget_exceeded")
    else:
        with _with_checkpoint(db_path, scan_id, 11, "tier1_summarize"):
            console.print("[bold cyan]Tier 1: Summarizing code chunks...[/]")
            # A-2 incremental re-scan: copy summaries from the latest prior
            # scan for unchanged files; the resume guard below then skips them.
            n_reused = reuse_prior_summaries(db_path, scan_id, model=config.get_model("fast"))
            if n_reused:
                console.print(
                    f"  Reused [green]{n_reused}[/] summaries from prior scan (unchanged files)"
                )
            with _timed("Tier 1 summarize"), Progress(
                SpinnerColumn(),
                TextColumn("[bold]Tier 1 summaries"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                ptask = progress.add_task("Summarizing", total=len(chunks))

                def _on_summary_progress(done: int, total: int):
                    progress.update(ptask, completed=done, total=total)

                summaries = summarize_chunks(
                    chunks, llm_client, db_path, config.max_concurrent,
                    on_progress=_on_summary_progress,
                    scan_id=scan_id,
                    skip_rag=True,
                    skip_tests=True,
                    budget_exhausted=_budget_exhausted_fn(llm_client, config),
                    # A-1 Louvain semantic batching: Phase-7 call edges group
                    # chunks into community batches (one structured call each).
                    call_edges=edges,
                    semantic_batching=config.semantic_batching,
                    repo_root=resolved.repo_path,
                )

            n_failed = persist_summaries(summaries, scan_id, db_path)
            if n_failed:
                console.print(f"  [yellow]{n_failed} summaries failed[/] (not stored; the next "
                              "scan or a resume of this phase re-summarizes only those)")
                _record_degradation(db_path, scan_id, "tier1_summarize", "tier1_failed", n_failed,
                                    sorted(s["qualified_name"] for s in summaries
                                           if s.get("failed"))[:5])
            if chunks and not _count_tier1_targets(chunks, resolved.repo_path):
                # Never "complete" Tier 1 silently with nothing summarized (audit #90).
                console.print("  [yellow]Tier 1 had no chunks to summarize[/] (all filtered as tests "
                              "or structural types)")
                _record_degradation(db_path, scan_id, "tier1_summarize", "tier1_no_targets", len(chunks))
            summaries_dict = {s["qualified_name"]: s for s in summaries}
            console.print(f"  Summaries: [green]{len(summaries)}[/] (cost so far: ${llm_client.total_cost_usd():.4f})")
    ctx.summaries_dict = summaries_dict


execute = run
