"""Stage ``screens``: phase 2 ``screen_llm_specs`` (menu → screens → mapping → structural or LLM specs), moved verbatim from ``pipeline.py`` (M1.7)."""

from __future__ import annotations

from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..pipeline import (
    _budget_ok, _phase_should_run, _timed, _with_checkpoint, console,
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
    docs_dir = ctx.docs_dir
    project_slug = ctx.project_slug
    # ------------------------------------------------------------------
    # 2. Screen-centric LLM spec generation (PARALLEL to domain analysis)
    # ------------------------------------------------------------------
    from ..menu_detector import detect_and_build_screens
    from ..screen_mapper import map_all_screens
    from ..ai.screen_spec_generator import (
        generate_all_screen_specs, persist_screen_specs,
    )
    from ..generators.screen_doc_writer import write_all_screen_specs

    if _phase_should_run(2, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 2, "screen_llm_specs"):
            console.print("[bold cyan]Phase 2: Generating screen specs...[/]")

            # Detect screens from menu
            with _timed("screen detect"), console.status("[bold cyan]Detecting screens..."):
                menu_items, screens = detect_and_build_screens(resolved.repo_path)

            if not screens:
                from ..menu_detector import SUPPORTED_MENU_FORMATS
                console.print(
                    "[yellow]No menu system detected — menu format unsupported "
                    "or no menu present; skipping screen specs.[/]\n"
                    f"  [dim]Formats tried: {', '.join(SUPPORTED_MENU_FORMATS)}[/]"
                )
            else:
                console.print(f"  Screens detected: [green]{len(screens)}[/]")

                # Map screens to backend
                with _timed("screen map"), console.status("[bold cyan]Mapping screens to backend..."):
                    screen_mappings = map_all_screens(screens, resolved.repo_path)

                # Generate specs via LLM (keyless: structural specs from the mapping)
                if getattr(config, "no_llm", False):
                    from ..ai.screen_spec_generator import structural_screen_spec
                    screen_specs = [structural_screen_spec(m) for m in screen_mappings]
                    console.print(f"  Screen specs (structural, --no-llm): [green]{len(screen_specs)}[/]")
                    write_all_screen_specs(
                        screen_specs, docs_dir, project_slug,
                        repo_url=resolved.url or str(resolved.repo_path),
                        repo_commit=resolved.commit_sha,
                    )
                    persist_screen_specs(screen_specs, db_path, scan_id)
                elif not _budget_ok(llm_client, config, "Screen spec generation"):
                    console.print("[yellow]Budget exceeded — skipping Phase 2 LLM.[/]")
                else:
                    with _timed("screen specs"), Progress(
                        SpinnerColumn(),
                        TextColumn("[bold cyan]Generating screen specs"),
                        BarColumn(),
                        TaskProgressColumn(),
                        console=console,
                    ) as progress:
                        ptask = progress.add_task("Generating", total=len(screen_mappings))

                        def _on_screen_progress(done: int, total: int):
                            progress.update(ptask, completed=done, total=total)

                        screen_specs = generate_all_screen_specs(
                            screen_mappings,
                            llm_client,
                            db_path,
                            max_workers=max(1, config.max_concurrent or 1),
                            on_progress=_on_screen_progress,
                        )

                    # Write specs to disk
                    if screen_specs:
                        with _timed("write screen specs"), console.status(
                            "[bold cyan]Writing screen specs to disk..."
                        ):
                            write_results = write_all_screen_specs(
                                screen_specs,
                                docs_dir,
                                project_slug,
                                repo_url=resolved.url or str(resolved.repo_path),
                                repo_commit=resolved.commit_sha,
                            )
                            written = sum(1 for r in write_results if r["status"] == "written")
                            console.print(f"  Screen specs: [green]{written}/{len(screen_specs)}[/] written")

                    # Persist to DB
                    persist_screen_specs(screen_specs, db_path, scan_id)
                    screen_cost = llm_client.get_costs().get("screen", {}).get("est_usd", 0.0)
                    console.print(
                        f"  Screen cost: ${screen_cost:.4f}  "
                        f"(total so far: ${llm_client.total_cost_usd():.4f})"
                    )
    else:
        console.print("[dim]Phase 2 (screen_llm_specs): skipped (already complete)[/]")


execute = run
