"""Stage ``parse``: parse files + OpenAPI/GraphQL/proto contracts (pipeline phase 6), moved verbatim (M1.6)."""

from __future__ import annotations

from .context import RunContext


def run(ctx: RunContext) -> None:
    from ..pipeline import _stamp_file_hash, _timed, _with_checkpoint, console, logger
    from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
    from ..repo.file_walker import walk_repo
    from ..parsers.python_parser import PythonParser
    from ..parsers.csharp import CSharpParser
    from ..parsers.java import JavaParser
    from ..parsers.javascript import JavaScriptParser
    from ..parsers.webforms import WebFormsParser
    from ..parsers.jsp import JspParser

    resolved, config, lang_stats = ctx.resolved, ctx.config, ctx.lang_stats

    with _with_checkpoint(ctx.db_path, ctx.scan_id, 6, "parse"):
        from ..parsers.import_map import IMPORT_MAP_PARSERS

        parsers = [PythonParser(), CSharpParser(), JavaParser(), JavaScriptParser(), WebFormsParser(), JspParser()]
        # A-6 import-map tier: Go/Rust/Ruby/PHP via lightweight extractors.
        parsers += [cls() for cls in IMPORT_MAP_PARSERS]
        all_nodes: list = []
        parse_errors = 0

        def _parse_one(file_path):
            """Parse a single file with the first matching parser. Returns
            (nodes, error_exc) — exc is None on success."""
            for p in parsers:
                if p.can_parse(file_path):
                    try:
                        nodes = p.parse_file(file_path)
                        _stamp_file_hash(nodes, file_path)  # A-2: cross-scan identity
                        return nodes, None
                    except Exception as exc:
                        return [], (file_path, exc)
            return [], None

        with _timed("parse"), Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]Parsing files..."),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            from ..core.encoding import reset_fallback_stats
            reset_fallback_stats()  # M0.4: per-scan fallback count
            files = list(walk_repo(resolved.repo_path, set(lang_stats.keys())))
            task = progress.add_task("Parsing", total=len(files))

            # tree-sitter's C-level parse releases the GIL, so threads give
            # real parallelism here. Bounded by max_concurrent so we don't
            # starve the main thread or OS file-descriptor limits.
            from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

            workers = max(1, int(config.max_concurrent or 1))
            with ThreadPoolExecutor(max_workers=workers) as parse_pool:
                futures = {parse_pool.submit(_parse_one, f): f for f in files}
                for fut in _as_completed(futures):
                    try:
                        nodes, err = fut.result()
                    except Exception as exc:  # shouldn't happen — _parse_one catches
                        logger.warning("Parse future crashed: %s", exc)
                        parse_errors += 1
                        progress.advance(task)
                        continue
                    if err is not None:
                        fpath, exc = err
                        logger.warning("Parse error: %s: %s", fpath, exc)
                        parse_errors += 1
                    else:
                        all_nodes.extend(nodes)
                    progress.advance(task)

        console.print(
            f"  Parsed [green]{len(all_nodes)}[/] code nodes from "
            f"[green]{len(files)}[/] files"
            + (f"  ([yellow]{parse_errors} errors[/])" if parse_errors else "")
        )
        # M0.4: report files decoded via a legacy/lossy fallback (never dropped).
        from ..core.encoding import fallback_stats
        _enc_fallbacks = fallback_stats()
        if _enc_fallbacks:
            _enc_summary = ", ".join(f"{n} {enc}" for enc, n in sorted(_enc_fallbacks.items()))
            console.print(f"  Encoding fallback: [yellow]{_enc_summary}[/] file(s)")
            logger.info("Encoding fallback decodes: %s", _enc_fallbacks)

        # HIGH-7: ingest OpenAPI/Swagger specs as an authoritative endpoint
        # source. Spec endpoints flow into the same verified-facts API table
        # as parser endpoints; dedup against AST endpoints by (method, route)
        # so a route declared in both isn't double-counted (AST wins — it has
        # real source line numbers).
        from ..extractors import read_openapi_files
        openapi_nodes = read_openapi_files(resolved.repo_path)
        if openapi_nodes:
            ast_routes = {
                (n.framework_hints.get("method"), n.framework_hints.get("route"))
                for n in all_nodes if n.node_type == "endpoint"
            }
            fresh = [
                n for n in openapi_nodes
                if (n.framework_hints.get("method"), n.framework_hints.get("route")) not in ast_routes
            ]
            all_nodes.extend(fresh)
            console.print(
                f"  OpenAPI: [green]{len(openapi_nodes)}[/] spec endpoints "
                f"([green]{len(fresh)}[/] new beyond AST)"
            )

        # HIGH-7: GraphQL SDL + gRPC/proto contracts — object types/messages
        # become entities, root operations/RPCs become endpoints. Deduped by
        # identity (entities) and (method, route) (endpoints).
        from ..extractors import read_graphql_files, read_proto_files
        contract_nodes = read_graphql_files(resolved.repo_path) + read_proto_files(resolved.repo_path)
        if contract_nodes:
            existing_qns = {n.qualified_name for n in all_nodes}
            ep_routes = {
                (n.framework_hints.get("method"), n.framework_hints.get("route"))
                for n in all_nodes if n.node_type == "endpoint"
            }
            fresh = []
            for n in contract_nodes:
                if n.qualified_name in existing_qns:
                    continue
                if n.node_type == "endpoint" and (
                    n.framework_hints.get("method"), n.framework_hints.get("route")
                ) in ep_routes:
                    continue
                fresh.append(n)
                existing_qns.add(n.qualified_name)
            all_nodes.extend(fresh)
            n_ent = sum(1 for n in fresh if n.node_type == "db_model")
            n_ep = sum(1 for n in fresh if n.node_type == "endpoint")
            console.print(
                f"  Contracts (GraphQL/proto): [green]{n_ent}[/] entities, "
                f"[green]{n_ep}[/] operations"
            )
    ctx.all_nodes = all_nodes


def rehydrate(ctx: RunContext) -> None:
    from ..pipeline import _load_code_nodes_from_db, console

    console.print("[dim]Phase 6 (parse): loading from DB...[/]")
    ctx.all_nodes = _load_code_nodes_from_db(ctx.db_path, ctx.scan_id)
    console.print(f"  Loaded [green]{len(ctx.all_nodes)}[/] code nodes from DB")


def execute(ctx: RunContext) -> None:
    (run if ctx.phase_runs(6) else rehydrate)(ctx)
