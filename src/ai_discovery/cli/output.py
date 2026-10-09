"""Result/error emission and progress events for v1 commands (roadmap item M2.1).

* ``--json``: the command's result is one JSON document on stdout
  (``{"ok": true, ...}`` or ``{"ok": false, "error": {...}}``); human text and
  logs go to stderr.
* ``--progress json``: NDJSON events ``{"event": "stage|warn|error|done", ...}``
  on stdout — or on stderr when ``--json`` also claims stdout.
"""

from __future__ import annotations

import functools
import json
import sys
import time
from typing import Any, Callable

import typer
from rich.console import Console
from rich.markup import escape

from ..core.errors import DiscoveryError, as_discovery_error
from .context import Globals, get_globals


def stderr_console(g: Globals) -> Console:
    return Console(stderr=True, no_color=g.no_color)


def emit(g: Globals, result: dict[str, Any], human: Callable[[Console], None] | str | None = None) -> None:
    """Print a command result in the mode the user asked for."""
    if g.json:
        sys.stdout.write(json.dumps({"ok": True, **result}, default=str) + "\n")
        return
    console = Console(no_color=g.no_color)
    if callable(human):
        human(console)
    elif isinstance(human, str):
        console.print(human)


def fail(g: Globals, err: DiscoveryError) -> None:
    """Report *err* and exit with its code."""
    if g.json:
        sys.stdout.write(json.dumps({"ok": False, "error": err.to_dict()}) + "\n")
    else:
        console = stderr_console(g)
        console.print(f"[red]error[/] {escape('[' + err.reason + ']')}: {escape(err.message)}", highlight=False)
        if err.hint:
            console.print(f"  hint: {escape(err.hint)}", highlight=False)
    raise typer.Exit(code=err.exit_code)


def handle_errors(fn: Callable) -> Callable:
    """Decorator for v1 commands: map known exceptions to reasoned errors.

    The wrapped command must take ``ctx: typer.Context`` as its first parameter.
    """
    @functools.wraps(fn)
    def wrapper(ctx: typer.Context, *args, **kwargs):
        try:
            return fn(ctx, *args, **kwargs)
        except typer.Exit:
            raise
        except Exception as exc:  # noqa: BLE001 — mapped below, else re-raised
            mapped = as_discovery_error(exc)
            if mapped is None:
                raise
            fail(get_globals(ctx), mapped)
    return wrapper


class ProgressEmitter:
    """Stage/warn/error/done events in rich, json (NDJSON) or none mode."""

    def __init__(self, g: Globals):
        # -q silences human-readable progress; NDJSON stays (it is machine output).
        self.mode = "none" if (g.quiet and g.progress == "rich") else g.progress
        self._out = sys.stderr if g.json else sys.stdout
        self._console = stderr_console(g)
        self._t0 = time.monotonic()

    def _event(self, event: str, **fields: Any) -> None:
        if self.mode == "json":
            payload = {"event": event, "t": round(time.monotonic() - self._t0, 3), **fields}
            self._out.write(json.dumps(payload, default=str) + "\n")
            self._out.flush()
        elif self.mode == "rich":
            text = fields.get("message") or fields.get("stage") or ""
            style = {"warn": "yellow", "error": "red", "done": "green"}.get(event, "cyan")
            self._console.print(f"[{style}]{event}[/] {text}", highlight=False)

    def stage(self, stage: str, status: str = "start", **fields: Any) -> None:
        self._event("stage", stage=stage, status=status, **fields)

    def warn(self, message: str, **fields: Any) -> None:
        self._event("warn", message=message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._event("error", message=message, **fields)

    def done(self, outcome: str = "ok", **fields: Any) -> None:
        self._event("done", outcome=outcome, **fields)
