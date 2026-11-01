"""CLI smoke tests for the current (pre-v1) `discover` CLI (roadmap item M0.3).

Each of the 13 commands gets `--help` plus one real path that needs no network,
LLM or prior scan. These document *current* behaviour so later changes are
deliberate. Known bugs were asserted as the *correct* behaviour under
``xfail(strict=True)``; M0.9 fixed the two pinned here (ingest-docs NameError,
verify-drift exit code) and removed their markers.

The v1 CLI (M2) replaces this file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SPRING_APP = FIXTURES / "projects" / "spring-boot-app"

# M2.7: the legacy commands are gone; every command is a v1 one (`mcp` is a group, listed separately).
V1_COMMANDS = ["init", "scan", "status", "doctor", "serve", "export", "drift", "push", "ask", "impact"]
COMMANDS = V1_COMMANDS
REMOVED = ["chat", "query", "federate", "view", "test-llm", "ingest-docs", "export-graph", "detect-screens",
           "verify-drift", "ingest"]

runner = CliRunner()


def _invoke(*args: str, cwd: Path | None = None):
    if cwd is None:
        return runner.invoke(app, list(args))
    with runner.isolated_filesystem(temp_dir=cwd):
        return runner.invoke(app, list(args))


def _text(result) -> str:
    """Rich wraps long lines; compare on whitespace-normalised output."""
    return " ".join(result.output.split())


def test_command_set_is_known() -> None:
    names = {c.name or c.callback.__name__.replace("_", "-") for c in app.registered_commands}
    expected = set(COMMANDS) | set(V1_COMMANDS)
    assert names == expected, f"CLI command set changed: {sorted(names ^ expected)}"


@pytest.mark.parametrize("command", COMMANDS)
def test_help(command: str) -> None:
    result = _invoke(command, "--help")
    assert result.exit_code == 0, result.output
    assert "Usage" in _text(result)


# ── one real path per command ─────────────────────────────────────────────

def test_scan_missing_repo_fails(tmp_path: Path) -> None:
    # v1 (M2.3): clean reasoned error instead of the old ValueError traceback (audit #84).
    result = _invoke("--json", "-C", str(tmp_path / "nope"), "scan")
    assert result.exit_code == 5
    assert '"project_not_found"' in result.output


@pytest.mark.parametrize("command", REMOVED)
def test_removed_commands_are_gone(command: str) -> None:
    # M2.7: chat → ask, ingest → push, export-graph → export --format json, verify-drift → drift,
    # detect-screens → scan --only screens, test-llm → doctor --llm, view → serve (design/cli-v1.md §6)
    result = _invoke(command, "--help")
    assert result.exit_code == 2 and "No such command" in _text(result)


@pytest.mark.parametrize("args", [["ask", "orders"], ["impact", "entity:Order"]])
def test_query_commands_without_scan_fail_with_not_indexed(tmp_path: Path, args) -> None:
    result = _invoke("--json", "-C", str(tmp_path), *args)
    assert result.exit_code == 5 and '"not_indexed"' in result.output
