"""Stage ``rollup``: phase 14 ``tier3_doc_rollup`` (+ prose validation), moved verbatim from ``pipeline.py`` (M1.7)."""

from __future__ import annotations

import logging

from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..pipeline import (
    _budget_exhausted_fn, _budget_ok, _finalise_scan, _llm_skipped, _timed, _with_checkpoint, console,
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
    project_slug = ctx.project_slug
    all_nodes = ctx.all_nodes
    domains = ctx.domains
    summaries_dict = ctx.summaries_dict
    flows_by_domain = ctx.flows_by_domain
    rollups = ctx.rollups
    # ------------------------------------------------------------------
    # 14. Tier 3: Doc rollup
    # ------------------------------------------------------------------
    from ..ai.rollup import generate_all_docs, persist_rollups, DOC_TYPES

    if getattr(config, "no_llm", False):
        _llm_skipped(config, "Phase 14 (tier3_doc_rollup)")
        rollups = []
    elif not _budget_ok(llm_client, config, "Tier 3 doc rollup"):
        _finalise_scan(scan_id, db_path, "budget_exceeded", llm_client)
        return ctx.halt("budget_exceeded")
    else:
        with _with_checkpoint(db_path, scan_id, 14, "tier3_doc_rollup"):
            if config.provider == "ollama":
                tier3_model = config.get_model("deep")
                console.print(f"[dim]Warming tier3 ({tier3_model})...[/]")
                llm_client.warm("tier3", "1h")

            console.print("[bold cyan]Tier 3: Generating SDLC documents...[/]")
            with _timed("Tier 3 doc rollup"), Progress(
                SpinnerColumn(),
                TextColumn("[bold]Tier 3 docs"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                total_docs = len(domains) * len(DOC_TYPES)
                ptask = progress.add_task("Generating", total=total_docs)

                def _on_rollup_progress(done: int, total: int):
                    progress.update(ptask, completed=done, total=total)

                rollups = generate_all_docs(
                    domains, summaries_dict, flows_by_domain, llm_client,
                    on_progress=_on_rollup_progress,
                    max_workers=config.max_concurrent,
                    db_path=db_path,
                    scan_id=scan_id,
                    budget_exhausted=_budget_exhausted_fn(llm_client, config),
                )

            # P0-4: deterministic structural check (no LLM). Flag file:line citations
            # and qualified symbols in the generated prose that are absent from the
            # parsed graph — likely fabrications — and annotate the doc so readers see
            # the warning before relying on it.
            try:
                from ..ai.prose_validator import build_known_graph, validate_prose, annotate
                known_graph = build_known_graph(all_nodes)
                flagged_total = 0
                for r in rollups:
                    v = validate_prose(r.content_md, known_graph)
                    if not v.is_clean:
                        r.content_md = annotate(r.content_md, v)
                        flagged_total += v.count
                if flagged_total:
                    console.print(
                        f"  [yellow]Unverified code references flagged:[/] {flagged_total} "
                        "(annotated in docs)"
                    )
            except Exception as exc:  # annotation is non-critical: warn, don't lose docs
                console.print(f"  [yellow]Prose validation skipped:[/] {exc}")
                logger.debug("prose validation traceback", exc_info=True)
                ctx.degrade("rollup", "prose_validation_skipped", 1, [type(exc).__name__])

            persist_rollups(rollups, scan_id, db_path, project_slug)
            console.print(f"  Documents: [green]{len(rollups)}[/] generated")
    ctx.rollups = rollups


execute = run
