"""Stage ``embed``: phases 9 ``chunk`` + 10 ``rag_embed`` (and the post-chunk source release), moved verbatim from ``pipeline.py`` (M1.7)."""

from __future__ import annotations

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from ..pipeline import (
    _build_rag_chunks, _count_tier1_targets, _llm_skipped, _phase_should_run, _release_node_source, _timed, _with_checkpoint, console, logger,
)
from .context import RunContext


def run(ctx: RunContext) -> None:
    # inputs from earlier stages, under their old pipeline-local names
    config = ctx.config
    llm_client = ctx.llm_client
    db_path = ctx.db_path
    scan_id = ctx.scan_id
    resolved = ctx.resolved
    start_phase = ctx.start_phase
    skip_phases = ctx.skip_phases
    all_nodes = ctx.all_nodes
    chunks = ctx.chunks
    rag_chunks = ctx.rag_chunks
    # ------------------------------------------------------------------
    # 9. Smart chunk
    # ------------------------------------------------------------------
    from ..ai.chunker import chunk_code_nodes, chunk_for_rag

    if _phase_should_run(9, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 9, "chunk"):
            with _timed("chunk"), console.status("[bold cyan]Chunking code nodes..."):
                chunks = chunk_code_nodes(all_nodes)
                rag_chunks = _build_rag_chunks(chunks, config, resolved.repo_path)
            tier1_count = _count_tier1_targets(chunks, resolved.repo_path)
            console.print(
                f"  Chunks: [green]{len(chunks)}[/] parsed "
                f"([green]{tier1_count}[/] for Tier 1), "
                f"[green]{len(rag_chunks)}[/] RAG chunks"
            )
    else:
        console.print("[dim]Phase 9 (chunk): rebuilding (cheap)...[/]")
        chunks = chunk_code_nodes(all_nodes)
        rag_chunks = _build_rag_chunks(chunks, config, resolved.repo_path)

    # P0-3 (memory): chunking is the last in-memory consumer of node.source_code.
    # Release it now (it stays in code_nodes) so phases 10–19 don't carry the
    # full corpus source alongside embeddings and LLM buffers.
    freed_mb = _release_node_source(all_nodes) // (1024 * 1024)
    if freed_mb >= 1:
        console.print(f"  [dim]Released ~{freed_mb} MB of in-memory source after chunking[/]")

    # ------------------------------------------------------------------
    # 10. Embed for RAG
    # ------------------------------------------------------------------
    from ..rag.embedder import embed_chunks

    # llm_client is already constructed up-front (before Phase 2); see CRIT-1 note.

    if not config.rag.enabled:
        console.print("[dim]Phase 10 (rag_embed): disabled via config.rag.enabled=false[/]")
    elif _llm_skipped(config, "Phase 10 (rag_embed)"):
        pass
    elif _phase_should_run(10, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 10, "rag_embed"):
            # Pre-warm small models that stay resident for the whole run.
            # (No-op on Bedrock; on Ollama this pins them with keep_alive=2h so they
            # survive the 5-minute default unload timer across phase transitions.)
            if config.provider in ("ollama", "mlx-gemma", "mlx-qwen"):
                from ai_discovery.shared.model_defaults import MODELS
                emb_model = MODELS.get(config.provider, {}).get("embedding", config.rag.ollama_model)
                console.print(f"[dim]Warming embedding model ({emb_model})...[/]")
                llm_client.warm_embedding("2h")
                tier1_model = config.get_model("tier1")
                console.print(f"[dim]Warming tier1 ({tier1_model})...[/]")
                llm_client.warm("tier1", "2h")

            with _timed("RAG embed"):
                try:
                    workers = max(1, int(config.max_concurrent or 1))
                    with Progress(
                        TextColumn("[bold cyan]Embedding RAG chunks"),
                        BarColumn(),
                        MofNCompleteColumn(),
                        TextColumn("•"),
                        TimeElapsedColumn(),
                        TextColumn("•"),
                        TimeRemainingColumn(),
                        console=console,
                        transient=True,
                    ) as progress:
                        task = progress.add_task("embed", total=len(rag_chunks))

                        def _on_progress(done: int, total: int) -> None:
                            progress.update(task, completed=done)

                        embed_result = embed_chunks(
                            rag_chunks, db_path, llm_client,
                            max_workers=workers,
                            progress_callback=_on_progress,
                        )
                    if embed_result.get("resumed"):
                        console.print(
                            f"  RAG embeddings: [green]{embed_result['embedded']}[/] vectors "
                            f"[dim](resumed — skipped re-embedding)[/]"
                        )
                    else:
                        console.print(
                            f"  RAG embeddings: [green]{embed_result['embedded']}[/] vectors "
                            f"(dim={embed_result['dim']}, workers={workers})"
                        )
                except Exception as exc:
                    logger.warning("RAG embedding failed (continuing without RAG): %s", exc)
                    console.print(f"  [yellow]RAG embedding skipped:[/] {exc}")
    else:
        console.print("[dim]Phase 10 (rag_embed): skipped (already complete)[/]")
    ctx.chunks = chunks
    ctx.rag_chunks = rag_chunks


execute = run
