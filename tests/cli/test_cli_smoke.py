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

COMMANDS = [
    "chat", "ingest", "query", "impact", "federate", "view",
    "test-llm", "ingest-docs", "export-graph", "detect-screens", "verify-drift",
]
V1_COMMANDS = ["init", "scan", "status", "doctor", "serve"]  # v1 commands added so far (M4.5); legacy ones go in M2.7

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


def test_chat_without_scan_fails(tmp_path: Path) -> None:
    result = _invoke("chat", "-p", "nope", "-o", str(tmp_path / "o"))
    assert result.exit_code == 1
    assert "discover scan" in _text(result)


def test_ingest_rejects_unknown_target(tmp_path: Path) -> None:
    result = _invoke("ingest", "-p", "nope", "-o", str(tmp_path / "o"), "--target", "bogus")
    assert result.exit_code == 1
    assert "dochub" in _text(result) and "gitea" in _text(result)


def test_query_runs_sql(tmp_path: Path) -> None:
    db = tmp_path / "q.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()
    result = _invoke("query", "SELECT a FROM t", "--db", str(db))
    assert result.exit_code == 0, result.output
    assert "42" in _text(result)


def test_impact_without_scan_fails(tmp_path: Path) -> None:
    result = _invoke("impact", "Order", "-p", "nope", "-o", str(tmp_path / "o"))
    assert result.exit_code == 1
    assert "discover scan" in _text(result)


def test_federate_rejects_missing_dirs(tmp_path: Path) -> None:
    result = _invoke("federate", str(tmp_path / "a"), str(tmp_path / "b"), "-o", str(tmp_path / "fed"))
    assert result.exit_code == 1
    assert "Not a directory" in _text(result)


def test_view_without_scan_fails(tmp_path: Path) -> None:
    result = _invoke("view", "-p", "nope", "-o", str(tmp_path / "o"), "--no-open")
    assert result.exit_code == 1
    assert "discover scan" in _text(result)


def test_test_llm_with_all_probes_skipped() -> None:
    result = _invoke("test-llm", "--provider", "ollama", "--skip-gen", "--skip-embedding")
    assert result.exit_code == 0, result.output


def test_ingest_docs_dry_run(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text("# Doc\n\nBody.\n", encoding="utf-8")
    result = _invoke("ingest-docs", str(tmp_path), "-p", "x", "--dry-run")
    assert result.exit_code == 0, result.output


def test_export_graph_without_scan_fails(tmp_path: Path) -> None:
    result = _invoke("export-graph", "-p", "nope", "-o", str(tmp_path / "o"))
    assert result.exit_code == 1
    assert "discover scan" in _text(result)


def test_detect_screens_on_fixture(tmp_path: Path) -> None:
    out = tmp_path / "ds"
    result = _invoke("detect-screens", str(SPRING_APP), "-o", str(out))
    assert result.exit_code == 0, result.output
    assert (out / "screen_map.yaml").exists() and (out / "menu_tree.json").exists()


def test_verify_drift_missing_spec_dir_fails(tmp_path: Path) -> None:
    result = _invoke("verify-drift", str(SPRING_APP), "-s", str(tmp_path / "nope"))
    assert result.exit_code != 0
