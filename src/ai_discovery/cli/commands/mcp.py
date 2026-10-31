"""`discover mcp [serve|install|uninstall|print-config]` (roadmap item M5.3, design/cli-v1.md).

``serve`` (the default) runs the stdio MCP server; stdout is reserved for the
protocol, so nothing else is printed there. ``install`` merges one server entry
into ``<repo>/.mcp.json`` and ``uninstall`` removes exactly that entry (and the
file, if nothing else is left in it).
"""

from __future__ import annotations

import json
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


def server_entry(tools: Optional[str] = None) -> dict:
    args = ["mcp", "serve"] + (["--tools", tools] if tools else [])
    return {"command": "discover", "args": args}


def _read_mcp_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except ValueError as exc:
        raise DiscoveryError(f"{path} is not valid JSON; fix or remove it first", "config_invalid_value") from exc
    if not isinstance(data, dict):
        raise DiscoveryError(f"{path} must contain a JSON object", "config_invalid_value")
    return data


def install_entry(repo: Path, tools: Optional[str] = None) -> tuple[Path, bool]:
    """Write our server into ``<repo>/.mcp.json``; returns (path, changed). Other servers are kept."""
    path = repo / ".mcp.json"
    data = _read_mcp_json(path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise DiscoveryError(f"{path}: `mcpServers` must be an object", "config_invalid_value")
    entry = server_entry(tools)
    if servers.get(SERVER_NAME) == entry:
        return path, False
    servers[SERVER_NAME] = entry
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path, True


def uninstall_entry(repo: Path) -> tuple[Path, str]:
    """Remove our server from ``.mcp.json``. Returns (path, "removed" | "file_removed" | "absent")."""
    path = repo / ".mcp.json"
    data = _read_mcp_json(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return path, "absent"
    del servers[SERVER_NAME]
    if not servers and set(data) == {"mcpServers"}:
        path.unlink()
        return path, "file_removed"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path, "removed"


def _select(tools: Optional[str]):
    from ...mcp.registry import ToolSelectionError, select

    try:
        return select(tools)
    except ToolSelectionError as exc:
        raise DiscoveryError(str(exc), "config_invalid_value", hint="see `discover mcp print-config --help`") from exc


def _repo(ctx: typer.Context) -> Path:
    layout = get_globals(ctx).layout()
    return layout.repo or get_globals(ctx).repo


def register(app: typer.Typer) -> None:
    mcp_app = typer.Typer(help="MCP server for coding agents (Claude Code, Codex, Cursor, …).",
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
        tools: Optional[str] = typer.Option(None, "--tools", help=_TOOLS_HELP),
        scope: str = typer.Option("project", "--scope", help="project (writes <repo>/.mcp.json)."),
    ) -> None:
        """Add the ai-discovery server to the project's .mcp.json (other servers are kept)."""
        g = get_globals(ctx)
        if scope != "project":
            raise DiscoveryError(f"--scope {scope} is not supported yet", "config_invalid_value",
                                 hint="use --scope project; per-client and user-level installs arrive with M5.14")
        if tools:
            _select(tools)
        path, changed = install_entry(_repo(ctx), tools)
        emit(g, {"path": str(path), "changed": changed, "server": SERVER_NAME, "entry": server_entry(tools)},
             f"{'Wrote' if changed else 'Already present in'} {path}")

    @mcp_app.command("uninstall")
    @handle_errors
    def uninstall(ctx: typer.Context) -> None:
        """Remove exactly the entry `install` wrote."""
        g = get_globals(ctx)
        path, state = uninstall_entry(_repo(ctx))
        msg = {"absent": f"No {SERVER_NAME} entry in {path}", "removed": f"Removed {SERVER_NAME} from {path}",
               "file_removed": f"Removed {path} (it held only {SERVER_NAME})"}[state]
        emit(g, {"path": str(path), "result": state}, msg)

    @mcp_app.command("print-config")
    @handle_errors
    def print_config(
        ctx: typer.Context,
        tools: Optional[str] = typer.Option(None, "--tools", help=_TOOLS_HELP),
    ) -> None:
        """Print the .mcp.json snippet and the tools it would expose (with schema token cost)."""
        from ...mcp.registry import schema_tokens

        g = get_globals(ctx)
        specs = _select(tools)
        snippet = {"mcpServers": {SERVER_NAME: server_entry(tools)}}
        result = {"config": snippet, "tools": [s.name for s in specs], "schema_tokens": schema_tokens(specs)}

        def human(console) -> None:
            console.print(json.dumps(snippet, indent=2), highlight=False, markup=False)
            console.print(f"tools ({len(specs)}, ~{result['schema_tokens']} schema tokens): "
                          + ", ".join(result["tools"]), highlight=False)
        emit(g, result, human)

    app.add_typer(mcp_app, name="mcp")
