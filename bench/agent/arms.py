"""Arms of the agent-loop eval (roadmap item M5.7, design/benchmark-protocol.md §6).

Every arm gets the **byte-identical task prompt** (plus the same fixed suffix). Arms
differ only in what the agent is given: tools, skills, an instructions block, and —
for the autonomous arm — the operator's pre-approval to implement and verify
without stopping. The model is fixed by the pre-registration.

Primary analysis: ``bare`` vs ``discover``. Secondary: ``discover-auto``.
Exploratory (never decide GA): ``codex-bare``, ``codex-discover``, ``repowise``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
PLUGIN = ROOT / "plugin"

PROMPT_SUFFIX = ("\n\nImplement this change in the repository in the current directory. Work without asking "
                 "questions. When you are done, reply with a short summary of what you changed.")
AUTONOMY_NOTE = ("Operator pre-approval: you may implement without waiting for a human. Plan the change with the "
                 "discover-change-plan skill, implement it within the planned files, then check it with the "
                 "discover-verify-change skill and run the build and tests it lists before you finish.")
DISCOVER_TOOLS = "default,+get_verification_plan,+get_symbol_source"


@dataclass(frozen=True)
class Arm:
    name: str
    agent: str                 # claude | codex
    discover: bool             # build the index + MCP server + skills + instructions block
    role: str                  # primary | secondary | exploratory
    autonomy: bool = False
    repowise: bool = False

    def available(self) -> tuple[bool, str]:
        exe = "claude" if self.agent == "claude" else "codex"
        if shutil.which(exe) is None:
            return False, f"{exe} CLI not found"
        if self.repowise and not os.environ.get("BENCH_REPOWISE_MCP"):
            return False, "set BENCH_REPOWISE_MCP to the repowise MCP server entry (JSON) to enable this arm"
        return True, ""


ARMS: dict[str, Arm] = {a.name: a for a in (
    Arm("bare", "claude", discover=False, role="primary"),
    Arm("discover", "claude", discover=True, role="primary"),
    Arm("discover-auto", "claude", discover=True, role="secondary", autonomy=True),
    Arm("codex-bare", "codex", discover=False, role="exploratory"),
    Arm("codex-discover", "codex", discover=True, role="exploratory"),
    Arm("repowise", "claude", discover=False, role="exploratory", repowise=True),
)}


def discover_exe() -> str:
    exe = Path(sys.executable).with_name("discover")
    return str(exe) if exe.exists() else (shutil.which("discover") or "discover")


def mcp_servers(arm: Arm, ws: Path) -> dict:
    servers: dict = {}
    if arm.discover:
        servers["ai-discovery"] = {"command": discover_exe(),
                                   "args": ["-C", str(ws), "mcp", "serve", "--tools", DISCOVER_TOOLS]}
    if arm.repowise:
        servers["repowise"] = json.loads(os.environ["BENCH_REPOWISE_MCP"])
    return servers


def command(arm: Arm, *, prompt: str, ws: Path, model: str, budget_usd: float, scratch: Path) -> tuple[list[str], dict]:
    """(argv, extra env) for one run. ``scratch`` holds harness-owned config outside the workspace."""
    env: dict[str, str] = {}
    text = prompt + PROMPT_SUFFIX
    scratch.mkdir(parents=True, exist_ok=True)
    if arm.agent == "claude":
        cfg = scratch / "mcp.json"
        cfg.write_text(json.dumps({"mcpServers": mcp_servers(arm, ws)}))
        argv = ["claude", "-p", text, "--model", model, "--output-format", "stream-json", "--verbose",
                "--no-session-persistence", "--setting-sources", "project", "--strict-mcp-config",
                "--mcp-config", str(cfg), "--permission-mode", "bypassPermissions",
                "--max-budget-usd", f"{budget_usd:.2f}"]
        if arm.discover:
            argv += ["--plugin-dir", str(PLUGIN)]
        if arm.autonomy:
            argv += ["--append-system-prompt", AUTONOMY_NOTE]
        return argv, env
    # codex: MCP servers via a private CODEX_HOME (user-scope config.toml), never the operator's own config
    home = scratch / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    blocks = []
    for name, srv in mcp_servers(arm, ws).items():
        blocks.append(f"[mcp_servers.{json.dumps(name)}]\ncommand = {json.dumps(srv['command'])}\n"
                      "args = [" + ", ".join(json.dumps(a) for a in srv.get("args", [])) + "]\n")
    (home / "config.toml").write_text("\n".join(blocks))
    env["CODEX_HOME"] = str(home)
    argv = ["codex", "exec", "--json", "-m", model, "-C", str(ws), "--sandbox", "workspace-write",
            "--skip-git-repo-check", text]
    return argv, env


def prepare(arm: Arm, ws: Path, *, run: Callable[[list[str]], None]) -> dict:
    """Arm-specific setup in the workspace before the agent starts; returns timings/notes for the record."""
    notes: dict = {}
    if arm.discover:
        run([discover_exe(), "-q", "-C", str(ws), "scan"])
        from ai_discovery.mcp import instructions
        from ai_discovery.query import Project

        block = instructions.render(Project.open(ws))
        instructions.write(ws, block, "claude" if arm.agent == "claude" else "agents")
        notes["instructions_tokens"] = instructions.tokens(block)
    return notes


def model_for(arm: Arm, claude_model: str, codex_model: Optional[str]) -> Optional[str]:
    return claude_model if arm.agent == "claude" else codex_model
