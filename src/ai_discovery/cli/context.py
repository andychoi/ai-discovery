"""Global CLI state and flags (roadmap item M2.1, design/cli-v1.md §2).

Global flags are declared once on the root callback. ``hoist_global_flags``
lets them appear anywhere on the command line (``discover scan -v --json``)
by moving them in front of the sub-command before Click parses argv.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import typer

PROGRESS_MODES = ("rich", "json", "none")

# Global flags: name -> takes a value?
_GLOBAL_VALUE_FLAGS = {"-C", "--repo", "--state-dir", "--progress"}
_GLOBAL_BOOL_FLAGS = {"--json", "-v", "--verbose", "-q", "--quiet", "--no-color", "--version"}


@dataclass
class Globals:
    repo: Path
    state_dir: Optional[Path] = None
    json: bool = False
    progress: str = "rich"
    verbosity: int = 0
    quiet: bool = False
    no_color: bool = False

    def layout(self):
        """v1 ProjectLayout for ``--repo`` (walks up to an existing ``.discovery/``)."""
        from ..core.paths import ProjectLayout, find_project

        root = find_project(self.repo) or self.repo
        return ProjectLayout.for_repo(root, state_dir=self.state_dir)


def get_globals(ctx: typer.Context | None) -> Globals:
    """Globals for the current invocation (defaults when called outside the CLI)."""
    while ctx is not None:
        if isinstance(ctx.obj, Globals):
            return ctx.obj
        ctx = ctx.parent
    return Globals(repo=Path.cwd())


def _is_short_cluster(token: str, letter: str) -> bool:
    """True for ``-v``/``-vv``/``-vvv`` style clusters of a single letter."""
    return token.startswith("-") and not token.startswith("--") and len(token) > 1 \
        and set(token[1:]) == {letter}


def hoist_global_flags(argv: list[str]) -> list[str]:
    """Move global flags (and their values) in front of the sub-command."""
    front: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            rest.extend(argv[i:])
            break
        name = tok.split("=", 1)[0]
        is_global = (name in _GLOBAL_VALUE_FLAGS or name in _GLOBAL_BOOL_FLAGS
                     or _is_short_cluster(tok, "v"))
        if is_global:
            front.append(tok)
            if name in _GLOBAL_VALUE_FLAGS and "=" not in tok and i + 1 < len(argv):
                front.append(argv[i + 1])
                i += 1
        else:
            rest.append(tok)
        i += 1
    return front + rest


def apply_no_color(no_color: bool) -> bool:
    """Honour --no-color and the NO_COLOR convention; returns the effective value."""
    effective = no_color or bool(os.environ.get("NO_COLOR"))
    if effective:
        os.environ["NO_COLOR"] = "1"
    return effective
