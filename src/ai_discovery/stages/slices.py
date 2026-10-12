"""Stage ``slices``: execution slices (pipeline phase 8) + 8a entry-point linking,
moved verbatim (M1.6)."""

from __future__ import annotations

from .context import RunContext


def run(ctx: RunContext) -> None:
    from ..graph.call_graph import ExecutionSliceBuilder
    from ..pipeline import _timed, _with_checkpoint, console

    with _with_checkpoint(ctx.db_path, ctx.scan_id, 8, "execution_slices"):
        with _timed("execution slices"), console.status("[bold cyan]Building execution slices..."):
            slice_builder = ExecutionSliceBuilder(ctx.all_nodes, ctx.edges)
            ctx.scenarios = slice_builder.build_all_scenarios()
        console.print(f"  Execution slices: [green]{len(ctx.scenarios)}[/] scenarios identified")


def rehydrate(ctx: RunContext) -> None:
    from ..graph.call_graph import ExecutionSliceBuilder
    from ..pipeline import console

    console.print("[dim]Phase 8 (execution_slices): rebuilding (cheap)...[/]")
    slice_builder = ExecutionSliceBuilder(ctx.all_nodes, ctx.edges)
    ctx.scenarios = slice_builder.build_all_scenarios()


def link_entry_points(ctx: RunContext) -> None:
    """8a (inline): Link state transitions to entry points (UI/API/Batch/Event)."""
    from ..graph.entry_point_linker import link_entry_points as _link
    from ..pipeline import _timed, console

    all_transitions = [
        n.state_transition
        for s in ctx.scenarios
        for n in s.nodes
        if n.state_transition is not None
    ]
    ctx.all_transitions = all_transitions
    if all_transitions:
        with _timed("entry-point link"), console.status("[bold cyan]Linking state transitions to entry points..."):
            _link(all_transitions, ctx.all_nodes, ctx.edges)
        linked = sum(1 for t in all_transitions if t.entry_points)
        console.print(
            f"  Entry-point links: [green]{linked}/{len(all_transitions)}[/] transitions"
        )


def execute(ctx: RunContext) -> None:
    (run if ctx.phase_runs(8) else rehydrate)(ctx)
    link_entry_points(ctx)
