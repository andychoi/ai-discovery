"""`discover drift [--type screen|all]` — have the sources behind the pages changed? (roadmap item M2.6).

Compares each page's ``source_hashes`` (sha256 per repo-relative file, recorded when the page was built) with
the working tree. Exit 0 when nothing drifted, 6 ``drift_detected`` when a page's own sources changed or
disappeared, 5 when there is no index (``not_indexed``) or nothing carries source hashes
(``nothing_to_check``) — never 0 when nothing was checked. Pages that are only stale *through* a drifted
child (module → overview) are counted, not listed as drift.
"""

from __future__ import annotations

import json
import sqlite3

import typer

from ...core.errors import DiscoveryError
from ..context import get_globals
from ..output import emit, handle_errors

TYPES = ("all", "screen")


def register(app: typer.Typer) -> None:
    @app.command("drift")
    @handle_errors
    def drift(
        ctx: typer.Context,
        type: str = typer.Option("all", "--type", help="all | screen — which pages to check."),
        limit: int = typer.Option(50, "--limit", min=1, help="Drifted pages listed at most (all are counted)."),
    ) -> None:
        """Report pages whose source files changed since the scan (exit 6 on drift; CI-friendly)."""
        from ...pages.build import stale_pages

        g = get_globals(ctx)
        if type not in TYPES:
            raise DiscoveryError(f"--type must be one of {', '.join(TYPES)}", "contradictory_flags")
        layout = g.layout()
        if not layout.db_path.exists():
            raise DiscoveryError("this repository has not been scanned", "not_indexed", hint="run `discover scan`")
        conn = sqlite3.connect(f"file:{layout.db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT scan_id FROM current_scan WHERE id = 1").fetchone()
            if row is None:
                raise DiscoveryError("no completed scan", "not_indexed", hint="run `discover scan`")
            scan_id = row[0]
            type_clause = "" if type == "all" else " AND type = 'screen'"
            checked: dict[str, int] = {}
            for page_id, hashes in conn.execute(
                    f"SELECT page_id, source_hashes_json FROM pages WHERE scan_id = ?{type_clause}", (scan_id,)):
                try:
                    n = len(json.loads(hashes or "{}"))
                except ValueError:
                    n = 0
                if n:
                    checked[page_id] = n
            if not checked:
                raise DiscoveryError("no pages carry source hashes — nothing to check", "nothing_to_check",
                                     hint="run `discover scan` to build the wiki")
            stale = stale_pages(conn, scan_id, layout.repo)
        finally:
            conn.close()
        direct = {p: why for p, why in stale.items() if p in checked and not why.startswith("via")}
        inherited = sum(1 for p, why in stale.items() if why.startswith("via"))
        result = {"status": "drift_detected" if direct else "clean", "scan_id": scan_id, "type": type,
                  "checked": len(checked), "drifted": len(direct), "stale_by_inheritance": inherited,
                  "pages": [{"page_id": p, "reason": why} for p, why in sorted(direct.items())][:limit]}

        def human(console) -> None:
            if not direct:
                console.print(f"[green]✓[/] no drift: {len(checked)} page(s) match their sources", highlight=False)
                return
            console.print(f"[yellow]drift[/]: {len(direct)} of {len(checked)} page(s) changed since the scan"
                          + (f" (+{inherited} stale through them)" if inherited else ""), highlight=False)
            for item in result["pages"]:
                console.print(f"  {item['page_id']}  [dim]{item['reason']}[/]", highlight=False)
            if len(direct) > limit:
                console.print(f"  …and {len(direct) - limit} more", highlight=False)
            console.print("Run [bold]discover scan[/] to refresh them.")
        emit(g, result, human)
        if direct:
            raise typer.Exit(code=6)
