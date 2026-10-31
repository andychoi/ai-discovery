"""Local MCP tool-use ledger (roadmap item M5.13)."""

from __future__ import annotations

import json
import shutil

import anyio
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.mcp.ledger import FILE_NAME, Ledger, entry_for, read_entries, summarize


def _answer(status="ok", tokens=100, truncated=False, timing=5.0, reason=None):
    return {"status": status, "data": {"reason": reason} if reason else {}, "truncated": truncated,
            "meta": {"response_budget": {"used_tokens": tokens, "max_tokens": 4000}, "omitted_count": 3 if truncated
                     else 0, "timing_ms": timing, "scan_id": 7, "behind_by": 0}}


def test_entry_hashes_arguments_never_stores_values() -> None:
    e = entry_for("get_change_impact", "claude-code", {"diff": "secret = 'hunter2'", "depth": 2}, _answer())
    assert e["arg_keys"] == ["depth", "diff"] and len(e["args_sha"]) == 16
    assert "hunter2" not in json.dumps(e)
    assert entry_for("x", "", {"b": 1, "a": 2}, _answer())["args_sha"] == \
        entry_for("x", "", {"a": 2, "b": 1}, _answer())["args_sha"]


def test_rotation_keeps_bounded_history(tmp_path) -> None:
    led = Ledger(tmp_path / FILE_NAME, max_bytes=600, keep=2)
    for i in range(40):
        led.record(entry_for("search", "c", {"q": i}, _answer()))
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [FILE_NAME, f"{FILE_NAME}.1", f"{FILE_NAME}.2"]
    entries = list(read_entries(tmp_path / FILE_NAME))
    assert 0 < len(entries) < 40
    assert [e["ts"] for e in entries] == sorted(e["ts"] for e in entries)      # oldest first across files


def test_summarize(tmp_path) -> None:
    led = Ledger(tmp_path / FILE_NAME)
    led.record(entry_for("search", "claude-code", {"q": "a"}, _answer(timing=10)))
    led.record(entry_for("search", "claude-code", {"q": "a"}, _answer(timing=30, truncated=True)))
    led.record(entry_for("get_context", "codex", {"t": "x"}, _answer(status="not_found", reason="x")))
    (tmp_path / FILE_NAME).open("a").write("not json\n")
    s = summarize(tmp_path / FILE_NAME)
    assert s["calls"] == 3 and s["clients"] == {"claude-code": 2, "codex": 1}
    assert s["tools"]["search"]["calls"] == 2 and s["tools"]["search"]["repeat_calls"] == 1
    assert s["tools"]["search"]["truncated"] == 1 and s["tools"]["get_context"]["not_found"] == 1
    assert s["rates"]["not_found"] == round(1 / 3, 3)
    assert summarize(tmp_path / "missing.jsonl")["calls"] == 0


def test_disable_and_failure_safety(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DISCOVER_MCP_LEDGER", "0")
    assert Ledger.for_state_dir(tmp_path) is None
    monkeypatch.delenv("DISCOVER_MCP_LEDGER")
    assert Ledger.for_state_dir(tmp_path).path == tmp_path / FILE_NAME
    blocker = tmp_path / "file"
    blocker.write_text("")
    Ledger(blocker / "sub" / FILE_NAME).record({"tool": "x"})       # parent is a file: write fails silently


def test_server_records_each_call(scanned, tmp_path) -> None:
    from mcp import Client

    from ai_discovery.mcp.registry import select
    from ai_discovery.mcp.server import create_server

    led = Ledger(tmp_path / FILE_NAME)

    async def go():
        async with Client(create_server(scanned, select("default"), ledger=led)) as client:
            await client.call_tool("search", {"query": "orders"})
            await client.call_tool("get_context", {"target": "list"})
            await client.call_tool("no_such_tool", {})

    anyio.run(go)
    rows = list(read_entries(led.path))
    assert [r["tool"] for r in rows] == ["search", "get_context", "no_such_tool"]
    assert [r["status"] for r in rows] == ["ok", "unknown", "unknown"]
    assert rows[1]["reason"] == "ambiguous" and rows[2]["reason"] == "unknown_tool"
    assert all(r["client"] for r in rows) and rows[0]["scan_id"] is not None


def test_status_agent_section(scanned, tmp_path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(scanned.layout.repo, repo)
    logs = repo / ".discovery" / "logs"
    for old in logs.glob(f"{FILE_NAME}*"):      # the shared fixture may already have real calls (stdio test)
        old.unlink()
    led = Ledger(logs / FILE_NAME)
    led.record(entry_for("get_impact", "claude-code", {"target": "x"}, _answer(timing=12)))
    res = CliRunner().invoke(app, ["--json", "-C", str(repo), "status", "--agent"])
    assert res.exit_code == 0, res.output
    agent = json.loads(res.output)["agent"]
    assert agent["calls"] == 1 and agent["tools"]["get_impact"]["p50_ms"] == 12
    human = CliRunner().invoke(app, ["-C", str(repo), "status", "--agent"])
    assert "Agent tool use: 1 calls" in human.output and "get_impact" in human.output
    plain = json.loads(CliRunner().invoke(app, ["--json", "-C", str(repo), "status"]).output)
    assert "agent" not in plain
