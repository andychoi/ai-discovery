"""`discover serve` — the wiki: API v1 + bundled SPA on one port (roadmap item M4.5).

Binds 127.0.0.1 by default. A non-loopback ``--host`` is refused unless
``--allow-remote`` is given; then a warning and the bearer token for note
writes are printed (``server.check_bind``). Serving works before the first
scan too — the SPA shows its "No index yet" state.
"""

from __future__ import annotations

import errno
import socket
from typing import Optional

import typer

from ...core.errors import DiscoveryError
from ..context import get_globals
from ..output import emit, handle_errors, stderr_console
from ._index import find_index

DEFAULT_PORT = 7341


def _url(host: str, port: int) -> str:
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else ("localhost" if host == "::" else host)
    return f"http://[{shown}]:{port}" if ":" in shown else f"http://{shown}:{port}"


def _claim_port(host: str, port: int) -> int:
    """Fail early with a reasoned error if *port* is taken; port 0 picks a free one."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn does
        try:
            s.bind((host, port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise DiscoveryError(f"port {port} on {host} is already in use", "port_in_use",
                                     hint=f"pass another --port (e.g. --port {port + 1})") from exc
            raise DiscoveryError(f"cannot bind {host}:{port}: {exc.strerror or exc}", "failure") from exc
        return s.getsockname()[1]


def _log_level(verbosity: int, quiet: bool) -> str:
    if quiet:
        return "error"
    return {0: "warning", 1: "info"}.get(verbosity, "debug")


def register(app: typer.Typer) -> None:
    @app.command("serve")
    @handle_errors
    def serve(
        ctx: typer.Context,
        host: str = typer.Option("127.0.0.1", "--host", help="Address to bind (non-loopback needs --allow-remote)."),
        port: int = typer.Option(DEFAULT_PORT, "--port", min=0, max=65535, help="Port for API + UI (0 = any free)."),
        open_browser: bool = typer.Option(False, "--open/--no-open", help="Open the wiki in a browser."),
        allow_remote: bool = typer.Option(False, "--allow-remote",
                                          help="Allow binding a non-loopback address and remote clients."),
        token: Optional[str] = typer.Option(None, "--token", envvar="DISCOVER_SERVE_TOKEN",
                                            help="Bearer token for note writes with --allow-remote (default: random)."),
    ) -> None:
        """Serve the project wiki (API v1 + web UI) on one local port."""
        from ...server import BindRefused, check_bind

        g = get_globals(ctx)
        try:
            warning = check_bind(host, allow_remote)
        except BindRefused as exc:
            raise DiscoveryError(str(exc), "serve_remote_not_allowed",
                                 hint="drop --host, or add --allow-remote to expose the wiki deliberately") from exc
        port = _claim_port(host, port)

        import uvicorn

        from ...server import create_app
        from ...server.app import STATIC_DIR

        index, layout = find_index(g)
        app_ = create_app(layout, allow_remote=allow_remote, token=token)
        url = _url(host, port)
        notes: list[str] = []
        if index is None:
            notes.append("no index yet — run `discover scan`; the wiki shows an empty state until then")
        elif index.kind == "legacy":
            notes.append("only a legacy index exists — run `discover scan` to build the v1 wiki")
        if not (STATIC_DIR / "index.html").is_file():
            notes.append("web UI not bundled in this install (API only; see /api/docs)")

        result = {"url": url, "host": host, "port": port, "remote": allow_remote,
                  "indexed": index is not None and index.kind == "v1", "notes": notes}
        if allow_remote:
            result["token"] = app_.state.token

        def human(console) -> None:
            if warning:
                console.print(f"[yellow]{warning}[/]", highlight=False)
                console.print(f"  token: {app_.state.token}", highlight=False)
            for n in notes:
                console.print(f"[yellow]![/] {n}", highlight=False)
            console.print(f"Serving wiki at [bold]{url}[/]  (Ctrl+C to stop)", highlight=False)

        emit(g, result, human)
        if open_browser:
            import threading
            import webbrowser

            threading.Timer(1.0, webbrowser.open, args=(url,)).start()
        try:
            uvicorn.run(app_, host=host, port=port, log_level=_log_level(g.verbosity, g.quiet))
        except KeyboardInterrupt:
            stderr_console(g).print("stopped")
