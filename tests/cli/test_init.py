"""`discover init` + first-run menu (roadmap item M2.2)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.cli.commands import init as init_mod
from ai_discovery.config.loader import load_config

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    shutil.copytree(FIXTURES / "projects" / "spring-boot-app", r)
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


def _json(result) -> dict:
    return json.loads(result.output.strip().splitlines()[-1])


def _config(repo: Path):
    return load_config(repo / ".discovery" / "config.yaml", include_user=False)


# ── non-interactive ───────────────────────────────────────────────────────

def test_non_tty_without_choice_exits_2(repo: Path) -> None:
    result = runner.invoke(app, ["--json", "-C", str(repo), "init"])
    assert result.exit_code == 2
    err = _json(result)["error"]
    assert err["reason"] == "init_needs_choice" and "--yes" in err["hint"]
    assert not (repo / ".discovery").exists()


def test_yes_writes_structure_only_config(repo: Path) -> None:
    result = runner.invoke(app, ["--json", "-C", str(repo), "init", "--yes"])
    assert result.exit_code == 0, result.output
    res = _json(result)
    assert res["prose"] is False and res["provider"] == "aigw"
    assert set(res["written"]) == {str(repo.resolve() / ".discovery" / ".gitignore"),
                                   str(repo.resolve() / ".discovery" / "config.yaml")}
    assert (repo / ".discovery" / ".gitignore").read_text() == "*\n!config.yaml\n!.gitignore\n"
    cfg = _config(repo)
    assert cfg.prose is False and cfg.provider == "aigw"


def test_prose_and_provider_flags(repo: Path) -> None:
    result = runner.invoke(app, ["-C", str(repo), "init", "--prose", "--provider", "openrouter"])
    assert result.exit_code == 0, result.output
    cfg = _config(repo)
    assert cfg.prose is True and cfg.provider == "openrouter"


def test_path_argument_and_state_dir(repo: Path, tmp_path: Path) -> None:
    state = tmp_path / "elsewhere"
    result = runner.invoke(app, ["--state-dir", str(state), "init", str(repo), "--yes"])
    assert result.exit_code == 0, result.output
    assert (state / "config.yaml").exists() and not (repo / ".discovery").exists()


def test_existing_config_needs_force(repo: Path) -> None:
    assert runner.invoke(app, ["-C", str(repo), "init", "--yes"]).exit_code == 0
    again = runner.invoke(app, ["--json", "-C", str(repo), "init", "--prose"])
    assert again.exit_code == 2 and "--force" in _json(again)["error"]["hint"]
    assert runner.invoke(app, ["-C", str(repo), "init", "--prose", "--force"]).exit_code == 0
    assert _config(repo).prose is True


def test_unknown_provider_rejected(repo: Path) -> None:
    result = runner.invoke(app, ["--json", "-C", str(repo), "init", "--yes", "--provider", "nope"])
    assert result.exit_code == 2 and _json(result)["error"]["reason"] == "config_invalid_value"


# ── interactive menu ──────────────────────────────────────────────────────

@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(init_mod, "_is_interactive", lambda: True)


def test_menu_structure_only(repo: Path, tty) -> None:
    result = runner.invoke(app, ["-C", str(repo), "init"], input="1\nn\n")
    assert result.exit_code == 0, result.output
    assert "Structure only" in result.output and _config(repo).prose is False


def test_menu_prose_picks_provider(repo: Path, tty) -> None:
    result = runner.invoke(app, ["-C", str(repo), "init"], input="2\nanthropic\nn\n")
    assert result.exit_code == 0, result.output
    cfg = _config(repo)
    assert cfg.prose is True and cfg.provider == "anthropic"


def test_menu_advanced(repo: Path, tty) -> None:
    result = runner.invoke(app, ["-C", str(repo), "init"], input="3\nopenrouter\ny\n25\n4\nn\n")
    assert result.exit_code == 0, result.output
    cfg = _config(repo)
    assert (cfg.prose, cfg.provider, cfg.budget_limit_usd, cfg.max_concurrent) == (True, "openrouter", 25.0, 4)


def test_menu_eof_is_treated_as_non_interactive(repo: Path, tty) -> None:
    result = runner.invoke(app, ["--no-color", "-C", str(repo), "init"], input="")
    assert result.exit_code == 2 and "init_needs_choice" in result.output
    assert not (repo / ".discovery" / "config.yaml").exists()


# ── detection ─────────────────────────────────────────────────────────────

def test_detects_languages_menu_and_framework(repo: Path) -> None:
    stack = init_mod.detect_stack(repo)
    assert "java" in stack.languages
    assert stack.menu_format and stack.menu_entries > 0  # src/config/menu.json
    assert stack.framework  # Spring


def test_detects_sap_markers(tmp_path: Path) -> None:
    (tmp_path / ".abapgit.xml").write_text("<asx:abap/>", encoding="utf-8")
    (tmp_path / "webapp").mkdir()
    (tmp_path / "webapp" / "manifest.json").write_text('{"sap.app": {"id": "x"}}', encoding="utf-8")
    assert init_mod.detect_stack(tmp_path).sap == ["abapGit", "UI5/Fiori"]


def test_detection_is_printed(repo: Path) -> None:
    result = runner.invoke(app, ["--no-color", "-C", str(repo), "init", "--yes"])
    text = " ".join(result.output.split())
    assert "languages: java" in text and "menu:" in text


# ── MCP entry ─────────────────────────────────────────────────────────────

def test_mcp_flag_writes_entry_now_that_server_exists(repo: Path) -> None:
    # M5.3 shipped `discover mcp`, so `init --mcp` writes the entry instead of a "not available yet" note.
    result = runner.invoke(app, ["--json", "-C", str(repo), "init", "--yes", "--mcp"])
    assert result.exit_code == 0
    assert not any("M5.3" in n for n in _json(result)["notes"])
    servers = json.loads((repo / ".mcp.json").read_text())["mcpServers"]
    assert servers["ai-discovery"] == {"command": "discover", "args": ["mcp", "serve"]}


def test_write_mcp_entry_merges(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    init_mod.write_mcp_entry(tmp_path)
    servers = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"other", "ai-discovery"}
    assert servers["ai-discovery"] == {"command": "discover", "args": ["mcp", "serve"]}
