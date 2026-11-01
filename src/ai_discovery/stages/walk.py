"""Stage ``walk``: the scan's file set and language detection (pipeline phase 5; walker v2, M6.1)."""

from __future__ import annotations

from .context import RunContext


def _walker(ctx: RunContext):
    from ..repo.walker import RepoWalker, WalkOptions

    if ctx.walker is None:
        ctx.walker = RepoWalker(ctx.resolved.repo_path, WalkOptions.from_config(ctx.config))
    return ctx.walker


def run(ctx: RunContext) -> None:
    from ..pipeline import _timed, _with_checkpoint, console
    from ..repo.walker import detect_languages

    with _with_checkpoint(ctx.db_path, ctx.scan_id, 5, "lang_detect"):
        with _timed("lang detect"), console.status("[bold cyan]Detecting languages..."):
            ctx.lang_stats = detect_languages(ctx.resolved.repo_path, walker=_walker(ctx))
        lang_names = ", ".join(sorted(ctx.lang_stats.keys())) or "(none)"
        console.print(f"  Languages detected: [green]{lang_names}[/]")


def rehydrate(ctx: RunContext) -> None:
    from ..pipeline import _timed, console
    from ..repo.walker import detect_languages

    console.print("[dim]Phase 5 (lang_detect): skipped (already complete)[/]")
    with _timed("lang detect (cached)"), console.status("[bold cyan]Detecting languages (for resume)..."):
        ctx.lang_stats = detect_languages(ctx.resolved.repo_path, walker=_walker(ctx))


def execute(ctx: RunContext) -> None:
    (run if ctx.phase_runs(5) else rehydrate)(ctx)
