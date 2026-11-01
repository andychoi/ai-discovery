"""Generated agent instructions block (roadmap item M5.11)."""

from __future__ import annotations

import json
import shutil

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.mcp import instructions as ins
from ai_discovery.query import Project

runner = CliRunner()
BLOCK = f"{ins.BEGIN}\nhello\n{ins.END}\n"


def test_render_is_deterministic_capped_and_complete(scanned) -> None:
    a, b = ins.render(scanned), ins.render(scanned)
    assert a == b and a.startswith(ins.BEGIN) and a.rstrip().endswith(ins.END)
    assert ins.tokens(a) <= ins.DEFAULT_MAX_TOKENS
    for needle in ("get_overview", "get_impact", "get_change_impact", "working_tree", "content_trust",
                   "java (14 files)", "Entry points", "Screens"):
        assert needle in a, needle


def test_cap_drops_list_entries_not_workflow(scanned) -> None:
    full = ins.render(scanned)
    small = ins.render(scanned, max_tokens=ins.tokens(full) - 60)
    assert ins.tokens(small) <= ins.tokens(full) - 60
    assert small.count("`com.example.") < full.count("`com.example.") and "get_change_impact" in small
    tiny = ins.render(scanned, max_tokens=10)          # cannot fit: workflow kept whole, lists emptied
    assert tiny.rstrip().endswith(ins.END) and "Entry points" not in tiny and "get_impact" in tiny


def test_not_indexed(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(ins.NotIndexed):
        ins.render(Project.open(tmp_path))


def test_write_and_remove_preserve_the_rest(tmp_path) -> None:
    original = "# Project rules\n\nUse tabs.\n"
    (tmp_path / "CLAUDE.md").write_text(original)
    assert ins.write(tmp_path, BLOCK, "claude")[1] == "updated"
    text = (tmp_path / "CLAUDE.md").read_text()
    assert text.startswith(original) and text.endswith(BLOCK)
    assert ins.write(tmp_path, BLOCK, "claude")[1] == "unchanged"
    newer = BLOCK.replace("hello", "hello again")
    assert ins.write(tmp_path, newer, "claude")[1] == "updated"
    assert (tmp_path / "CLAUDE.md").read_text().count(ins.END) == 1 and "hello again" in \
        (tmp_path / "CLAUDE.md").read_text()
    assert ins.remove(tmp_path, "claude")[1] == "removed"
    assert (tmp_path / "CLAUDE.md").read_text() == original          # byte-identical
    assert ins.remove(tmp_path, "claude")[1] == "absent"


def test_created_file_is_deleted_on_remove(tmp_path) -> None:
    assert ins.write(tmp_path, BLOCK, "agents")[1] == "created"
    assert (tmp_path / "AGENTS.md").read_text() == BLOCK
    assert ins.remove(tmp_path, "agents")[1] == "file_removed" and not (tmp_path / "AGENTS.md").exists()


def test_block_in_the_middle_is_replaced_in_place(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text(f"top\n\n{BLOCK}\nbottom\n")
    ins.write(tmp_path, BLOCK.replace("hello", "x"), "agents")
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.startswith("top\n\n") and text.rstrip().endswith("bottom") and "\nx\n" in text


def test_which_files() -> None:
    assert ins.which_files("both") == ["agents", "claude"] and ins.which_files(None) == []
    with pytest.raises(ValueError):
        ins.which_files("readme")


def test_cli_install_and_uninstall_instructions(scanned, tmp_path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(scanned.layout.repo, repo)                     # keep the shared fixture repo clean
    (repo / "CLAUDE.md").write_text("# mine\n")
    args = ["--json", "-C", str(repo), "mcp"]
    res = runner.invoke(app, args + ["install", "--command", "discover", "--instructions", "both"])
    assert res.exit_code == 0, res.output
    written = {w["result"] for w in json.loads(res.output)["instructions"]}
    assert written == {"created", "updated"}
    assert ins.BEGIN in (repo / "AGENTS.md").read_text() and (repo / "CLAUDE.md").read_text().startswith("# mine\n")
    res = runner.invoke(app, args + ["uninstall", "--instructions", "both"])
    assert res.exit_code == 0, res.output
    assert not (repo / "AGENTS.md").exists() and (repo / "CLAUDE.md").read_text() == "# mine\n"


def test_cli_instructions_errors(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    args = ["--json", "-C", str(tmp_path), "mcp", "install", "--command", "discover", "--instructions"]
    res = runner.invoke(app, args + ["agents"])
    assert res.exit_code == 5 and json.loads(res.output)["error"]["reason"] == "not_indexed"
    assert not (tmp_path / ".mcp.json").exists()                    # nothing written when it refuses
    assert runner.invoke(app, args + ["readme"]).exit_code == 2
