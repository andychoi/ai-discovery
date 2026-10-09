"""v1 CLI skeleton (roadmap item M2.1)."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from ai_discovery import __version__
from ai_discovery.cli import app
from ai_discovery.cli.context import Globals, get_globals, hoist_global_flags
from ai_discovery.cli.output import ProgressEmitter, emit, handle_errors
from ai_discovery.core.errors import REASONS, DiscoveryError, as_discovery_error
from ai_discovery.core.logging import configure_logging, level_for

runner = CliRunner()
HEAVY = ("tree_sitter", "boto3", "sqlite_vec", "ai_discovery.pipeline")


# ── fast --help ───────────────────────────────────────────────────────────

def test_help_imports_nothing_heavy() -> None:
    code = (
        "import sys\n"
        "from ai_discovery.cli import main\n"
        "try:\n    main(['--help'])\nexcept SystemExit:\n    pass\n"
        f"print([m for m in {HEAVY!r} if m in sys.modules])\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[-1] == "[]", out.stdout


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and result.output.strip() == f"discover {__version__}"


def test_no_subcommand_prints_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0 and "Usage" in result.output


# ── global flags ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("argv,expected", [
    (["scan", "repo", "-v", "--json"], ["-v", "--json", "scan", "repo"]),
    (["query", "x", "--repo", "/r", "-p", "q"], ["--repo", "/r", "query", "x", "-p", "q"]),
    (["scan", "--state-dir=/s", "-vv"], ["--state-dir=/s", "-vv", "scan"]),
    (["--json", "status"], ["--json", "status"]),
    (["scan", "--", "-v"], ["scan", "--", "-v"]),
    (["scan", "-p", "x"], ["scan", "-p", "x"]),
])
def test_hoist_global_flags(argv, expected) -> None:
    assert hoist_global_flags(argv) == expected


def _probe_app():
    """A throwaway app sharing the real root callback, with one probe command."""
    probe = typer.Typer()
    probe.callback(invoke_without_command=True)(app.registered_callback.callback)
    seen = {}

    @probe.command()
    def show(ctx: typer.Context) -> None:
        seen["g"] = get_globals(ctx)

    return probe, seen


def test_globals_reach_commands(tmp_path: Path) -> None:
    probe, seen = _probe_app()
    args = hoist_global_flags(["show", "-C", str(tmp_path), "--state-dir", str(tmp_path / "s"),
                               "--json", "--progress", "json", "-vv", "--no-color"])
    result = runner.invoke(probe, args)
    assert result.exit_code == 0, result.output
    g = seen["g"]
    assert (g.repo, g.state_dir, g.json, g.progress, g.verbosity, g.no_color) == \
        (tmp_path.resolve(), tmp_path / "s", True, "json", 2, True)


def test_layout_from_globals(tmp_path: Path) -> None:
    g = Globals(repo=tmp_path, state_dir=tmp_path / "state")
    assert g.layout().state_dir == (tmp_path / "state").resolve()


@pytest.mark.parametrize("args,reason", [
    (["--progress", "fancy", "show"], "contradictory_flags"),
    (["-v", "-q", "show"], "contradictory_flags"),
])
def test_contradictory_global_flags_exit_2(args, reason) -> None:
    probe, _ = _probe_app()
    result = runner.invoke(probe, ["--json", *args])
    assert result.exit_code == 2
    assert json.loads(result.output.splitlines()[-1])["error"]["reason"] == reason


def test_legacy_commands_accept_global_flags(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "q.db"
    sqlite3.connect(db).close()
    args = hoist_global_flags(["query", "select 1", "--db", str(db), "-v", "--no-color", "--progress", "none"])
    assert runner.invoke(app, args).exit_code == 0


# ── errors and exit codes ─────────────────────────────────────────────────

def test_reason_registry_matches_design_exit_codes() -> None:
    assert REASONS["lock_held"] == 4 and REASONS["budget_exceeded"] == 3
    assert REASONS["not_indexed"] == 5 and REASONS["drift_detected"] == 6
    assert REASONS["config_invalid_key"] == 2 and REASONS["failure"] == 1
    with pytest.raises(ValueError, match="unknown reason slug"):
        DiscoveryError("x", "made_up_reason")


def test_known_exceptions_map_to_reasons(tmp_path: Path) -> None:
    from ai_discovery.config.loader import ConfigError
    from ai_discovery.core.lock import LockHeld

    assert as_discovery_error(ConfigError("bad")).exit_code == 2
    assert as_discovery_error(LockHeld(tmp_path / "lock", {"pid": 1})).reason == "lock_held"
    assert as_discovery_error(KeyError("x")) is None


def _error_app(exc: Exception):
    probe = typer.Typer()
    probe.callback(invoke_without_command=True)(app.registered_callback.callback)

    @probe.command()
    @handle_errors
    def boom(ctx: typer.Context) -> None:
        raise exc

    return probe


def test_handle_errors_json_envelope_and_exit_code() -> None:
    result = runner.invoke(_error_app(DiscoveryError("no index here", "not_indexed", hint="run scan")),
                           ["--json", "boom"])
    assert result.exit_code == 5
    payload = json.loads(result.output.splitlines()[-1])
    assert payload == {"ok": False, "error": {"reason": "not_indexed", "message": "no index here",
                                               "hint": "run scan", "exit_code": 5}}


def test_handle_errors_human_message() -> None:
    result = runner.invoke(_error_app(DiscoveryError("no index here", "not_indexed", hint="run scan")),
                           ["--no-color", "boom"])
    assert result.exit_code == 5
    assert "[not_indexed]" in result.output and "hint: run scan" in result.output


def test_unmapped_exceptions_propagate() -> None:
    result = runner.invoke(_error_app(KeyError("bug")), ["boom"])
    assert isinstance(result.exception, KeyError)


def test_emit_json(capsys) -> None:
    emit(Globals(repo=Path("."), json=True), {"count": 3}, human="ignored")
    assert json.loads(capsys.readouterr().out) == {"ok": True, "count": 3}


# ── progress events ───────────────────────────────────────────────────────

def test_progress_json_is_ndjson(capsys) -> None:
    p = ProgressEmitter(Globals(repo=Path("."), progress="json"))
    p.stage("parse", "start", files=3)
    p.warn("12 files decoded as cp949")
    p.done("ok")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [ln["event"] for ln in lines] == ["stage", "warn", "done"]
    assert lines[0]["stage"] == "parse" and lines[0]["files"] == 3


def test_progress_json_goes_to_stderr_when_json_result(capsys) -> None:
    ProgressEmitter(Globals(repo=Path("."), progress="json", json=True)).done()
    out, err = capsys.readouterr()
    assert out == "" and json.loads(err)["event"] == "done"


def test_progress_none_is_silent(capsys) -> None:
    ProgressEmitter(Globals(repo=Path("."), progress="none")).stage("parse")
    assert capsys.readouterr() == ("", "")


# ── logging ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("v,q,level", [(0, False, logging.WARNING), (1, False, logging.INFO),
                                       (2, False, logging.DEBUG), (0, True, logging.ERROR)])
def test_log_levels(v, q, level) -> None:
    assert level_for(v, q) == level


def test_configure_logging_is_idempotent_and_captures_warnings(tmp_path: Path) -> None:
    configure_logging(1)
    configure_logging(2, log_file=tmp_path / "run.log")
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_ai_discovery_cli", False)]
    assert len(ours) == 2  # stderr + file, not duplicated
    # pytest records warnings itself, so call the installed hook directly.
    warnings.showwarning("routed to logging", UserWarning, "f.py", 1)
    for h in ours:
        h.flush()
    assert "routed to logging" in (tmp_path / "run.log").read_text()
    configure_logging(0)
