"""`discover ask "question"` — find where something lives (roadmap item M2.7; replaces `chat`).

Hybrid retrieval through the query service (FTS + vector when embeddings exist, fused by RRF): the answer is
a ranked list of wiki pages and symbols, each with its page id and the ``file:line`` citations behind it — no
model call, nothing generated. ``discover serve`` / an MCP client turn the same hits into prose if wanted.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

import typer

from ...core.errors import DiscoveryError
from ..context import get_globals
from ..output import emit, handle_errors


def _citations(db_path, page_ids: list[str], per_page: int = 3) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if not page_ids or not db_path.exists():
        return out
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT scan_id FROM current_scan WHERE id = 1").fetchone()
        if row is None:
            return out
        marks = ",".join("?" * len(page_ids))
        for pid, raw in conn.execute(f"SELECT page_id, citations_json FROM pages WHERE scan_id = ? AND page_id IN ({marks})",
                                     (row[0], *page_ids)):
            try:
                cites = json.loads(raw or "[]")
            except ValueError:
                cites = []
            out[pid] = [f"{c['path']}:{c['line_start']}" + (f"-{c['line_end']}" if c.get("line_end", c["line_start"]) != c["line_start"] else "")
                        for c in cites[:per_page]]
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    return out


def register(app: typer.Typer) -> None:
    @app.command("ask")
    @handle_errors
    def ask(
        ctx: typer.Context,
        question: str = typer.Argument(..., help="What to look for, in words or identifiers."),
        kinds: Optional[str] = typer.Option(None, "--kinds", help="Page types and/or 'symbol', comma-separated."),
        limit: int = typer.Option(10, "--limit", min=1, max=50, help="Hits to show."),
    ) -> None:
        """Search the wiki and the code graph; every hit cites its page and source lines."""
        from ... import query

        g = get_globals(ctx)
        layout = g.layout()
        kinds_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
        answer = query.search(query.Project(layout), question, kinds=kinds_list, limit=limit)
        if answer.get("status") == "not_indexed":
            raise DiscoveryError("this repository has not been scanned", "not_indexed", hint="run `discover scan`")
        hits = (answer.get("data") or {}).get("hits", [])
        cites = _citations(layout.db_path, [h["id"] for h in hits])
        for h in hits:
            h["citations"] = cites.get(h["id"], [])
        result = {"question": question, "hits": hits, "degraded": (answer.get("meta") or {}).get("degraded", [])}

        def human(console) -> None:
            if not hits:
                console.print(f"no matches for “{question}”", highlight=False)
                return
            for i, h in enumerate(hits, 1):
                console.print(f"[bold]{i}. {h['title']}[/]  [dim]{h['id']}[/]", highlight=False)
                if h.get("snippet"):
                    console.print(f"   {h['snippet'].replace('[', '').replace(']', '')}", highlight=False)
                if h["citations"]:
                    console.print(f"   [dim]{', '.join(h['citations'])}[/]", highlight=False)
            if result["degraded"]:
                console.print(f"[yellow]![/] {'; '.join(result['degraded'])}", highlight=False)
        emit(g, result, human)
