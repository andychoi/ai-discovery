"""Wiki web server: FastAPI app factory (roadmap item M4.1, design/wiki-web.md).

One project per process. Reads use a read-only SQLite connection pinned to
``current_scan``; the only write is ``POST /api/v1/pages/{id}/notes``.

Exposure rules:
* binds 127.0.0.1 by default; a non-loopback ``--host`` needs ``--allow-remote``
  (``check_bind`` refuses otherwise and returns a warning to print when allowed);
* without ``allow_remote`` every request from a non-loopback client is refused
  (defence in depth if the socket is exposed some other way);
* with ``allow_remote`` the notes endpoint requires ``Authorization: Bearer <token>``.

The SPA (M4.2) is served from ``server/static`` when that directory exists.
"""

from __future__ import annotations

import ipaddress
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

STATIC_DIR = Path(__file__).parent / "static"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class BindRefused(ValueError):
    pass


def _is_loopback(host: Optional[str]) -> bool:
    if host is None:
        return False
    if host in LOOPBACK_HOSTS or host == "testclient":  # Starlette TestClient peer
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind(host: str, allow_remote: bool) -> Optional[str]:
    """Validate a bind address. Returns a warning to show, or raises BindRefused."""
    if _is_loopback(host):
        return None
    if not allow_remote:
        raise BindRefused(f"refusing to bind {host}: the wiki would be reachable from other machines; "
                          "pass --allow-remote to do this deliberately")
    return (f"WARNING: serving on {host} — anyone who can reach this address can read the project index. "
            "Note writes require the bearer token printed below.")


def create_app(layout, *, allow_remote: bool = False, token: Optional[str] = None,
               static_dir: Optional[Path] = None) -> FastAPI:
    from .api_v1 import build_router

    app = FastAPI(title="ai-discovery wiki", version="1", docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json",
                  swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect")  # /docs/* is the SPA's
    app.state.layout = layout
    app.state.allow_remote = allow_remote
    app.state.token = token or (secrets.token_urlsafe(24) if allow_remote else None)

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        client = request.client.host if request.client else None
        if not app.state.allow_remote and not _is_loopback(client):
            return JSONResponse({"status": "forbidden", "data": {"reason": "serve_remote_not_allowed"}},
                                status_code=403)
        return await call_next(request)

    app.include_router(build_router(), prefix="/api/v1")

    static = static_dir or STATIC_DIR
    if (static / "index.html").is_file():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=static / "assets", check_dir=False), name="assets")
        index_html = (static / "index.html").read_text(encoding="utf-8")

        @app.get("/{spa_path:path}", include_in_schema=False)
        def spa(spa_path: str):
            # Client-side routes all render the SPA shell; real files only come from /assets.
            if spa_path.startswith("api/"):
                return JSONResponse({"status": "not_found", "data": {}}, status_code=404)
            return HTMLResponse(index_html)
    else:
        @app.get("/", include_in_schema=False)
        def placeholder():
            return HTMLResponse("<!doctype html><title>ai-discovery</title>"
                                "<p>The wiki UI is not built yet (roadmap M4.2/M4.3). "
                                "The API is available at <a href='/api/docs'>/api/docs</a>.</p>")
    return app
