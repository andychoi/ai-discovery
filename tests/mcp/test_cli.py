"""`discover mcp` command: install/uninstall symmetry, print-config, real stdio session (M5.3)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import anyio
import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app

runner = CliRunner()


def _run(repo: Path, *args: str):
    return runner.invoke(app, ["--json", "-C", str(repo), "mcp", *args])


def test_install_uninstall_symmetric(tmp_path) -> None:
    repo = tmp_path
    (repo / ".git").mkdir()
    res = _run(repo, "install")
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["changed"] is True
    data = json.loads((repo / ".mcp.json").read_text())
    assert data == {"mcpServers": {"ai-discovery": {"command": "discover", "args": ["mcp", "serve"]}}}
    assert json.loads(_run(repo, "install").output)["changed"] is False          # idempotent
    assert json.loads(_run(repo, "uninstall").output)["result"] == "file_removed"
    assert not (repo / ".mcp.json").exists()
    assert json.loads(_run(repo, "uninstall").output)["result"] == "absent"


def test_install_keeps_other_servers(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    other = {"mcpServers": {"other": {"command": "x"}}, "note": "keep"}
    (tmp_path / ".mcp.json").write_text(json.dumps(other))
    assert _run(tmp_path, "install", "--tools", "lean,+get_table").exit_code == 0
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["ai-discovery"]["args"] == ["mcp", "serve", "--tools", "lean,+get_table"]
    assert data["mcpServers"]["other"] == {"command": "x"} and data["note"] == "keep"
    assert json.loads(_run(tmp_path, "uninstall").output)["result"] == "removed"
    assert json.loads((tmp_path / ".mcp.json").read_text()) == {"mcpServers": {"other": {"command": "x"}},
                                                                 "note": "keep"}


def test_install_rejects_bad_tools_and_scope(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    res = _run(tmp_path, "install", "--tools", "nope")
    assert res.exit_code == 2 and json.loads(res.output)["error"]["reason"] == "config_invalid_value"
    assert _run(tmp_path, "install", "--scope", "user").exit_code == 2
    assert not (tmp_path / ".mcp.json").exists()


def test_print_config(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    out = json.loads(_run(tmp_path, "print-config", "--tools", "lean").output)
    assert out["config"]["mcpServers"]["ai-discovery"]["args"][-1] == "lean"
    assert len(out["tools"]) <= 5 and out["schema_tokens"] > 0


def test_stdio_session_end_to_end(scanned) -> None:
    """Launch `discover mcp serve` as a subprocess and talk MCP over its stdin/stdout."""
    from mcp import Client, StdioServerParameters

    exe = shutil.which("discover", path=str(Path(sys.executable).parent))
    if exe is None:
        pytest.skip("discover entry point not installed in this environment")
    params = StdioServerParameters(command=exe, args=["-C", str(scanned.layout.repo), "mcp", "serve",
                                                      "--tools", "lean"])

    async def go():
        async with Client(params) as client:
            names = [t.name for t in (await client.list_tools()).tools]
            res = await client.call_tool("search", {"query": "getAllProducts", "limit": 3})
            return names, res

    names, res = anyio.run(go)
    assert len(names) <= 5 and "search" in names
    assert res.is_error is False and res.structured_content["data"]["hits"][0]["id"].endswith("getAllProducts")
