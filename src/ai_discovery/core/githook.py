"""Post-commit freshness hook (roadmap item M2.8).

``discover init --git-hook`` adds a marker-delimited block to ``<hooks>/post-commit`` (created if missing,
chained after an existing shell hook otherwise). After each commit the block starts a **detached**, keyless,
incremental ``discover scan --from-hook`` and returns at once — the commit is never blocked or failed:

- the repository root is resolved at run time (``git rev-parse --show-toplevel``), so linked worktrees
  re-index themselves; a worktree without a state dir is skipped (it was never set up);
- nothing runs while a rebase, merge, cherry-pick or revert is in progress (each step commits);
- the scan skips quietly when another scan holds the run lock, and appends one line per run to
  ``<state>/logs/hook.log``;
- prose runs only if the project's config says ``prose: true`` — and then still through the cost gate,
  non-interactively (under the ask threshold it runs; above it the hook logs ``cost_gate_no_tty``).

``--no-git-hook`` removes exactly the block (and the file, when the block was all it held).
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import DiscoveryError

HOOK_NAME = "post-commit"
HOOK_VERSION = "1"
BEGIN = "# >>> ai-discovery freshness hook (discover init --git-hook) >>>"
END = "# <<< ai-discovery freshness hook <<<"
_VERSION_LINE = re.compile(r"^# ai-discovery-hook-version: (\S+)$", re.M)
_COMMAND_LINE = re.compile(r"^# ai-discovery-hook-command: (.*)$", re.M)
_SHELLS = re.compile(r"^#!\s*(/usr/bin/env\s+)?(/bin/|/usr/bin/|/usr/local/bin/)?(sh|bash|dash|zsh|ksh)\b")


@dataclass
class HookStatus:
    status: str          # installed | stale | missing | not_git
    path: Optional[Path]
    detail: str


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def hook_path(repo: Path) -> Optional[Path]:
    """``<hooks dir>/post-commit`` as git resolves it (``core.hooksPath``, worktrees), or None outside git."""
    raw = _git(repo, "rev-parse", "--git-path", f"hooks/{HOOK_NAME}")
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else (Path(repo) / p).resolve()


def discover_command() -> list[str]:
    """How the hook starts this installation's CLI (absolute paths: hooks run with a minimal PATH)."""
    script = Path(sys.executable).with_name("discover")
    if script.exists():
        return [str(script)]
    return [sys.executable, "-m", "ai_discovery"]


def block(command: list[str], state_dir: Optional[Path] = None) -> str:
    """The shell block. *state_dir* is embedded only when it is not the default ``<root>/.discovery``."""
    cmd = " ".join(shlex.quote(c) for c in command)
    state = shlex.quote(str(state_dir)) if state_dir else '"$_ad_root/.discovery"'
    state_flag = f" --state-dir {shlex.quote(str(state_dir))}" if state_dir else ""
    return "\n".join([
        BEGIN,
        f"# ai-discovery-hook-version: {HOOK_VERSION}",
        "# Re-indexes the repository in the background after each commit; never blocks or fails the commit.",
        f"# ai-discovery-hook-command: {cmd}",
        "_ad_gd=$(git rev-parse --git-dir 2>/dev/null) || _ad_gd=",
        'if [ -n "$_ad_gd" ] && [ ! -d "$_ad_gd/rebase-merge" ] && [ ! -d "$_ad_gd/rebase-apply" ] \\',
        '   && [ ! -f "$_ad_gd/MERGE_HEAD" ] && [ ! -f "$_ad_gd/CHERRY_PICK_HEAD" ] && [ ! -f "$_ad_gd/REVERT_HEAD" ]; then',
        "  _ad_root=$(git rev-parse --show-toplevel 2>/dev/null) || _ad_root=",
        f"  _ad_state={state}",
        '  if [ -n "$_ad_root" ] && [ -d "$_ad_state" ]; then',
        '    mkdir -p "$_ad_state/logs" 2>/dev/null',
        # git exports GIT_DIR/GIT_INDEX_FILE to hooks; the scan's own git calls must not inherit them
        f'    ( unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX; {cmd} --json --progress none -C "$_ad_root"{state_flag} scan --from-hook '
        '>>"$_ad_state/logs/hook.log" 2>&1 & ) </dev/null >/dev/null 2>&1',
        "  fi",
        "fi",
        "true",
        END,
    ]) + "\n"


def _split(text: str) -> tuple[str, Optional[str], str]:
    """(before, block-or-None, after) around the marked block."""
    start = text.find(BEGIN)
    if start < 0:
        return text, None, ""
    end = text.find(END, start)
    if end < 0:
        return text, None, ""
    end += len(END)
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:start], text[start:end], text[end:]


def install(repo: Path, *, state_dir: Optional[Path] = None, command: Optional[list[str]] = None) -> tuple[Path, str]:
    """Add (or refresh) the block. Returns (hook path, 'created' | 'appended' | 'updated')."""
    path = hook_path(repo)
    if path is None:
        raise DiscoveryError(f"{repo} is not a git repository; the freshness hook needs git", "project_not_found")
    new_block = block(command or discover_command(), state_dir)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + new_block, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path, "created"
    text = path.read_text(encoding="utf-8", errors="replace")
    before, old, after = _split(text)
    if old is not None:
        path.write_text(before + new_block + after, encoding="utf-8")
        return path, "updated"
    first = text.splitlines()[0] if text.strip() else ""
    if first.startswith("#!") and not _SHELLS.match(first):
        raise DiscoveryError(f"{path} is not a shell script ({first}); the hook cannot be chained into it",
                             "contradictory_flags",
                             hint=f"call `{' '.join(command or discover_command())} -C <repo> scan --from-hook &` "
                                  "from that hook yourself")
    lines = text.rstrip("\n").split("\n")
    # an existing hook that ends with `exit …` would never reach an appended block: insert before it
    tail = len(lines)
    while tail > 0 and not lines[tail - 1].strip():
        tail -= 1
    if tail and re.match(r"^\s*exit\b", lines[tail - 1]):
        head = "\n".join(lines[:tail - 1])
        updated = (head + "\n" if head else "") + new_block + "\n".join(lines[tail - 1:]) + "\n"
    else:
        updated = text.rstrip("\n") + "\n\n" + new_block
    path.write_text(updated, encoding="utf-8")
    return path, "appended"


def uninstall(repo: Path) -> tuple[Optional[Path], str]:
    """Remove exactly the block. Returns (hook path, 'removed' | 'deleted' | 'absent')."""
    path = hook_path(repo)
    if path is None or not path.exists():
        return path, "absent"
    text = path.read_text(encoding="utf-8", errors="replace")
    before, old, after = _split(text)
    if old is None:
        return path, "absent"
    rest = before.rstrip("\n") + ("\n" + after if after.strip() else "")
    rest = rest.rstrip("\n") + "\n" if rest.strip() else ""
    if not rest.strip() or rest.strip() == "#!/bin/sh":
        path.unlink()
        return path, "deleted"
    path.write_text(rest, encoding="utf-8")
    return path, "removed"


def status(repo: Path) -> HookStatus:
    path = hook_path(repo)
    if path is None:
        return HookStatus("not_git", None, "not a git repository")
    if not path.exists():
        return HookStatus("missing", path, "no post-commit hook")
    _, old, _ = _split(path.read_text(encoding="utf-8", errors="replace"))
    if old is None:
        return HookStatus("missing", path, "post-commit hook exists without the ai-discovery block")
    version = _VERSION_LINE.search(old)
    if not version or version.group(1) != HOOK_VERSION:
        return HookStatus("stale", path, "hook block is from an older version")
    cmd = _COMMAND_LINE.search(old)
    exe = shlex.split(cmd.group(1))[0] if cmd and cmd.group(1).strip() else ""
    if not exe or not Path(exe).exists():
        return HookStatus("stale", path, f"hook starts {exe or '?'}, which no longer exists")
    if not os.access(path, os.X_OK):
        return HookStatus("stale", path, "hook file is not executable, so git does not run it")
    return HookStatus("installed", path, f"re-indexes after each commit via {exe}")
