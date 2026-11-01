"""Groundedness of page prose (roadmap item M3.8, ADR-0005 §4).

A narrative may only refer to what the page or the index actually holds. ``Grounder.check`` inspects the
LLM's text before it is stored:

- **backticked references** — grounded when they appear verbatim in what the model was shown (title, facts,
  structural text) or exist in the index (a qualified or short symbol name, a file path or basename, a route,
  a table, a page title); otherwise *unverified*;
- **file citations** (``path/File.ext:12``) — the file must be indexed (``ai/prose_validator`` rules);
- **links** — ``[text](target)`` must point at an existing page id; any other target (URLs included) is
  unverified, since nothing in the scan backs it;
- **mermaid** — LLM-written diagrams are sanitized (``click`` / ``%%{init}`` directives, HTML and script
  lines removed); a block whose diagram type is unknown is dropped.

``pages/prose.py`` re-asks once with the unverified references named; a second unverified answer is treated
as a failure (existing prose kept as stale, else a stub at confidence 0.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ..ai.prose_validator import _FILE_CITATION_RE, KnownGraph, _file_is_known, build_known_graph

_BACKTICK = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_LINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\s]+)\)")
_MERMAID = re.compile(r"```mermaid[ \t]*\n(.*?)```", re.S)
_PAGE_ID = re.compile(r"^[a-z_]+:\S+$")
MERMAID_TYPES = ("graph", "flowchart", "sequenceDiagram", "stateDiagram", "stateDiagram-v2", "classDiagram",
                 "erDiagram", "journey", "gantt", "pie", "mindmap", "timeline")
_MERMAID_UNSAFE = re.compile(r"^\s*(click\s|%%\{|<\s*/?\s*(script|iframe|img|a)\b)|javascript:", re.I)
_TRIVIAL = re.compile(r"^[\W\d_]+$")


@dataclass
class Grounding:
    text: str                                         # sanitized narrative (mermaid cleaned or dropped)
    unverified: list[str] = field(default_factory=list)
    dropped_diagrams: int = 0

    @property
    def ok(self) -> bool:
        return not self.unverified


class Grounder:
    """Index lookups built once per page build; ``check`` is pure and thread-safe."""

    def __init__(self, nodes: Iterable, page_ids: Iterable[str], extra_names: Iterable[str] = ()):
        rows = list(nodes)
        self.known: KnownGraph = build_known_graph(rows)
        self.names = {n.lower() for n in self.known.symbol_names | self.known.qualified_names}
        self.names |= {str(x).lower() for x in extra_names if x}
        for r in rows:   # routes and table names are referenced bare in prose
            hints = r.get("framework_hints") if isinstance(r, dict) else None
            for key in ("route", "table"):
                if hints and hints.get(key):
                    self.names.add(str(hints[key]).lower())
        self.page_ids = set(page_ids)

    @classmethod
    def from_db(cls, conn, scan_id: int, page_ids: Iterable[str], titles: Iterable[str] = ()) -> "Grounder":
        import json

        import sqlite3

        nodes = []
        try:
            rows = conn.execute("SELECT file_path, name, qualified_name, framework_hints FROM code_nodes "
                                "WHERE scan_id = ?", (scan_id,)).fetchall()
        except sqlite3.OperationalError:   # no graph tables: only what the page itself shows is grounded
            rows = []
        for fp, name, qn, hints in rows:
            try:
                h = json.loads(hints) if hints else {}
            except ValueError:
                h = {}
            nodes.append({"file_path": fp, "name": name, "qualified_name": qn,
                          "framework_hints": h if isinstance(h, dict) else {}})
        return cls(nodes, page_ids, titles)

    # ── checks ────────────────────────────────────────────────────────────
    def _token_ok(self, token: str, shown: str) -> bool:
        if token in shown or _TRIVIAL.match(token):
            return True
        low = token.lower().strip("()")
        if low in self.names or low.rsplit(".", 1)[-1] in self.names:
            return True
        if "/" in token or "." in token.rsplit("/", 1)[-1]:
            if _file_is_known(token.lstrip("./"), self.known):
                return True
        # a qualified reference whose tail is a known qualified name (`Shop.OrderService.save`)
        return any(qn.endswith("." + token) or token.endswith("." + qn) for qn in self.known.qualified_names)

    def sanitize_mermaid(self, text: str) -> tuple[str, int]:
        dropped = 0

        def fix(m: re.Match) -> str:
            nonlocal dropped
            lines = [ln for ln in m.group(1).splitlines() if ln.strip()]
            if not lines or lines[0].split()[0] not in MERMAID_TYPES:
                dropped += 1
                return ""
            kept = [ln for ln in lines if not _MERMAID_UNSAFE.search(ln)]
            return "```mermaid\n" + "\n".join(kept) + "\n```"
        return _MERMAID.sub(fix, text), dropped

    def check(self, text: str, shown: str) -> Grounding:
        """Ground *text* (the narrative) against *shown* (what the model was given) and the index."""
        clean, dropped = self.sanitize_mermaid(text)
        prose_only = _MERMAID.sub("", clean)           # diagrams are sanitized, not fact-checked
        bad: list[str] = []
        for token in _BACKTICK.findall(prose_only):
            token = token.strip()
            cited = _FILE_CITATION_RE.fullmatch(token)
            if cited:                                   # `path/File.ext:12` is checked as a file citation
                token = cited.group(1)
                if token not in shown and not _file_is_known(token, self.known) and token not in bad:
                    bad.append(token)
                continue
            if token and not self._token_ok(token, shown) and token not in bad:
                bad.append(token)
        for path, _line in _FILE_CITATION_RE.findall(prose_only):
            if path not in shown and not _file_is_known(path, self.known) and path not in bad:
                bad.append(path)
        for _label, target in _LINK.findall(prose_only):
            if not (_PAGE_ID.match(target) and target in self.page_ids) and target not in bad:
                bad.append(target)
        return Grounding(clean.strip(), bad, dropped)
