"""Page model (roadmap item M3.1, ADR-0005).

A ``Page`` is the unit of the wiki, the MCP context and the exports. Pages are
built deterministically from a scan snapshot by builders (``registry.py``) and
stored in the ``pages`` table (migration 0002). Serialization is canonical
(sorted keys, fixed separators) so two builds of the same snapshot are
byte-identical (idempotence, M3.5).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

# Initial page types (ADR-0005 §2); SAP types arrive with M7.4.
PAGE_TYPES: frozenset[str] = frozenset({
    "overview", "domain", "module", "file", "symbol", "endpoint", "screen", "menu",
    "entity", "table", "stored_proc", "batch_job", "interface", "flow", "onboarding",
    "tcode", "dynpro",
})
FRESHNESS = ("fresh", "stale", "unknown")
LINK_KINDS = frozenset({
    "link", "parent", "child", "calls", "called_by", "reads", "writes", "renders",
    "routes_to", "belongs_to", "implements", "see_also",
})

CONFIDENCE_STRUCTURAL = 1.0
CONFIDENCE_PROSE = 0.8
CONFIDENCE_STUB = 0.3

_KEY_UNSAFE = re.compile(r"\s+")


class PageError(ValueError):
    pass


def make_page_id(page_type: str, key: str) -> str:
    """Stable id ``<type>:<key>``; whitespace in *key* collapses to '-'."""
    if page_type not in PAGE_TYPES:
        raise PageError(f"unknown page type {page_type!r}")
    key = _KEY_UNSAFE.sub("-", str(key).strip())
    if not key:
        raise PageError("page key must not be empty")
    return f"{page_type}:{key}"


def split_page_id(page_id: str) -> tuple[str, str]:
    page_type, sep, key = page_id.partition(":")
    if not sep or page_type not in PAGE_TYPES or not key:
        raise PageError(f"malformed page id {page_id!r} (expected '<type>:<key>')")
    return page_type, key


@dataclass(frozen=True, order=True)
class Link:
    dst: str
    kind: str = "link"

    def __post_init__(self) -> None:
        split_page_id(self.dst)
        if self.kind not in LINK_KINDS:
            raise PageError(f"unknown link kind {self.kind!r}")


@dataclass(frozen=True, order=True)
class Citation:
    path: str               # repo-relative, '/' separators
    line_start: int
    line_end: int

    def __post_init__(self) -> None:
        if self.path.startswith("/") or "\\" in self.path:
            raise PageError(f"citation path must be repo-relative with '/': {self.path!r}")
        if self.line_start < 1 or self.line_end < self.line_start:
            raise PageError(f"bad citation lines {self.line_start}-{self.line_end} for {self.path}")

    def __str__(self) -> str:
        span = str(self.line_start) if self.line_start == self.line_end else f"{self.line_start}-{self.line_end}"
        return f"{self.path}:{span}"


@dataclass(frozen=True)
class Stamp:
    builder: str
    builder_version: str
    config_hash: str = ""
    commit: str = ""
    dirty: bool = False


@dataclass(frozen=True)
class Section:
    heading: str
    body_md: str


@dataclass
class Page:
    page_id: str
    title: str
    facts: dict[str, Any] = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    confidence: float = CONFIDENCE_STRUCTURAL
    stamp: Stamp | None = None
    source_hashes: dict[str, str] = field(default_factory=dict)
    freshness: str = "fresh"
    prose_ref: str | None = None

    def __post_init__(self) -> None:
        split_page_id(self.page_id)
        if not self.title.strip():
            raise PageError(f"{self.page_id}: title must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise PageError(f"{self.page_id}: confidence {self.confidence} out of range")
        if self.freshness not in FRESHNESS:
            raise PageError(f"{self.page_id}: freshness must be one of {FRESHNESS}")
        # Canonical order so serialization is deterministic.
        self.links = sorted(set(self.links))
        self.citations = sorted(set(self.citations))

    @property
    def type(self) -> str:
        return split_page_id(self.page_id)[0]

    @property
    def key(self) -> str:
        return split_page_id(self.page_id)[1]

    def body_md(self) -> str:
        """Markdown body: sections in order (rendering templates arrive with M3.2)."""
        return "\n\n".join(f"## {s.heading}\n\n{s.body_md.strip()}" for s in self.sections) + ("\n" if self.sections else "")

    # ── storage ───────────────────────────────────────────────────────────
    def to_row(self, scan_id: int) -> dict[str, Any]:
        return {
            "scan_id": scan_id,
            "page_id": self.page_id,
            "type": self.type,
            "title": self.title,
            "facts_json": canonical_json({"facts": self.facts,
                                          "sections": [asdict(s) for s in self.sections]}),
            "body_md": self.body_md(),
            "links_json": canonical_json([asdict(link) for link in self.links]),
            "citations_json": canonical_json([asdict(c) for c in self.citations]),
            "confidence": self.confidence,
            "stamp_json": canonical_json(asdict(self.stamp) if self.stamp else None),
            "source_hashes_json": canonical_json(self.source_hashes),
            "freshness": self.freshness,
            "prose_ref": self.prose_ref,
        }

    @classmethod
    def from_row(cls, row: Any) -> "Page":
        r = dict(row)
        payload = json.loads(r["facts_json"] or "{}")
        stamp = json.loads(r["stamp_json"]) if r.get("stamp_json") else None
        return cls(
            page_id=r["page_id"],
            title=r["title"],
            facts=payload.get("facts", {}),
            sections=[Section(**s) for s in payload.get("sections", [])],
            links=[Link(**x) for x in json.loads(r["links_json"] or "[]")],
            citations=[Citation(**x) for x in json.loads(r["citations_json"] or "[]")],
            confidence=float(r["confidence"]),
            stamp=Stamp(**stamp) if stamp else None,
            source_hashes=json.loads(r["source_hashes_json"] or "{}"),
            freshness=r["freshness"],
            prose_ref=r.get("prose_ref"),
        )


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


_COLUMNS = ("scan_id", "page_id", "type", "title", "facts_json", "body_md", "links_json",
            "citations_json", "confidence", "stamp_json", "source_hashes_json", "freshness", "prose_ref")


def save_pages(conn, scan_id: int, pages: Iterable[Page]) -> int:
    """Upsert *pages* (and their outgoing links) for *scan_id*. Returns the count."""
    n = 0
    marks = ", ".join("?" * len(_COLUMNS))
    for page in pages:
        row = page.to_row(scan_id)
        conn.execute(f"INSERT OR REPLACE INTO pages ({', '.join(_COLUMNS)}) VALUES ({marks})",
                     [row[c] for c in _COLUMNS])
        conn.execute("DELETE FROM page_links WHERE scan_id = ? AND src = ?", (scan_id, page.page_id))
        conn.executemany("INSERT OR IGNORE INTO page_links (scan_id, src, dst, kind) VALUES (?, ?, ?, ?)",
                         [(scan_id, page.page_id, link.dst, link.kind) for link in page.links])
        n += 1
    return n


def load_page(conn, scan_id: int, page_id: str) -> Page | None:
    prev = conn.row_factory
    import sqlite3

    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM pages WHERE scan_id = ? AND page_id = ?", (scan_id, page_id)).fetchone()
    finally:
        conn.row_factory = prev
    return Page.from_row(row) if row else None


def backlinks(conn, scan_id: int, page_id: str) -> list[Link]:
    rows = conn.execute("SELECT src, kind FROM page_links WHERE scan_id = ? AND dst = ? ORDER BY src, kind",
                        (scan_id, page_id)).fetchall()
    return [Link(src, kind) for src, kind in rows]
