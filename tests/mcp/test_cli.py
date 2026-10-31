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


def _one(res, key: str) -> dict:
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)[key]
    assert len(out) == 1
    return out[0]


def test_install_uninstall_symmetric(tmp_path) -> None:
    repo = tmp_path
    (repo / ".git").mkdir()
    assert _one(_run(repo, "install", "--command", "discover"), "installed")["changed"] is True
    data = json.loads((repo / ".mcp.json").read_text())
    assert data == {"mcpServers": {"ai-discovery": {"command": "discover", "args": ["mcp", "serve"]}}}
    assert _one(_run(repo, "install", "--command", "discover"), "installed")["changed"] is False   # idempotent
    assert _one(_run(repo, "uninstall"), "uninstalled")["result"] == "file_removed"
    assert not (repo / ".mcp.json").exists()
    assert _one(_run(repo, "uninstall"), "uninstalled")["result"] == "absent"


def test_install_keeps_other_servers(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    other = {"mcpServers": {"other": {"command": "x"}}, "note": "keep"}
    (tmp_path / ".mcp.json").write_text(json.dumps(other))
    _one(_run(tmp_path, "install", "--tools", "lean,+get_table", "--command", "discover"), "installed")
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["ai-discovery"]["args"] == ["mcp", "serve", "--tools", "lean,+get_table"]
    assert data["mcpServers"]["other"] == {"command": "x"} and data["note"] == "keep"
    assert _one(_run(tmp_path, "uninstall"), "uninstalled")["result"] == "removed"
    assert json.loads((tmp_path / ".mcp.json").read_text()) == {"mcpServers": {"other": {"command": "x"}},
                                                                 "note": "keep"}


def test_install_rejects_bad_tools_target_and_claude_user_scope(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    res = _run(tmp_path, "install", "--tools", "nope")
    assert res.exit_code == 2 and json.loads(res.output)["error"]["reason"] == "config_invalid_value"
    assert _run(tmp_path, "install", "--target", "emacs").exit_code == 2
    user = _run(tmp_path, "install", "--scope", "user")             # claude user scope: print the command instead
    assert user.exit_code == 2 and "claude mcp add --scope user" in json.loads(user.output)["error"]["hint"]
    assert not (tmp_path / ".mcp.json").exists()


def test_install_all_targets_and_doctor(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home" / ".codex"))
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "home" / ".copilot"))
    out = json.loads(_run(repo, "install", "--target", "all", "--command", sys.executable).output)
    assert {r["target"] for r in out["installed"]} == {"claude", "codex", "cursor", "vscode"}
    assert (repo / ".cursor/mcp.json").exists() and (repo / ".vscode/mcp.json").exists()
    assert (repo / ".codex/config.toml").exists()
    doc = runner.invoke(app, ["--json", "-C", str(repo), "doctor"])
    checks = {c["name"]: c for c in json.loads(doc.output)["checks"]}
    assert all(checks[f"mcp {t} (project)"]["status"] == "ok" for t in ("claude", "codex", "cursor", "vscode"))
    gone = json.loads(_run(repo, "uninstall", "--target", "all").output)["uninstalled"]
    assert {r["result"] for r in gone} == {"file_removed"}
    assert sorted(p.name for p in repo.iterdir()) == [".git"]         # nothing left behind


def test_print_config(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    out = json.loads(_run(tmp_path, "print-config", "--tools", "lean").output)
    assert out["config"]["mcpServers"]["ai-discovery"]["args"][-1] == "lean"
    assert len(out["tools"]) <= 5 and out["schema_tokens"] > 0
    codex = json.loads(_run(tmp_path, "print-config", "--target", "codex", "--command", "discover").output)
    assert codex["snippets"][0]["text"].count('[mcp_servers."ai-discovery"]') == 1
    assert not (tmp_path / ".codex").exists()                        # print-config writes nothing


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
