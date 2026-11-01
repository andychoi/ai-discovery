"""Build the wiki after a scan (roadmap item M3.5, ADR-0002/0005).

Registered as the ``pages`` post-scan hook (``core.hooks``): after every
completed scan it runs all page builders against the new snapshot, stores the
pages (+ backlinks) for that scan, rebuilds ``pages_fts``, and tombstones page
ids that existed in the previous scan but not in this one — each pointing at
its nearest surviving parent so links and notes keep working.

Freshness: pages are rebuilt every scan, so they are fresh when written. A
page becomes *stale* when a file it was built from changes on disk afterwards
(``source_hashes``); staleness then propagates up the hierarchy
(file → module → parent modules → overview). ``stale_pages`` computes this on
read without touching the DB.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from .model import make_page_id, save_pages, split_page_id
from .notes import record_alias
from .registry import BuildContext, run_builders

logger = logging.getLogger(__name__)

OVERVIEW_ID = "overview:system"
UP_LINK_KINDS = ("belongs_to", "parent")


@dataclass
class BuildResult:
    scan_id: int
    pages: int
    by_type: dict[str, int] = field(default_factory=dict)
    tombstoned: list[str] = field(default_factory=list)
    fts_rows: Optional[int] = None

    def __str__(self) -> str:
        fts = "" if self.fts_rows is None else f", {self.fts_rows} indexed"
        gone = f", {len(self.tombstoned)} removed" if self.tombstoned else ""
        return f"{self.pages} pages{fts}{gone}"


def _sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _stamp_source_hashes(pages, repo: Path) -> None:
    """Record the hash of every cited file (pages without hashes of their own)."""
    cache: dict[str, Optional[str]] = {}
    for p in pages:
        if p.source_hashes:
            continue
        for c in p.citations:
            if c.path not in cache:
                cache[c.path] = _sha256(repo / c.path)
            if cache[c.path]:
                p.source_hashes[c.path] = cache[c.path]


def _previous_scan_with_pages(conn: sqlite3.Connection, scan_id: int) -> Optional[int]:
    row = conn.execute("SELECT MAX(scan_id) FROM pages WHERE scan_id < ?", (scan_id,)).fetchone()
    return row[0] if row and row[0] is not None else None


def _ancestors(page_id: str) -> list[str]:
    """Pages above *page_id*, nearest first (file → module → parent modules → root → overview)."""
    page_type, key = split_page_id(page_id)
    chain: list[str] = []
    path: Optional[str] = None
    if page_type == "symbol" and "@" in key:
        path = key.split("@", 1)[1].rsplit(":", 1)[0]
        chain.append(make_page_id("file", path))
    elif page_type == "file":
        path = key
    if page_type == "module":
        dirs = PurePosixPath(key).parts[:-1] if key != "." else ()
    else:
        dirs = PurePosixPath(path).parts[:-1] if path else ()
    for i in range(len(dirs), 0, -1):
        chain.append(make_page_id("module", PurePosixPath(*dirs[:i]).as_posix()))
    if page_id != make_page_id("module", "."):
        chain.append(make_page_id("module", "."))
    chain.append(OVERVIEW_ID)
    return chain


def nearest_parent(page_id: str, live: set[str]) -> str:
    """The closest surviving ancestor of a removed page; the overview as last resort."""
    for candidate in _ancestors(page_id):
        if candidate in live and candidate != page_id:
            return candidate
    return OVERVIEW_ID


def build_pages(conn: sqlite3.Connection, scan_id: int, repo: Path, *, commit: str = "",
                config_hash: str = "", extra: Optional[dict] = None, dirty: bool = False) -> BuildResult:
    """Build and store every page for *scan_id*; tombstone pages that disappeared."""
    from ..store.fts import fts5_available, rebuild_fts

    ctx = BuildContext(conn=conn, scan_id=scan_id, repo=Path(repo), commit=commit,
                       config_hash=config_hash, extra=dict(extra or {}), dirty=dirty)
    pages = run_builders(ctx)
    _stamp_source_hashes(pages, Path(repo))
    conn.execute("DELETE FROM page_links WHERE scan_id = ?", (scan_id,))
    conn.execute("DELETE FROM pages WHERE scan_id = ?", (scan_id,))
    save_pages(conn, scan_id, pages)

    live = {p.page_id for p in pages}
    tombstoned: list[str] = []
    prev = _previous_scan_with_pages(conn, scan_id)
    if prev is not None:
        old = {r[0] for r in conn.execute("SELECT page_id FROM pages WHERE scan_id = ?", (prev,))}
        for gone in sorted(old - live):
            record_alias(conn, gone, nearest_parent(gone, live), removed_in_scan=scan_id)
            tombstoned.append(gone)
    # a page that came back is live again: drop its old tombstone
    conn.executemany("DELETE FROM page_tombstones WHERE page_id = ?", [(pid,) for pid in sorted(live)])

    fts_rows = rebuild_fts(conn, scan_id) if fts5_available(conn) else None
    by_type: dict[str, int] = {}
    for p in pages:
        by_type[p.type] = by_type.get(p.type, 0) + 1
    return BuildResult(scan_id, len(pages), dict(sorted(by_type.items())), tombstoned, fts_rows)


# ── staleness ─────────────────────────────────────────────────────────────

def _up_edges(conn: sqlite3.Connection, scan_id: int) -> dict[str, set[str]]:
    """child page -> pages above it (module parents, owning file/module, overview)."""
    up: dict[str, set[str]] = {}
    for src, dst, kind in conn.execute("SELECT src, dst, kind FROM page_links WHERE scan_id = ?", (scan_id,)):
        if kind in UP_LINK_KINDS:
            up.setdefault(src, set()).add(dst)
        elif kind == "child":
            up.setdefault(dst, set()).add(src)
    return up


def stale_pages(conn: sqlite3.Connection, scan_id: int, repo: Path) -> dict[str, str]:
    """Pages whose source files changed since the scan, plus their ancestors.

    Returns ``{page_id: reason}`` where reason is ``"changed: <path>"`` for a
    page built from a changed file, or ``"via <page_id>"`` when inherited.
    """
    repo = Path(repo)
    current: dict[str, Optional[str]] = {}
    direct: dict[str, str] = {}
    for page_id, hashes_json in conn.execute(
            "SELECT page_id, source_hashes_json FROM pages WHERE scan_id = ? ORDER BY page_id", (scan_id,)):
        for path, digest in sorted(json.loads(hashes_json or "{}").items()):
            if path not in current:
                current[path] = _sha256(repo / path)
            if current[path] != digest:
                direct[page_id] = f"changed: {path}" if current[path] else f"deleted: {path}"
                break
    if not direct:
        return {}
    up = _up_edges(conn, scan_id)
    stale = dict(direct)
    frontier = sorted(direct)
    while frontier:
        child = frontier.pop()
        for parent in sorted(up.get(child, ())):
            if parent not in stale:
                stale[parent] = f"via {child}"
                frontier.append(parent)
    if OVERVIEW_ID not in stale and any(split_page_id(p)[0] in ("file", "module") for p in stale):
        if conn.execute("SELECT 1 FROM pages WHERE scan_id = ? AND page_id = ?", (scan_id, OVERVIEW_ID)).fetchone():
            stale[OVERVIEW_ID] = "via modules"
    return dict(sorted(stale.items()))


# ── post-scan hook ────────────────────────────────────────────────────────

def _scan_meta(conn: sqlite3.Connection, scan_id: int) -> tuple[str, str, bool]:
    """(commit, config hash, dirty) — dirty: the scan read uncommitted changes (M2.8 `worktree`)."""
    try:
        row = conn.execute("SELECT commit_sha, config_json, worktree FROM scan_runs WHERE id = ?",
                           (scan_id,)).fetchone()
    except sqlite3.OperationalError:   # store older than migration 0006
        row = conn.execute("SELECT commit_sha, config_json, '' FROM scan_runs WHERE id = ?", (scan_id,)).fetchone()
    if not row:
        return "", "", False
    commit, config_json, worktree = row
    cfg_hash = hashlib.sha256((config_json or "").encode()).hexdigest()[:16] if config_json else ""
    return commit or "", cfg_hash, bool(worktree)


def _post_scan(ctx) -> str:
    from ..db import get_conn

    if ctx.scan_id is None or ctx.layout.repo is None:
        return "skipped (no scan)"
    conn = get_conn(ctx.layout.db_path)
    try:
        commit, cfg_hash, dirty = _scan_meta(conn, ctx.scan_id)
        result = build_pages(conn, ctx.scan_id, ctx.layout.repo, commit=commit, config_hash=cfg_hash, dirty=dirty)
        conn.commit()
    finally:
        conn.close()
    return str(result)


def _register() -> None:
    from ..core.hooks import is_registered, register_post_scan

    if not is_registered("pages"):
        register_post_scan("pages", _post_scan)


_register()
