"""`discover` CLI (roadmap M2: v1 CLI).

Assembly: the root Typer ``app`` gets the global flags (``context.py``) and the
v1 commands from ``commands/`` — one module per command, each importing its
heavy dependencies inside the command body so ``discover --help`` stays fast.
The pre-v1 commands (chat, query, federate, view, test-llm, detect-screens,
export-graph, verify-drift, ingest, ingest-docs) were removed in M2.7; see
``docs/roadmap/design/cli-v1.md`` §6 for what replaces each.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from rich.console import Console

from .context import PROGRESS_MODES, Globals, apply_no_color, hoist_global_flags

console = Console()
app = typer.Typer(name="discover", help="Brownfield codebase discovery: a code graph, a wiki and an MCP server.")


def _version_callback(value: bool) -> None:
    if value:
        from .. import __version__

        typer.echo(f"discover {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    repo: Optional[Path] = typer.Option(None, "-C", "--repo", help="Run as if started in PATH."),
    state_dir: Optional[Path] = typer.Option(None, "--state-dir", help="Use DIR instead of <repo>/.discovery/."),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as one JSON document on stdout."),
    progress: str = typer.Option("rich", "--progress", help="Progress output: rich | json (NDJSON) | none."),
    verbose: int = typer.Option(0, "-v", "--verbose", count=True, help="More logging (-vv for debug)."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Only errors."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colour (also NO_COLOR)."),
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True,
                                 help="Print the version and exit."),
) -> None:
    """Brownfield codebase discovery & doc generation."""
    from ..core.errors import DiscoveryError
    from ..core.logging import configure_logging
    from .output import fail

    g = Globals(
        repo=(repo or Path.cwd()).expanduser().resolve(),
        state_dir=state_dir,
        json=json_out,
        progress=progress,
        verbosity=verbose,
        quiet=quiet,
        no_color=apply_no_color(no_color),
    )
    ctx.obj = g
    if g.no_color:
        console.no_color = True
    configure_logging(verbose, quiet)
    if progress not in PROGRESS_MODES:
        fail(g, DiscoveryError(f"--progress must be one of {', '.join(PROGRESS_MODES)}", "contradictory_flags"))
    if verbose and quiet:
        fail(g, DiscoveryError("-v and -q contradict each other; pass one", "contradictory_flags"))
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


def _register_v1_commands() -> None:
    from .commands import register

    register(app)


_register_v1_commands()


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point: hoist global flags, then dispatch."""
    args = hoist_global_flags(list(sys.argv[1:] if argv is None else argv))
    app(args=args, prog_name="discover")
