"""`discover init [PATH]` — set up a project (roadmap item M2.2, design/cli-v1.md §3).

1. Detects the stack (languages, framework, menu format, SAP markers) and prints it.
2. Chooses a spend mode:
   * TTY: a 3-option menu — structure only (recommended first), structure +
     prose (pick a provider; spend is always estimated before it happens),
     advanced (budget, concurrency, provider).
   * Non-TTY or stdin EOF: needs ``--yes`` or ``--prose/--no-prose``; else
     exit 2 ``init_needs_choice``.
3. Writes ``.discovery/config.yaml`` + ``.discovery/.gitignore`` and lists every
   file it wrote so it can be undone. ``--mcp`` adds a ``.mcp.json`` entry once
   the MCP server exists (M5.3); until then it is skipped with a note.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import typer

from ...core.errors import DiscoveryError
from ..context import Globals, get_globals
from ..output import emit, handle_errors

PROVIDERS = ("aigw", "omlx", "openrouter", "bedrock", "openai", "gemini", "anthropic",
             "ollama", "mlx-gemma", "mlx-qwen")
MCP_SERVER_NAME = "ai-discovery"


@dataclass
class StackReport:
    languages: dict[str, int] = field(default_factory=dict)
    framework: Optional[str] = None
    menu_format: Optional[str] = None
    menu_entries: int = 0
    sap: list[str] = field(default_factory=list)


def detect_stack(root: Path) -> StackReport:
    """Cheap, read-only detection of what scanning will find."""
    from ...framework_detector import FrameworkDetector
    from ...menu_detector import HybridMenuDetector
    from ...repo.lang_detector import detect_languages

    report = StackReport()
    report.languages = {lang: info["file_count"] for lang, info in
                        sorted(detect_languages(root).items(), key=lambda kv: -kv[1]["file_count"])}
    try:
        pattern = FrameworkDetector.detect_framework(root)
        report.framework = pattern.framework if pattern else None
    except Exception:  # noqa: BLE001 — detection is advisory
        report.framework = None
    try:
        detector = HybridMenuDetector()
        items = detector.detect(root) or []
        report.menu_format = detector.matched_format if items else None
        report.menu_entries = len(items)
    except Exception:  # noqa: BLE001
        report.menu_format = None
    if any(root.rglob(".abapgit.xml")):
        report.sap.append("abapGit")
    for manifest in root.rglob("manifest.json"):
        if "node_modules" in manifest.parts:
            continue
        try:
            if '"sap.app"' in manifest.read_text(encoding="utf-8", errors="replace"):
                report.sap.append("UI5/Fiori")
                break
        except OSError:
            continue
    return report


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask(prompt: str, default: str, choices: Optional[tuple[str, ...]] = None) -> str:
    """Prompt via typer's public API (typer vendors click); EOF/abort => non-interactive."""
    label = f"{prompt} [{'/'.join(choices)}]" if choices else prompt
    for _ in range(3):
        try:
            value = str(typer.prompt(label, default=default)).strip()
        except (EOFError, typer.Abort):
            raise _NonInteractive() from None
        if not choices or value in choices:
            return value
        typer.echo(f"  please answer one of: {', '.join(choices)}", err=True)
    raise _NonInteractive()


class _NonInteractive(Exception):
    pass


def _menu(stack: StackReport, provider: str) -> dict:
    """Interactive first-run menu → settings."""
    typer.echo("\nHow should discovery run?")
    typer.echo("  1) Structure only — no key, no spend: graph, screens, entities, tables, wiki (recommended first)")
    typer.echo("  2) Structure + prose — LLM-written narratives; the cost is estimated before any spend")
    typer.echo("  3) Advanced — provider, budget, concurrency")
    choice = _ask("Choice", "1", ("1", "2", "3"))
    settings: dict = {"prose": choice in ("2", "3"), "provider": provider}
    if choice in ("2", "3"):
        settings["provider"] = _ask("Provider", provider, PROVIDERS)
    if choice == "3":
        settings["prose"] = _ask("Write LLM prose? (y/n)", "y", ("y", "n")) == "y"
        settings["budget_limit_usd"] = float(_ask("Budget limit (USD)", "50"))
        settings["max_concurrent"] = int(_ask("Max concurrent LLM calls", "10"))
    return settings


def _render_config(settings: dict) -> str:
    from ...config.template import render_template

    text = render_template(settings["provider"], prose=settings["prose"])
    for key in ("budget_limit_usd", "max_concurrent"):
        if key in settings:
            lines = [f"{key}: {settings[key]}" if ln.startswith(f"{key}:") else ln for ln in text.splitlines()]
            text = "\n".join(lines) + "\n"
    return text


def write_mcp_entry(repo: Path) -> Path:
    """Merge an ``ai-discovery`` server into ``<repo>/.mcp.json`` (keeps other servers)."""
    path = repo / ".mcp.json"
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except ValueError as exc:
            raise DiscoveryError(f"{path} is not valid JSON; fix or remove it first", "config_invalid_value") from exc
    servers = data.setdefault("mcpServers", {})
    servers[MCP_SERVER_NAME] = {"command": "discover", "args": ["mcp", "serve"]}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _mcp_available(app: typer.Typer) -> bool:
    return any((c.name or c.callback.__name__) == "mcp" for c in app.registered_commands) or \
        any(getattr(g, "name", None) == "mcp" for g in app.registered_groups)


def register(app: typer.Typer) -> None:
    @app.command("init")
    @handle_errors
    def init(
        ctx: typer.Context,
        path: Optional[Path] = typer.Argument(None, help="Repository to set up (default: -C / current dir)."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Accept defaults (structure only unless --prose)."),
        prose: Optional[bool] = typer.Option(None, "--prose/--no-prose", help="Spend mode: add LLM prose, or structure only."),
        provider: str = typer.Option("aigw", "--provider", help=f"LLM provider ({' | '.join(PROVIDERS)})."),
        mcp: Optional[bool] = typer.Option(None, "--mcp/--no-mcp", help="Add an MCP server entry to .mcp.json."),
        force: bool = typer.Option(False, "--force", help="Overwrite an existing .discovery/config.yaml."),
        git_hook: Optional[bool] = typer.Option(None, "--git-hook/--no-git-hook",
                                                help="Install (or remove) the post-commit hook that re-indexes "
                                                     "in the background after each commit."),
    ) -> None:
        """Detect the stack and write .discovery/config.yaml (first-run menu on a terminal)."""
        from ...core.paths import ProjectLayout

        g: Globals = get_globals(ctx)
        if provider not in PROVIDERS:
            raise DiscoveryError(f"unknown provider {provider!r}; one of: {', '.join(PROVIDERS)}",
                                 "config_invalid_value")
        root = (path.expanduser().resolve() if path else g.repo)
        if not root.is_dir():
            raise DiscoveryError(f"{root} is not a directory", "project_not_found")
        layout = ProjectLayout.for_repo(root, state_dir=g.state_dir)
        if layout.config_path.exists() and not force:
            if git_hook is not None and prose is None and mcp is None:
                return _hook_only(g, layout, git_hook)      # `init --git-hook` on a set-up project (M2.8)
            raise DiscoveryError(f"{layout.config_path} already exists", "contradictory_flags",
                                 hint="pass --force to overwrite it")

        stack = detect_stack(layout.repo or root)
        if not g.json:
            langs = ", ".join(f"{k} ({v})" for k, v in stack.languages.items()) or "none recognised"
            typer.echo(f"Repository: {layout.repo or root}", err=True)
            typer.echo(f"  languages: {langs}", err=True)
            typer.echo(f"  framework: {stack.framework or '—'}", err=True)
            typer.echo(f"  menu:      {stack.menu_format + f' ({stack.menu_entries} entries)' if stack.menu_format else 'none detected (screens will be skipped)'}", err=True)
            if stack.sap:
                typer.echo(f"  SAP:       {', '.join(stack.sap)} (support arrives with M7)", err=True)

        settings = {"prose": bool(prose), "provider": provider}
        decided = yes or prose is not None
        if not decided:
            if not _is_interactive() or g.json:
                raise DiscoveryError("init needs a choice in a non-interactive session", "init_needs_choice",
                                     hint="pass --yes (structure only) or --prose / --no-prose")
            try:
                settings = _menu(stack, provider)
            except _NonInteractive:
                raise DiscoveryError("no answer on stdin", "init_needs_choice",
                                     hint="pass --yes (structure only) or --prose / --no-prose") from None

        written: list[str] = []
        layout.ensure()
        written.append(str(layout.state_dir / ".gitignore"))
        layout.config_path.write_text(_render_config(settings), encoding="utf-8")
        written.append(str(layout.config_path))

        want_mcp = mcp if mcp is not None else False
        if mcp is None and _is_interactive() and not g.json and not yes:
            try:
                want_mcp = _ask("Add discovery to Claude Code via .mcp.json? (y/n)", "n", ("y", "n")) == "y"
            except _NonInteractive:
                want_mcp = False
        notes: list[str] = []
        if want_mcp:
            if _app_ref[0] is not None and _mcp_available(_app_ref[0]):
                written.append(str(write_mcp_entry(layout.repo or root)))
            else:
                notes.append("MCP server not available yet (roadmap M5.3) — .mcp.json not written")

        removed: list[str] = []
        if git_hook is not None:
            hook_written, hook_removed, hook_note = _apply_hook(layout, git_hook, g.state_dir)
            written += hook_written
            removed += hook_removed
            notes += hook_note

        result = {"repo": str(layout.repo or root), "config": str(layout.config_path), "removed": removed,
                  "prose": settings["prose"], "provider": settings["provider"], "written": written,
                  "notes": notes, "stack": stack.__dict__}

        def human(console) -> None:
            mode = "structure + prose" if settings["prose"] else "structure only (no LLM spend)"
            console.print(f"[green]✓[/] initialised: {mode}, provider {settings['provider']}")
            for f in written:
                console.print(f"  wrote {f}", highlight=False)
            for f in removed:
                console.print(f"  removed {f}", highlight=False)
            for n in notes:
                console.print(f"  [yellow]note[/] {n}", highlight=False)
            console.print("Next: [bold]discover scan[/]")
        emit(g, result, human)

    _app_ref[0] = app


def _apply_hook(layout, install: bool, state_dir) -> tuple[list[str], list[str], list[str]]:
    """(written, removed, notes) for --git-hook / --no-git-hook."""
    from ...core import githook

    repo = layout.repo
    if install:
        custom = layout.state_dir if state_dir else None
        path, action = githook.install(repo, state_dir=custom)
        note = {"created": [], "updated": ["refreshed the existing ai-discovery hook block"],
                "appended": ["chained after the existing post-commit hook"]}[action]
        return [f"{path} (post-commit hook, {action})"], [], note
    path, action = githook.uninstall(repo)
    if action == "absent":
        return [], [], ["no ai-discovery hook block to remove"]
    return [], [f"{path} ({'hook block' if action == 'removed' else 'hook file'})"], []


def _hook_only(g: Globals, layout, install: bool) -> None:
    written, removed, notes = _apply_hook(layout, install, g.state_dir)
    result = {"repo": str(layout.repo), "config": str(layout.config_path), "written": written,
              "removed": removed, "notes": notes, "git_hook": install}

    def human(console) -> None:
        console.print(f"[green]✓[/] post-commit hook {'installed' if install else 'removed'}")
        for f in written:
            console.print(f"  wrote {f}", highlight=False)
        for f in removed:
            console.print(f"  removed {f}", highlight=False)
        for n in notes:
            console.print(f"  [yellow]note[/] {n}", highlight=False)
    emit(g, result, human)


_app_ref: list = [None]
