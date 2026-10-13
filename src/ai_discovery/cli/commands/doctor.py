"""`discover doctor [--repair] [--llm]` — environment and index health (roadmap item M2.5).

Each check returns a ``Check`` (ok | warn | fail | skip, detail, fix hint).
``--repair`` fixes what is safe to fix automatically:
  * removes a stale run lock (dead/recycled pid on this host) or a corrupt one;
  * removes a lock held by another host only with ``--yes`` (its liveness
    can't be checked from here);
  * applies pending schema migrations.
``--json`` is read-only and cannot be combined with ``--repair``.
Exit code: 0 when nothing failed, 1 otherwise (after repairs).
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

import typer

from ...core.errors import DiscoveryError
from ..context import Globals, get_globals
from ..output import emit, fail, handle_errors
from ._index import IndexRef, find_index, legacy_dbs

MIN_PYTHON = (3, 10)
MIN_GIT = (2, 31)  # GIT_CONFIG_COUNT env (M0.8 credential header)


@dataclass
class Check:
    name: str
    status: str            # ok | warn | fail | skip
    detail: str = ""
    fix: Optional[str] = None
    repair: Optional[str] = None   # key of a repair action, when one applies
    data: dict = field(default_factory=dict)


# ── individual checks ─────────────────────────────────────────────────────

def check_python() -> Check:
    v = sys.version_info
    ok = v[:2] >= MIN_PYTHON
    return Check("python", "ok" if ok else "fail", f"{v.major}.{v.minor}.{v.micro}",
                 None if ok else f"use Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}")


def check_git() -> Check:
    if shutil.which("git") is None:
        return Check("git", "warn", "git not found", "install git (needed for repo identity and clones)")
    out = subprocess.run(["git", "--version"], capture_output=True, text=True)
    m = re.search(r"(\d+)\.(\d+)", out.stdout)
    version = tuple(int(x) for x in m.groups()) if m else (0, 0)
    if version < MIN_GIT:
        return Check("git", "warn", out.stdout.strip(),
                     f"git >= {MIN_GIT[0]}.{MIN_GIT[1]} needed for private-repo cloning credentials")
    return Check("git", "ok", out.stdout.strip())


def check_sqlite_fts5() -> Check:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return Check("sqlite_fts5", "ok", f"sqlite {sqlite3.sqlite_version}")
    except sqlite3.OperationalError as exc:
        return Check("sqlite_fts5", "fail", f"sqlite {sqlite3.sqlite_version}: {exc}",
                     "use a Python whose sqlite3 has FTS5 (needed for wiki search)")
    finally:
        conn.close()


def check_sqlite_vec() -> Check:
    try:
        try:
            import pysqlite3 as sq  # type: ignore[import-untyped]
            driver = "pysqlite3"
        except ImportError:
            sq = sqlite3
            driver = "sqlite3"
        import sqlite_vec  # type: ignore[import-untyped]

        conn = sq.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        version = conn.execute("SELECT vec_version()").fetchone()[0]
        conn.close()
        return Check("sqlite_vec", "ok", f"{version} via {driver}")
    except Exception as exc:  # noqa: BLE001 — any failure means "unavailable"
        return Check("sqlite_vec", "warn", f"unavailable: {exc}",
                     "RAG/embeddings disabled; install pysqlite3 + sqlite-vec for semantic search")


def check_grammars() -> Check:
    """Every parser module's queries compile and each parser extracts from a sample."""
    import importlib
    import tempfile

    samples = {
        "ai_discovery.parsers.java:JavaParser": ("A.java", "class A { void b() { c(); } void c() {} }"),
        "ai_discovery.parsers.csharp:CSharpParser": ("A.cs", "namespace N { class A { void B() { C(); } void C() {} } }"),
        "ai_discovery.parsers.python_parser:PythonParser": ("a.py", "def b():\n    return c()\n"),
        "ai_discovery.parsers.javascript:JavaScriptParser": ("a.ts", "export function b() { return 1; }\n"),
    }
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for target, (fname, src) in samples.items():
            mod, cls = target.split(":")
            try:
                parser = getattr(importlib.import_module(mod), cls)()
                path = Path(tmp) / fname
                path.write_text(src, encoding="utf-8")
                if not parser.parse_file(path):
                    problems.append(f"{cls}: extracted nothing")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{cls}: {exc}")
    try:
        from importlib.metadata import version
        ts = version("tree-sitter")
    except Exception:  # noqa: BLE001
        ts = "?"
    if problems:
        return Check("grammars", "fail", "; ".join(problems),
                     "reinstall with the pinned versions: pip install -e . (tree-sitter>=0.25,<0.26)")
    return Check("grammars", "ok", f"tree-sitter {ts}; java, c#, python, js/ts parse")


def check_config(g: Globals) -> Check:
    from ...config.loader import ConfigError, config_sources, load_config

    try:
        cfg = load_config(cwd=g.repo)
    except ConfigError as exc:
        return Check("config", "fail", str(exc), "fix the keys listed above")
    sources = config_sources(cfg) or ["defaults only"]
    return Check("config", "ok", f"provider={cfg.provider}; from {', '.join(sources)}",
                 data={"provider": cfg.provider, "sources": sources})


def check_llm(g: Globals) -> list[Check]:
    """Network probes (only with --llm): one generation call + one embedding."""
    from ...ai.llm_client import LLMClient
    from ...config.loader import load_config

    cfg = load_config(cwd=g.repo)
    client = LLMClient(cfg)
    out: list[Check] = []
    try:
        resp = client.invoke("tier1", "Reply with the single word: ok", max_tokens=8)
        out.append(Check("llm_generation", "ok", f"{cfg.provider} {cfg.get_model('fast')}: {resp.text.strip()[:40]!r}"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("llm_generation", "fail", f"{cfg.provider} {cfg.get_model('fast')}: {exc}",
                         "check the provider URL/key in config or .env"))
    try:
        vec = client.get_embedding("connection test")
        out.append(Check("llm_embedding", "ok", f"dim={len(vec)}"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("llm_embedding", "warn", str(exc), "embeddings unavailable: semantic search disabled"))
    return out


def check_index(index: Optional[IndexRef]) -> Check:
    if index is None:
        return Check("index", "warn", "no index for this repo", "run `discover scan`")
    from ...store import current_version, discover_migrations

    head = discover_migrations()[-1].version
    try:
        conn = sqlite3.connect(index.db_path)
        try:
            at = current_version(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return Check("index", "fail", f"{index.db_path}: {exc}", "the DB is unreadable; re-run `discover scan`")
    if at < head:
        return Check("index", "warn", f"{index.kind} DB at migration {at:04d}, head is {head:04d}",
                     "run `discover doctor --repair`", repair="migrate",
                     data={"db": str(index.db_path), "at": at, "head": head})
    return Check("index", "ok", f"{index.kind} DB at migration {at:04d} (head): {index.db_path}",
                 data={"db": str(index.db_path), "at": at, "head": head, "kind": index.kind})


def check_lock(index: Optional[IndexRef], lock_path: Path) -> Check:
    from ...core.lock import lock_status

    path = index.lock_path if index else lock_path
    state, holder = lock_status(path)
    who = f"pid {holder.get('pid')} on {holder.get('hostname')} since {holder.get('started_at')}" if holder else ""
    data = {"path": str(path), "state": state, "token": holder.get("token")}
    if state == "none":
        return Check("lock", "ok", "no run in progress", data=data)
    if state == "held":
        return Check("lock", "ok", f"scan in progress ({who})", data=data)
    if state == "stale":
        return Check("lock", "warn", f"stale lock from a dead process ({who})",
                     "run `discover doctor --repair`", repair="clear_lock", data=data)
    if state == "corrupt":
        return Check("lock", "warn", f"unreadable lock file {path}",
                     "run `discover doctor --repair`", repair="clear_lock", data=data)
    return Check("lock", "warn", f"lock held by another host ({who}); can't verify it is alive",
                 "if that run is gone: `discover doctor --repair --yes`", repair="clear_foreign_lock", data=data)


def check_old_layout(g: Globals, index: Optional[IndexRef]) -> Check:
    olds = legacy_dbs([g.repo, Path.cwd()])
    if not olds:
        return Check("legacy_layout", "ok", "none")
    mine = [str(p) for p in olds if index and p == index.db_path]
    others = [str(p) for p in olds if not (index and p == index.db_path)]
    detail = f"{len(olds)} legacy-layout DB(s) under data/discovery-output"
    fix = ("v1 scans (M2.3) write <repo>/.discovery/; legacy trees are not migrated — "
           "delete them when no longer needed")
    return Check("legacy_layout", "warn", detail, fix, data={"in_use": mine, "other": others})


# ── repairs ───────────────────────────────────────────────────────────────

def _repair(check: Check, yes: bool) -> str:
    from ...core.lock import remove_lock

    if check.repair == "migrate":
        from ...store import migrate

        conn = sqlite3.connect(check.data["db"])
        try:
            applied = migrate(conn)
        finally:
            conn.close()
        return f"applied migrations {applied}"
    if check.repair == "clear_lock":
        token = check.data.get("token")
        removed = remove_lock(Path(check.data["path"]), token if check.data["state"] == "stale" else None)
        return "removed stale lock" if removed else "lock changed meanwhile; left alone"
    if check.repair == "clear_foreign_lock":
        if not yes:
            return "skipped: lock belongs to another host; re-run with --yes to remove it"
        removed = remove_lock(Path(check.data["path"]), check.data.get("token"))
        return "removed foreign-host lock" if removed else "lock changed meanwhile; left alone"
    return "no repair available"


# ── command ───────────────────────────────────────────────────────────────

def run_checks(g: Globals, llm: bool) -> list[Check]:
    index, layout = find_index(g)
    checks: list[Callable[[], Check | list[Check]]] = [
        check_python, check_git, check_sqlite_fts5, check_sqlite_vec, check_grammars,
        lambda: check_config(g),
        lambda: check_index(index),
        lambda: check_lock(index, layout.lock_path),
        lambda: check_old_layout(g, index),
    ]
    if llm:
        checks.append(lambda: check_llm(g))
    out: list[Check] = []
    for fn in checks:
        result = fn()
        out.extend(result if isinstance(result, list) else [result])
    return out


_ICON = {"ok": "[green]✓[/]", "warn": "[yellow]![/]", "fail": "[red]✗[/]", "skip": "[dim]-[/]"}


def _render(checks: list[Check], repairs: dict[str, str]):
    from rich.markup import escape

    def human(console) -> None:
        for c in checks:
            console.print(f" {_ICON[c.status]} [bold]{c.name}[/]  {escape(c.detail)}", highlight=False)
            if c.name in repairs:
                console.print(f"     repair: {escape(repairs[c.name])}", highlight=False)
            elif c.fix and c.status != "ok":
                console.print(f"     fix: {escape(c.fix)}", highlight=False)
        bad = sum(c.status == "fail" for c in checks)
        warn = sum(c.status == "warn" for c in checks)
        console.print(f"\n{'[red]' if bad else '[green]'}{bad} failed[/], {warn} warning(s)")
    return human


def register(app: typer.Typer) -> None:
    @app.command("doctor")
    @handle_errors
    def doctor(
        ctx: typer.Context,
        repair: bool = typer.Option(False, "--repair", help="Fix what is safe to fix (locks, migrations)."),
        yes: bool = typer.Option(False, "--yes", "-y", help="With --repair: also remove another host's lock."),
        llm: bool = typer.Option(False, "--llm", help="Also probe the LLM provider (network; replaces test-llm)."),
    ) -> None:
        """Check the environment and this repo's index; --repair fixes what it safely can."""
        g = get_globals(ctx)
        if repair and g.json:
            fail(g, DiscoveryError("--json is read-only; it cannot be combined with --repair",
                                   "contradictory_flags"))
        checks = run_checks(g, llm)
        repairs: dict[str, str] = {}
        if repair:
            for c in checks:
                if c.repair:
                    repairs[c.name] = _repair(c, yes)
            if repairs:
                checks = run_checks(g, llm)
        emit(g, {"checks": [asdict(c) for c in checks]}, _render(checks, repairs))
        if any(c.status == "fail" for c in checks):
            raise typer.Exit(code=1)
