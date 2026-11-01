"""Stage ``domains``: domain classification + code-node persistence (first part of
pipeline phase 7), moved verbatim (M1.6). Runs inside ``graph.execute``'s phase-7
checkpoint, before the call graph — same order as before."""

from __future__ import annotations

import json

from .context import RunContext


def run(ctx: RunContext) -> None:
    from ..db import get_conn
    from ..graph.domain_classifier import classify_domains
    from ..pipeline import _timed, console

    all_nodes, scan_id = ctx.all_nodes, ctx.scan_id

    with _timed("domain classify"), console.status("[bold cyan]Classifying domains..."):
        domains_dict = classify_domains(all_nodes)
    domains = list(domains_dict.values())
    ctx.domains_dict, ctx.domains = domains_dict, domains
    console.print(f"  Domains: [green]{len(domains)}[/] ({', '.join(d.name for d in domains)})")

    with _timed("persist nodes"), console.status("[bold cyan]Persisting code nodes..."):
        conn = get_conn(ctx.db_path)
        try:
            for node in all_nodes:
                conn.execute(
                    "INSERT OR IGNORE INTO code_nodes "
                    "(scan_id, file_path, language, node_type, name, qualified_name, "
                    "line_start, line_end, source_code, annotations, params, "
                    "return_type, framework_hints, domain, file_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scan_id,
                        node.file_path,
                        node.language,
                        node.node_type,
                        node.name,
                        node.qualified_name,
                        node.line_start,
                        node.line_end,
                        node.source_code,
                        json.dumps(node.annotations),
                        json.dumps(node.params),
                        node.return_type,
                        # M3.4: parse-time base types ride along, so graphs rebuilt from the DB (pages,
                        # estimate, rehydrate) see the same base-type contracts as the live scan
                        json.dumps({**node.framework_hints, "bases": list(node.bases)} if node.bases
                                   else node.framework_hints),
                        node.domain,
                        node.file_hash,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    console.print(f"  Persisted [green]{len(all_nodes)}[/] code nodes")


def rehydrate(ctx: RunContext) -> None:
    from ..pipeline import _load_domains_from_db

    ctx.domains_dict = _load_domains_from_db(ctx.db_path, ctx.scan_id, ctx.all_nodes)
    ctx.domains = list(ctx.domains_dict.values())
