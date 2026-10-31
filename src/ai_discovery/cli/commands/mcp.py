"""`discover mcp [serve|install|uninstall|print-config]` (roadmap items M5.3, M5.14; design/cli-v1.md).

``serve`` (the default) runs the stdio MCP server; stdout is reserved for the
protocol, so nothing else is printed there. ``install`` writes one server entry
per client (``--target claude|codex|cursor|vscode|all``, ``--scope project|user``;
formats in ``mcp/targets``) and ``uninstall`` removes exactly what it wrote.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer

from ...core.errors import DiscoveryError
from ..context import get_globals
from ..output import emit, handle_errors

SERVER_NAME = "ai-discovery"
TOOLS_ENV = "DISCOVER_MCP_TOOLS"
MAX_TOKENS_ENV = "DISCOVER_MCP_MAX_TOKENS"

_TOOLS_HELP = ("Tool selection: a profile (default | lean | all), tool names (allowlist), and +name / -name edits, "
               "comma-separated. Also $" + TOOLS_ENV + ".")
_TARGET_HELP = "Client(s): claude, codex, cursor, vscode, comma-separated, or all."
_INSTR_HELP = ("Also write the generated agent instructions block into AGENTS.md (agents), CLAUDE.md (claude), "
               "both, or none (default).")
_SCOPE_HELP = "project (portable file in the repo, safe to commit) or user (your home config, absolute repo path)."


def server_entry(tools: Optional[str] = None) -> dict:
    """The Claude Code project entry (also what `discover init --mcp` writes)."""
    args = ["mcp", "serve"] + (["--tools", tools] if tools else [])
    return {"command": "discover", "args": args}


def install_entry(repo: Path, tools: Optional[str] = None) -> tuple[Path, bool]:
    """Claude Code project install (kept for callers of the M5.3 API)."""
    from ...mcp.targets import install

    res = install("claude", "project", repo, tools=tools)
    return Path(res["path"]), res["changed"]


def uninstall_entry(repo: Path) -> tuple[Path, str]:
    from ...mcp.targets import uninstall

    res = uninstall("claude", "project", repo)
    return Path(res["path"]), res["result"]


def _select(tools: Optional[str]):
    from ...mcp.registry import ToolSelectionError, select

    try:
        return select(tools)
    except ToolSelectionError as exc:
        raise DiscoveryError(str(exc), "config_invalid_value", hint="see `discover mcp print-config --help`") from exc


def _targets(spec: str) -> list[str]:
    from ...mcp.targets import TARGETS

    names = [t.strip().lower() for t in spec.split(",") if t.strip()]
    if names == ["all"]:
        return list(TARGETS)
    bad = [t for t in names if t not in TARGETS]
    if bad or not names:
        raise DiscoveryError(f"unknown target {', '.join(bad) or '(none)'}", "config_invalid_value",
                             hint=f"use {', '.join(TARGETS)} or all")
    return list(dict.fromkeys(names))


def _target_error(exc) -> DiscoveryError:
    return DiscoveryError(str(exc), "config_invalid_value", hint=getattr(exc, "hint", None))


def resolve_command(command: Optional[str]) -> tuple[str, Optional[str]]:
    """(command, note). Default: `discover` when it is on PATH, else this executable's absolute path."""
    import shutil
    import sys

    if command:
        return command, None
    if shutil.which("discover"):
        return "discover", None
    me = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if me is not None and me.name.startswith("discover") and me.is_file():
        return str(me), f"`discover` is not on PATH; wrote the absolute path {me} (pass --command to override)"
    return "discover", "`discover` is not on PATH; clients may fail to start it (pass --command /abs/path)"


def _instruction_files(spec: Optional[str]) -> list[str]:
    from ...mcp.instructions import which_files

    try:
        return which_files(spec)
    except ValueError as exc:
        raise DiscoveryError(str(exc), "config_invalid_value") from exc


def _repo(ctx: typer.Context) -> Path:
    g = get_globals(ctx)
    return g.layout().repo or g.repo


def register(app: typer.Typer) -> None:
    mcp_app = typer.Typer(help="MCP server for coding agents (Claude Code, Codex, Cursor, VS Code, …).",
                          invoke_without_command=True, no_args_is_help=False)

    def _serve(ctx: typer.Context, tools: Optional[str], max_tokens: Optional[int]) -> None:
        from ...mcp.server import run_stdio
        from ...query.session import Project

        g = get_globals(ctx)
        specs = _select(tools or os.environ.get(TOOLS_ENV))
        cap = max_tokens or (int(os.environ[MAX_TOKENS_ENV]) if os.environ.get(MAX_TOKENS_ENV, "").isdigit()
                             else None)
        run_stdio(Project(g.layout()), specs, max_tokens=cap)

    @mcp_app.callback()
    def main(ctx: typer.Context) -> None:
        """Run `discover mcp serve` when no subcommand is given."""
        if ctx.invoked_subcommand is None:
            _serve(ctx, None, None)

    @mcp_app.command("serve")
    @handle_errors
    def serve(
        ctx: typer.Context,
        tools: Optional[str] = typer.Option(None, "--tools", help=_TOOLS_HELP),
        max_tokens: Optional[int] = typer.Option(None, "--max-tokens", min=500, max=100_000,
                                                 help="Per-response token cap (default: each tool's own budget)."),
    ) -> None:
        """Serve MCP tools over stdio (launched by the agent, not by hand)."""
        _serve(ctx, tools, max_tokens)

    @mcp_app.command("install")
    @handle_errors
    def install(
        ctx: typer.Context,
        target: str = typer.Option("claude", "--target", "-t", help=_TARGET_HELP),
        scope: str = typer.Option("project", "--scope", help=_SCOPE_HELP),
        tools: Optional[str] = typer.Option(None, "--tools", help=_TOOLS_HELP),
        command: Optional[str] = typer.Option(None, "--command",
                                              help="Executable the client runs (default: `discover` if on PATH, "
                                                   "else the absolute path of this executable)."),
        force: bool = typer.Option(False, "--force", help="Write even if another discovery server is configured."),
        instructions: Optional[str] = typer.Option(None, "--instructions", help=_INSTR_HELP),
    ) -> None:
        """Wire the ai-discovery server into one or more MCP clients (other servers are kept)."""
        from ...mcp import targets as tg

        g = get_globals(ctx)
        if tools:
            _select(tools)
        files = _instruction_files(instructions)
        repo = _repo(ctx)
        written = []
        if files:
            from ...mcp import instructions as ins
            from ...query.session import Project

            try:
                block = ins.render(Project(g.layout()))
            except ins.NotIndexed as exc:
                raise DiscoveryError(str(exc), "not_indexed", hint="run `discover scan`, then re-run this install") \
                    from exc
            for which in files:
                path, state = ins.write(repo, block, which)
                written.append({"file": str(path), "result": state, "tokens": ins.tokens(block)})
        command, command_note = resolve_command(command)
        results, skipped = [], []
        names = _targets(target)
        for t in names:
            try:
                results.append(tg.install(t, scope, repo, tools=tools, command=command, force=force))
                if command_note:
                    results[-1]["notes"].append(command_note)
            except tg.TargetError as exc:
                if len(names) == 1:
                    raise _target_error(exc) from exc
                skipped.append({"target": t, "reason": str(exc), "hint": exc.hint})

        def human(console) -> None:
            for r in results:
                verb = "Wrote" if r["changed"] else "Already present in"
                console.print(f"{r['target']:7} {verb} {r['path']}", highlight=False)
                for n in r["notes"]:
                    console.print(f"        [yellow]![/] {n}", highlight=False)
            for s in skipped:
                console.print(f"{s['target']:7} [yellow]skipped[/]: {s['reason']}"
                              + (f" — {s['hint']}" if s["hint"] else ""), highlight=False)
            for w in written:
                console.print(f"instructions {w['result']}: {w['file']} (~{w['tokens']} tokens)", highlight=False)
        emit(g, {"installed": results, "skipped": skipped, "instructions": written}, human)

    @mcp_app.command("uninstall")
    @handle_errors
    def uninstall(
        ctx: typer.Context,
        target: str = typer.Option("claude", "--target", "-t", help=_TARGET_HELP),
        scope: str = typer.Option("project", "--scope", help=_SCOPE_HELP),
        instructions: Optional[str] = typer.Option(None, "--instructions",
                                                   help="Also remove the instructions block from AGENTS.md (agents), "
                                                        "CLAUDE.md (claude) or both."),
    ) -> None:
        """Remove exactly the entries `install` wrote."""
        from ...mcp import targets as tg

        g = get_globals(ctx)
        repo = _repo(ctx)
        removed = []
        files = _instruction_files(instructions)
        if files:
            from ...mcp import instructions as ins

            for which in files:
                path, state = ins.remove(repo, which)
                removed.append({"file": str(path), "result": state})
        results = []
        names = _targets(target)
        for t in names:
            try:
                results.append(tg.uninstall(t, scope, repo))
            except tg.TargetError as exc:
                if len(names) == 1:
                    raise _target_error(exc) from exc
                results.append({"target": t, "scope": scope, "result": "not_applicable", "reason": str(exc)})
        msg = {"absent": "no entry in", "removed": "removed from", "file_removed": "removed (file deleted)",
               "not_applicable": "not applicable"}

        def human(console) -> None:
            for r in results:
                console.print(f"{r['target']:7} {msg[r['result']]} {r.get('path', '')}", highlight=False)
            for w in removed:
                console.print(f"instructions {w['result']}: {w['file']}", highlight=False)
        emit(g, {"uninstalled": results, "instructions": removed}, human)

    @mcp_app.command("print-config")
    @handle_errors
    def print_config(
        ctx: typer.Context,
        target: Optional[str] = typer.Option(None, "--target", "-t", help=_TARGET_HELP),
        scope: str = typer.Option("project", "--scope", help=_SCOPE_HELP),
        tools: Optional[str] = typer.Option(None, "--tools", help=_TOOLS_HELP),
        command: Optional[str] = typer.Option(None, "--command", help="Executable the client runs."),
        instructions: bool = typer.Option(False, "--instructions",
                                          help="Also print the generated agent instructions block."),
    ) -> None:
        """Print what `install` would write (per client), the tools exposed and their schema-token cost."""
        from ...mcp import targets as tg
        from ...mcp.registry import schema_tokens

        g = get_globals(ctx)
        specs = _select(tools)
        repo = _repo(ctx)
        command, _ = resolve_command(command)
        snippets = []
        for t in _targets(target or "claude"):
            try:
                snippets.append(tg.snippet(t, scope, repo, tools=tools, command=command))
            except tg.TargetError as exc:
                snippets.append({"target": t, "scope": scope, "error": str(exc), "hint": exc.hint})
        result = {"config": {"mcpServers": {SERVER_NAME: server_entry(tools)}}, "snippets": snippets,
                  "tools": [s.name for s in specs], "schema_tokens": schema_tokens(specs)}
        if instructions:
            from ...mcp import instructions as ins
            from ...query.session import Project

            try:
                block = ins.render(Project(g.layout()))
                result["instructions"] = {"text": block, "tokens": ins.tokens(block)}
            except ins.NotIndexed as exc:
                result["instructions"] = {"error": str(exc)}

        def human(console) -> None:
            for s in snippets:
                if "error" in s:
                    console.print(f"# {s['target']} ({s['scope']}): {s['error']}", highlight=False, markup=False)
                    if s.get("hint"):
                        console.print(f"#   {s['hint']}", highlight=False, markup=False)
                    continue
                console.print(f"# {s['target']} ({s['scope']}) → {s['path']}", highlight=False, markup=False)
                console.print(s["text"], highlight=False, markup=False)
            console.print(f"tools ({len(specs)}, ~{result['schema_tokens']} schema tokens): "
                          + ", ".join(result["tools"]), highlight=False)
            if "instructions" in result:
                ins_ = result["instructions"]
                console.print(ins_.get("text") or f"# instructions: {ins_['error']}", highlight=False, markup=False)
        emit(g, result, human)

    app.add_typer(mcp_app, name="mcp")
