"""`discover export --format markdown|html|json [--out DIR]` (roadmap item M3.10, design/cli-v1.md).

Views over the current scan: ``json`` the versioned knowledge graph, ``html`` the wiki as a folder that opens
from ``file://``, ``markdown`` one file per page. Default output: ``<state>/exports/<format>``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import typer

from ...core.errors import DiscoveryError
from ..context import get_globals
from ..output import emit, handle_errors

FORMATS = ("markdown", "html", "json")


def register(app: typer.Typer) -> None:
    @app.command("export")
    @handle_errors
    def export(
        ctx: typer.Context,
        format: str = typer.Option(..., "--format", "-f", help="markdown | html | json"),
        out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output directory (default <state>/exports/<format>)."),
    ) -> None:
        """Export the current scan's wiki and knowledge graph (no LLM, no network)."""
        g = get_globals(ctx)
        if format not in FORMATS:
            raise DiscoveryError(f"--format must be one of {', '.join(FORMATS)}", "contradictory_flags")
        layout = g.layout()
        if not layout.db_path.exists():
            raise DiscoveryError("this repository has not been scanned", "not_indexed", hint="run `discover scan`")
        conn = sqlite3.connect(f"file:{layout.db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT scan_id FROM current_scan WHERE id = 1").fetchone()
            if row is None:
                raise DiscoveryError("no completed scan to export", "not_indexed", hint="run `discover scan`")
            scan_id = row[0]
            commit = (conn.execute("SELECT commit_sha FROM scan_runs WHERE id = ?", (scan_id,)).fetchone()
                      or [""])[0] or ""
            out_dir = (out.expanduser().resolve() if out else layout.exports_dir / format)
            if format == "json":
                from ...export import json as jexport

                doc = jexport.build(conn, scan_id, project=layout.slug, repo=layout.repo)
                path = jexport.write(doc, out_dir)
                result = {"format": format, "path": str(path), "counts": doc["counts"]}
            elif format == "markdown":
                from ...export import markdown as mexport

                result = {"format": format, **mexport.write(conn, scan_id, out_dir, commit=commit)}
            else:
                from ...export import html as hexport

                try:
                    result = {"format": format, **hexport.write(layout, out_dir)}
                except hexport.BundleMissing as exc:
                    raise DiscoveryError(str(exc), "failure", hint="build it with scripts/build_web.py") from exc
        finally:
            conn.close()
        result["scan_id"] = scan_id

        def human(console) -> None:
            where = result.get("index") or result.get("path") or result.get("dir")
            console.print(f"[green]✓[/] exported {format} → {where}", highlight=False)
            if format == "html":
                console.print("  open it in a browser (works from file://, offline)", highlight=False)
        emit(g, result, human)
