"""`discover impact TARGET` — what may break if TARGET changes (roadmap item M2.7; data: M5.2).

TARGET is a symbol (qualified name) or ``file:…``, ``table:…``, ``screen:…``, ``entity:…``, ``endpoint:…``
(the old ``impact ENTITY`` is ``impact entity:NAME``). Same engine and answer as MCP ``get_impact`` and the
wiki's ``/impact`` view. Exit 5 ``target_not_found`` when nothing (or more than one thing) matches.
"""

from __future__ import annotations

from typing import Optional

import typer

from ...core.errors import DiscoveryError
from ..context import get_globals
from ..output import emit, handle_errors


def register(app: typer.Typer) -> None:
    @app.command("impact")
    @handle_errors
    def impact(
        ctx: typer.Context,
        target: str = typer.Argument(..., help="symbol, file:PATH, table:NAME, screen:ID, entity:NAME, endpoint:…"),
        depth: int = typer.Option(3, "--depth", min=1, max=6, help="How many call levels to walk."),
        include: Optional[str] = typer.Option(None, "--include", help="Extra edge kinds, comma-separated."),
    ) -> None:
        """Rank what may break if TARGET changes, with the paths and the unknowns."""
        from ... import query

        g = get_globals(ctx)
        layout = g.layout()
        kinds = [k.strip() for k in include.split(",") if k.strip()] if include else None
        answer = query.impact(query.Project(layout), target, depth=depth, include=kinds)
        status = answer.get("status")
        if status == "not_indexed":
            raise DiscoveryError("this repository has not been scanned", "not_indexed", hint="run `discover scan`")
        data = answer.get("data") or {}
        ambiguous = status == "unknown" and data.get("reason") == "ambiguous"
        if status == "not_found" or ambiguous:
            options = [c.get("id") or c.get("qualified_name") or str(c) if isinstance(c, dict) else str(c)
                       for c in (data.get("candidates") or data.get("did_you_mean") or [])][:8]
            raise DiscoveryError(f"{'more than one' if ambiguous else 'no'} match for {target!r}",
                                 "target_not_found",
                                 hint=("did you mean: " + ", ".join(options)) if options else
                                 "use a qualified name or a prefix: file:, table:, screen:, entity:, endpoint:")
        d = answer["data"]

        def human(console) -> None:
            console.print(f"[bold]impact of[/] {d['target']}  [dim]depth {d['depth']}[/]", highlight=False)
            if d.get("note"):
                console.print(f"  [yellow]![/] {d['note']}", highlight=False)
            if not d["items"]:
                console.print("  nothing in the graph depends on it", highlight=False)
            for i, it in enumerate(d["items"], 1):
                where = f"  [dim]{it['citation']['path']}:{it['citation']['line_start']}[/]" if it.get("citation") else ""
                console.print(f"  {i:>2}. {it['node']}  [dim]{it['kind']} · {it['score']:.2f} · depth {it['depth']}"
                              f" · {it.get('certainty', '')}[/]{where}", highlight=False)
            if d.get("entry_points_reached"):
                console.print("Entry points reached: " + ", ".join(d["entry_points_reached"]), highlight=False)
            unknowns = d.get("unknowns") or []
            console.print(f"Unknowns: {len(unknowns)}" + (" (unresolved calls on the paths — the list may be incomplete)"
                                                          if unknowns else ""), highlight=False)
        emit(g, {"target": target, **answer}, human)
