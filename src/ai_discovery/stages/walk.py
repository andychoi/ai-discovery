"""Stage ``walk``: language detection (pipeline phase 5), moved verbatim (M1.6)."""

from __future__ import annotations

from .context import RunContext


def run(ctx: RunContext) -> None:
    from ..pipeline import _timed, _with_checkpoint, console
    from ..repo.lang_detector import detect_languages

    with _with_checkpoint(ctx.db_path, ctx.scan_id, 5, "lang_detect"):
        with _timed("lang detect"), console.status("[bold cyan]Detecting languages..."):
            ctx.lang_stats = detect_languages(ctx.resolved.repo_path)
        lang_names = ", ".join(sorted(ctx.lang_stats.keys())) or "(none)"
        console.print(f"  Languages detected: [green]{lang_names}[/]")


def rehydrate(ctx: RunContext) -> None:
    from ..pipeline import _timed, console
    from ..repo.lang_detector import detect_languages

    console.print("[dim]Phase 5 (lang_detect): skipped (already complete)[/]")
    with _timed("lang detect (cached)"), console.status("[bold cyan]Detecting languages (for resume)..."):
        ctx.lang_stats = detect_languages(ctx.resolved.repo_path)


def execute(ctx: RunContext) -> None:
    (run if ctx.phase_runs(5) else rehydrate)(ctx)
