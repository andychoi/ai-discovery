"""Install targets for MCP clients (roadmap item M5.14).

Formats verified against each client's documentation on 2026-10-31:

| target | scope   | file                                              | shape |
|--------|---------|---------------------------------------------------|-------|
| claude | project | ``<repo>/.mcp.json``                              | ``{"mcpServers": {name: {command, args}}}`` |
| claude | user    | ``~/.claude.json`` — managed by ``claude mcp add``; we print the command instead of editing it |
| cursor | project | ``<repo>/.cursor/mcp.json``                       | ``mcpServers`` + ``"type": "stdio"``; ``${workspaceFolder}`` expands |
| cursor | user    | ``~/.cursor/mcp.json``                            | same, absolute repo path |
| vscode | project | ``<repo>/.vscode/mcp.json``                       | ``{"servers": {name: {"type": "stdio", …}}}``; ``${workspaceFolder}`` expands (VS Code labels this file "compatibility") |
| vscode | user    | ``$COPILOT_HOME/mcp-config.json`` (``~/.copilot``)  | portable ``mcpServers`` |
| codex  | project | ``<repo>/.codex/config.toml`` (trusted projects only) | ``[mcp_servers.<name>]`` command/args |
| codex  | user    | ``$CODEX_HOME/config.toml`` (``~/.codex``)        | same, plus ``cwd`` |

Project-scope entries are portable (no absolute paths) so they can be committed;
user-scope entries name the repo absolutely and use a per-repo server name.
TOML is edited only inside a marker-delimited block, so uninstall removes
exactly what install wrote. A second server that launches ``discover mcp serve``
(under another name, or via the Claude Code plugin) is refused, not duplicated.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SERVER_NAME = "ai-discovery"
PLUGIN_NAME = "ai-discovery"
TARGETS = ("claude", "codex", "cursor", "vscode")
SCOPES = ("project", "user")
_BEGIN = "# >>> ai-discovery (managed by `discover mcp install`; edit outside this block) >>>"
_END = "# <<< ai-discovery <<<"


class TargetError(ValueError):
    """Install refused; ``hint`` says what to do instead."""

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class Placement:
    target: str
    scope: str
    path: Path
    name: str            # server key in the client's config
    fmt: str             # "mcpServers" | "servers" | "toml"
    repo_arg: Optional[str]   # value for -C (None: rely on the client's working directory)
    cwd: Optional[str] = None
    stdio_type: bool = False


# ── placement ─────────────────────────────────────────────────────────────

def _home(env: str, default: str) -> Path:
    val = os.environ.get(env)
    return Path(val).expanduser() if val else Path.home() / default


def user_server_name(repo: Path) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", repo.resolve().name).strip("-") or "repo"
    digest = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:6]
    return f"{SERVER_NAME}-{base}-{digest}"


def placement(target: str, scope: str, repo: Path) -> Placement:
    if target not in TARGETS:
        raise TargetError(f"unknown target {target!r}", hint=f"use one of {', '.join(TARGETS)} or all")
    if scope not in SCOPES:
        raise TargetError(f"unknown scope {scope!r}", hint="use project or user")
    repo = repo.resolve()
    if scope == "project":
        return {
            "claude": Placement("claude", scope, repo / ".mcp.json", SERVER_NAME, "mcpServers", None),
            "cursor": Placement("cursor", scope, repo / ".cursor" / "mcp.json", SERVER_NAME, "mcpServers",
                                "${workspaceFolder}", stdio_type=True),
            "vscode": Placement("vscode", scope, repo / ".vscode" / "mcp.json", SERVER_NAME, "servers",
                                "${workspaceFolder}", stdio_type=True),
            "codex": Placement("codex", scope, repo / ".codex" / "config.toml", SERVER_NAME, "toml", None),
        }[target]
    name, abs_repo = user_server_name(repo), str(repo)
    if target == "claude":
        raise TargetError(
            "Claude Code keeps user-scoped servers in ~/.claude.json, which `discover` does not edit",
            hint=f"run: claude mcp add --scope user {name} -- discover -C {abs_repo} mcp serve")
    return {
        "cursor": Placement("cursor", scope, Path.home() / ".cursor" / "mcp.json", name, "mcpServers", abs_repo,
                            stdio_type=True),
        "vscode": Placement("vscode", scope, _home("COPILOT_HOME", ".copilot") / "mcp-config.json", name,
                            "mcpServers", abs_repo),
        "codex": Placement("codex", scope, _home("CODEX_HOME", ".codex") / "config.toml", name, "toml", abs_repo,
                           cwd=abs_repo),
    }[target]


def entry(p: Placement, tools: Optional[str] = None, command: str = "discover") -> dict:
    args = (["-C", p.repo_arg] if p.repo_arg else []) + ["mcp", "serve"] + (["--tools", tools] if tools else [])
    out: dict = {"type": "stdio"} if p.stdio_type else {}
    out.update(command=command, args=args)
    if p.cwd:
        out["cwd"] = p.cwd
    return out


# ── duplicate detection ───────────────────────────────────────────────────

def _launches_discovery(server: dict) -> bool:
    cmd = Path(str(server.get("command") or "")).name.lower()
    args = [str(a) for a in server.get("args") or []]
    return (cmd in ("discover", "discover.exe") and "mcp" in args) or \
        (cmd.startswith("python") and "ai_discovery.cli" in args and "mcp" in args)


def _plugin_enabled(repo: Path) -> Optional[Path]:
    for path in (repo / ".claude" / "settings.json", repo / ".claude" / "settings.local.json",
                 Path.home() / ".claude" / "settings.json"):
        try:
            plugins = json.loads(path.read_text(encoding="utf-8")).get("enabledPlugins") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if any(str(k).split("@", 1)[0] == PLUGIN_NAME and v for k, v in dict(plugins).items()):
            return path
    return None


def _json_servers(p: Placement) -> tuple[dict, dict]:
    """(whole file, servers container) — empty dicts when the file is absent."""
    if not p.path.exists():
        return {}, {}
    try:
        data = json.loads(p.path.read_text(encoding="utf-8") or "{}")
    except ValueError as exc:
        raise TargetError(f"{p.path} is not valid JSON", hint="fix or remove it first") from exc
    if not isinstance(data, dict) or not isinstance(data.get(p.fmt, {}), dict):
        raise TargetError(f"{p.path}: expected an object with a `{p.fmt}` object")
    return data, data.get(p.fmt, {})


def _toml_servers(text: str) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10: best-effort regex for the duplicate check
        found = {}
        for m in re.finditer(r"^\[mcp_servers\.\"?([^\]\"]+)\"?\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S):
            cmd = re.search(r'^command\s*=\s*"([^"]*)"', m.group(2), re.M)
            args = re.search(r"^args\s*=\s*\[(.*?)\]", m.group(2), re.M | re.S)
            found[m.group(1)] = {"command": cmd.group(1) if cmd else "",
                                 "args": re.findall(r'"([^"]*)"', args.group(1)) if args else []}
        return found
    try:
        return dict(tomllib.loads(text).get("mcp_servers") or {})
    except tomllib.TOMLDecodeError as exc:
        raise TargetError(f"invalid TOML: {exc}", hint="fix the file first") from exc


def _strip_block(text: str) -> tuple[str, bool]:
    pattern = re.compile(rf"\n?{re.escape(_BEGIN)}\n.*?{re.escape(_END)}\n?", re.S)
    new, n = pattern.subn("\n", text)
    return (new.strip("\n") + "\n" if new.strip() else ""), bool(n)


def _others(p: Placement, servers: dict) -> list[str]:
    return sorted(k for k, v in servers.items() if k != p.name and isinstance(v, dict) and _launches_discovery(v))


# ── install / uninstall ───────────────────────────────────────────────────

def _toml_block(p: Placement, e: dict) -> str:
    lines = [_BEGIN, f"[mcp_servers.{json.dumps(p.name)}]", f"command = {json.dumps(e['command'])}",
             "args = [" + ", ".join(json.dumps(a) for a in e["args"]) + "]"]
    if e.get("cwd"):
        lines.append(f"cwd = {json.dumps(e['cwd'])}")
    return "\n".join(lines + [_END]) + "\n"


def install(target: str, scope: str, repo: Path, *, tools: Optional[str] = None, command: str = "discover",
            force: bool = False) -> dict:
    """Write our server for one client. Returns {target, scope, path, name, changed, notes}."""
    p = placement(target, scope, repo)
    e = entry(p, tools, command)
    notes: list[str] = []
    if target == "claude" and not force and (plugin := _plugin_enabled(repo)):
        raise TargetError(f"the {PLUGIN_NAME} Claude Code plugin is enabled ({plugin}) and already starts the server",
                          hint="skip this target, or disable the plugin first (--force wires a second server)")
    if p.fmt == "toml":
        text = p.path.read_text(encoding="utf-8") if p.path.exists() else ""
        outside, had_block = _strip_block(text)
        servers = _toml_servers(outside)
        clash = ([p.name] if p.name in servers else []) + _others(p, servers)
        if clash and not force:
            raise TargetError(f"{p.path} already defines a discovery server: {', '.join(clash)}",
                              hint="remove it, or pass --force to add ours anyway")
        block = _toml_block(p, e)
        new = (outside.rstrip("\n") + "\n\n" if outside.strip() else "") + block
        changed = new != text
        if changed:
            p.path.parent.mkdir(parents=True, exist_ok=True)
            p.path.write_text(new, encoding="utf-8")
        if scope == "project":
            notes.append("Codex reads .codex/config.toml only in trusted projects")
    else:
        data, servers = _json_servers(p)
        clash = _others(p, servers)
        if clash and not force:
            raise TargetError(f"{p.path} already has a discovery server under another name: {', '.join(clash)}",
                              hint="remove it, or pass --force to add ours anyway")
        changed = servers.get(p.name) != e
        if changed:
            data.setdefault(p.fmt, {})[p.name] = e
            p.path.parent.mkdir(parents=True, exist_ok=True)
            p.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if target == "vscode" and scope == "project":
        claude = placement("claude", "project", repo)
        if claude.path.exists() and SERVER_NAME in _json_servers(claude)[1]:
            notes.append("VS Code can also read .mcp.json (setting chat.mcp.workspaceRootConfig.enabled); "
                         "enable only one of the two to avoid a duplicate server")
    return {"target": target, "scope": scope, "path": str(p.path), "name": p.name, "changed": changed,
            "entry": e, "notes": notes}


def uninstall(target: str, scope: str, repo: Path) -> dict:
    """Remove exactly what ``install`` wrote. result: removed | file_removed | absent."""
    p = placement(target, scope, repo)
    result = "absent"
    if p.path.exists():
        if p.fmt == "toml":
            text = p.path.read_text(encoding="utf-8")
            new, had = _strip_block(text)
            if had:
                if new.strip():
                    p.path.write_text(new, encoding="utf-8")
                    result = "removed"
                else:
                    p.path.unlink()
                    result = "file_removed"
        else:
            data, servers = _json_servers(p)
            if p.name in servers:
                del servers[p.name]
                if not servers and set(data) == {p.fmt}:
                    p.path.unlink()
                    result = "file_removed"
                else:
                    p.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                    result = "removed"
        if result == "file_removed" and scope == "project" and p.path.parent != repo.resolve():
            try:
                p.path.parent.rmdir()   # only if we left it empty
            except OSError:
                pass
    return {"target": target, "scope": scope, "path": str(p.path), "name": p.name, "result": result}


def snippet(target: str, scope: str, repo: Path, *, tools: Optional[str] = None, command: str = "discover") -> dict:
    """What ``install`` would write, without writing: {path, format, text}."""
    p = placement(target, scope, repo)
    e = entry(p, tools, command)
    if p.fmt == "toml":
        text = _toml_block(p, e)
    else:
        text = json.dumps({p.fmt: {p.name: e}}, indent=2) + "\n"
    return {"target": target, "scope": scope, "path": str(p.path), "name": p.name, "format": p.fmt, "text": text}


# ── status (doctor) ───────────────────────────────────────────────────────

def installed(repo: Path) -> list[dict]:
    """Every placement that currently holds our entry, with a launchability verdict."""
    out = []
    for target in TARGETS:
        for scope in SCOPES:
            try:
                p = placement(target, scope, repo)
            except TargetError:
                continue
            if not p.path.exists():
                continue
            try:
                if p.fmt == "toml":
                    text = p.path.read_text(encoding="utf-8")
                    if _BEGIN not in text:
                        continue
                    servers = _toml_servers(text)
                else:
                    servers = _json_servers(p)[1]
            except TargetError as exc:
                out.append({"target": target, "scope": scope, "path": str(p.path), "status": "fail",
                            "detail": str(exc)})
                continue
            e = servers.get(p.name)
            if not isinstance(e, dict):
                continue
            cmd = str(e.get("command") or "")
            exe = cmd if os.path.isabs(cmd) and os.access(cmd, os.X_OK) else shutil.which(cmd)
            repo_ok = p.repo_arg is None or p.repo_arg.startswith("${") or Path(p.repo_arg).is_dir()
            status = "ok" if exe and repo_ok else "fail"
            detail = (f"{p.path}: launches {exe}" if status == "ok" else
                      f"{p.path}: command {cmd!r} is not on PATH" if not exe else f"{p.path}: repo path missing")
            out.append({"target": target, "scope": scope, "path": str(p.path), "status": status, "detail": detail,
                        "fix": None if status == "ok" else
                        f"discover mcp install --target {target} --scope {scope} --command \"$(which discover)\""})
    return out
