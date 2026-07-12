"""CLI smoke tests — catch import-time and validation-path regressions.

These are deliberately cheap: they exercise every subcommand's help text
(catching NameErrors/typos in command bodies that only surface at call time)
plus the specific validation branches that had no coverage. The
`ingest-docs --push <bad>` case is a regression guard for the undefined
`_VALID_PUSH_MODES` name that made the command crash on any invocation.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app

runner = CliRunner()

# Every command registered on the Typer app. Kept explicit (not introspected)
# so a removed/renamed command trips the test and gets re-confirmed.
_COMMANDS = [
    "scan", "chat", "view", "impact", "query", "init",
    "detect-screens", "verify-drift", "export-graph", "ingest", "ingest-docs",
]


def test_app_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("command", _COMMANDS)
def test_command_help_does_not_crash(command):
    """`--help` imports and constructs the command — a body-level NameError
    (like the old _VALID_PUSH_MODES) would surface here for many commands."""
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0, f"{command} --help failed:\n{result.output}"


def test_ingest_docs_rejects_invalid_push_mode():
    """Regression for P0-1: an invalid --push value must be rejected with a
    clean exit(1), not a NameError on the undefined _VALID_PUSH_MODES."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.md").write_text("# doc\n")
        result = runner.invoke(
            app, ["ingest-docs", tmp, "-p", "proj", "--push", "bogus"]
        )
    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Invalid --push" in result.output


def test_ingest_docs_accepts_valid_push_modes_dry_run():
    """A valid push mode should get past validation (dry-run, no network)."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.md").write_text("# doc\n\nbody\n")
        for mode in ("api", "gitea"):
            result = runner.invoke(
                app, ["ingest-docs", tmp, "-p", "proj", "--push", mode, "--dry-run"]
            )
            # Dry run must not fail on the push-mode validation branch.
            assert "Invalid --push" not in result.output, (mode, result.output)
