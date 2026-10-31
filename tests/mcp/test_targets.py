"""MCP client install targets (roadmap item M5.14)."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from ai_discovery.mcp import targets as tg


@pytest.fixture()
def env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("COPILOT_HOME", str(home / ".copilot"))
    return repo, home


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_project_formats(env) -> None:
    repo, _ = env
    for t in tg.TARGETS:
        tg.install(t, "project", repo)
    assert _json(repo / ".mcp.json")["mcpServers"]["ai-discovery"] == {"command": "discover", "args": ["mcp", "serve"]}
    cursor = _json(repo / ".cursor/mcp.json")["mcpServers"]["ai-discovery"]
    assert cursor == {"type": "stdio", "command": "discover", "args": ["-C", "${workspaceFolder}", "mcp", "serve"]}
    vscode = _json(repo / ".vscode/mcp.json")["servers"]["ai-discovery"]
    assert vscode["type"] == "stdio" and vscode["args"][:2] == ["-C", "${workspaceFolder}"]
    toml = (repo / ".codex/config.toml").read_text()
    assert '[mcp_servers."ai-discovery"]' in toml and 'args = ["mcp", "serve"]' in toml and "cwd" not in toml
    if sys.version_info >= (3, 11):
        import tomllib
        assert tomllib.loads(toml)["mcp_servers"]["ai-discovery"]["command"] == "discover"


def test_user_scope_is_absolute_and_per_repo(env) -> None:
    repo, home = env
    name = tg.user_server_name(repo)
    assert name.startswith("ai-discovery-repo-") and name == tg.user_server_name(repo)
    res = tg.install("cursor", "user", repo, tools="lean")
    assert res["path"] == str(home / ".cursor/mcp.json")
    e = _json(home / ".cursor/mcp.json")["mcpServers"][name]
    assert e["args"] == ["-C", str(repo.resolve()), "mcp", "serve", "--tools", "lean"]
    tg.install("codex", "user", repo)
    toml = (home / ".codex/config.toml").read_text()
    assert f'cwd = "{repo.resolve()}"' in toml
    tg.install("vscode", "user", repo)
    assert name in _json(home / ".copilot/mcp-config.json")["mcpServers"]
    with pytest.raises(tg.TargetError) as exc:
        tg.install("claude", "user", repo)
    assert "claude mcp add --scope user" in exc.value.hint


def test_toml_edits_only_inside_markers(env) -> None:
    repo, _ = env
    cfg = repo / ".codex/config.toml"
    cfg.parent.mkdir()
    cfg.write_text('model = "x"\n\n[mcp_servers.other]\ncommand = "npx"\nargs = ["y"]\n')
    first = tg.install("codex", "project", repo)
    again = tg.install("codex", "project", repo)
    assert first["changed"] and not again["changed"]
    tg.install("codex", "project", repo, tools="lean")             # replaced in place, not appended twice
    text = cfg.read_text()
    assert text.count("ai-discovery (managed") == 1 and '"--tools", "lean"' in text
    assert tg.uninstall("codex", "project", repo)["result"] == "removed"
    assert cfg.read_text() == 'model = "x"\n\n[mcp_servers.other]\ncommand = "npx"\nargs = ["y"]\n'


def test_duplicate_guard_json_and_toml(env) -> None:
    repo, _ = env
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"discovery": {"command": "discover",
                                                                             "args": ["mcp", "serve"]}}}))
    with pytest.raises(tg.TargetError, match="another name: discovery"):
        tg.install("claude", "project", repo)
    assert tg.install("claude", "project", repo, force=True)["changed"]
    cfg = repo / ".codex/config.toml"
    cfg.parent.mkdir()
    cfg.write_text('[mcp_servers.ai-discovery]\ncommand = "discover"\nargs = ["mcp", "serve"]\n')   # hand-written
    with pytest.raises(tg.TargetError, match="already defines"):
        tg.install("codex", "project", repo)


def test_plugin_guard(env) -> None:
    repo, _ = env
    (repo / ".claude").mkdir()
    (repo / ".claude/settings.json").write_text(json.dumps({"enabledPlugins": {"ai-discovery@market": True}}))
    with pytest.raises(tg.TargetError, match="plugin is enabled"):
        tg.install("claude", "project", repo)
    assert tg.install("cursor", "project", repo)["changed"]        # other clients are unaffected


def test_vscode_warns_about_shared_mcp_json(env) -> None:
    repo, _ = env
    tg.install("claude", "project", repo)
    assert any("workspaceRootConfig" in n for n in tg.install("vscode", "project", repo)["notes"])


def test_uninstall_symmetric_and_leaves_foreign_files(env) -> None:
    repo, _ = env
    (repo / ".vscode").mkdir()
    (repo / ".vscode/settings.json").write_text("{}")
    tg.install("vscode", "project", repo)
    assert tg.uninstall("vscode", "project", repo)["result"] == "file_removed"
    assert (repo / ".vscode/settings.json").exists()                # dir kept: not ours to remove
    tg.install("cursor", "project", repo)
    tg.uninstall("cursor", "project", repo)
    assert not (repo / ".cursor").exists()                          # we created it, it is empty: removed
    assert tg.uninstall("cursor", "project", repo)["result"] == "absent"


def test_installed_reports_launchability(env, tmp_path) -> None:
    repo, _ = env
    exe = tmp_path / "bin" / "discover"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    tg.install("cursor", "project", repo, command=str(exe))
    tg.install("claude", "project", repo, command="definitely-not-on-path-xyz")
    found = {(r["target"], r["scope"]): r for r in tg.installed(repo)}
    assert found[("cursor", "project")]["status"] == "ok"
    bad = found[("claude", "project")]
    assert bad["status"] == "fail" and "--command" in bad["fix"]


def test_snippet_writes_nothing(env) -> None:
    repo, _ = env
    s = tg.snippet("vscode", "project", repo)
    assert s["format"] == "servers" and json.loads(s["text"])["servers"]["ai-discovery"]["type"] == "stdio"
    assert not (repo / ".vscode").exists()


def test_resolve_command(monkeypatch, tmp_path) -> None:
    from ai_discovery.cli.commands import mcp as cmd

    assert cmd.resolve_command("/opt/x/discover") == ("/opt/x/discover", None)
    monkeypatch.setenv("PATH", str(tmp_path))                       # discover not on PATH
    fake = tmp_path / "venv" / "discover"
    fake.parent.mkdir()
    fake.write_text("")
    monkeypatch.setattr(sys, "argv", [str(fake)])
    command, note = cmd.resolve_command(None)
    assert command == str(fake.resolve()) and "not on PATH" in note
    assert os.environ["PATH"] == str(tmp_path)
