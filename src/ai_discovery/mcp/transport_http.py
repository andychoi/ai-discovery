"""Streamable-HTTP transport and stdio watchdog (roadmap item M5.9).

Policy (stricter than a warning):

- **Loopback by default.** ``127.0.0.1`` / ``::1`` / ``localhost`` binds need no
  token; the SDK's DNS-rebinding guard limits the ``Host`` header to loopback names.
- **Off loopback, a token is mandatory.** The server refuses to start without
  ``--token`` / ``$DISCOVER_MCP_TOKEN``; every request must carry
  ``Authorization: Bearer <token>`` (constant-time compare). ``--allow-host``
  pins accepted ``Host`` headers; without it the token alone gates access.
- A token given on loopback is enforced too.

Same tools, middleware, budgets and ledger as stdio: only the transport differs.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

TOKEN_ENV = "DISCOVER_MCP_TOKEN"
DEFAULT_PORT = 7342
MCP_PATH = "/mcp"
_LOOPBACK_NAMES = {"localhost"}


class HttpBindRefused(ValueError):
    pass


def is_loopback(host: str) -> bool:
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def check_http_bind(host: str, token: Optional[str]) -> None:
    """Raise ``HttpBindRefused`` for a non-loopback bind without a token."""
    if not is_loopback(host) and not (token and token.strip()):
        raise HttpBindRefused(
            f"refusing to serve MCP on {host} without a token: anyone who can reach the port could read the "
            f"index of this repository")
    if token is not None and len(token.strip()) < 16:
        raise HttpBindRefused("the MCP token must be at least 16 characters")


class BearerAuth:
    """Pure-ASGI middleware: 401 unless ``Authorization: Bearer <token>`` matches (constant time)."""

    def __init__(self, app: Any, token: str):
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        got = b""
        for name, value in scope.get("headers") or ():
            if name.lower() == b"authorization":
                got = value
                break
        if not hmac.compare_digest(got, self._expected):
            body = json.dumps({"error": "unauthorized", "remedy": "send Authorization: Bearer <token>"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_app(server: Any, *, host: str, port: int, token: Optional[str] = None,
              allow_hosts: Optional[list[str]] = None) -> Any:
    """ASGI app: SDK streamable-HTTP app (+ host allowlist) wrapped in bearer auth when a token is set."""
    from mcp.server.transport_security import TransportSecuritySettings

    check_http_bind(host, token)
    if allow_hosts:
        hosts = [h if re.search(r":(\d+|\*)$", h) else f"{h}:*" for h in allow_hosts]   # IPv6 in [brackets]
        security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                             allowed_origins=[f"http://{h}" for h in hosts] +
                                                             [f"https://{h}" for h in hosts])
    elif is_loopback(host):
        security = None   # SDK default: loopback Host/Origin only
    else:
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)   # token gates access
    app = server.streamable_http_app(streamable_http_path=MCP_PATH, host=host, transport_security=security)
    return BearerAuth(app, token) if token else app


def url_for(host: str, port: int) -> str:
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    return f"http://[{shown}]:{port}{MCP_PATH}" if ":" in shown else f"http://{shown}:{port}{MCP_PATH}"


def run_http(server: Any, *, host: str, port: int, token: Optional[str] = None,
             allow_hosts: Optional[list[str]] = None, log_level: str = "warning") -> None:
    import uvicorn

    app = build_app(server, host=host, port=port, token=token, allow_hosts=allow_hosts)
    uvicorn.run(app, host=host, port=port, log_level=log_level)


# ── stdio parent watchdog ─────────────────────────────────────────────────

def start_parent_watchdog(*, interval: float = 2.0, getppid: Callable[[], int] = os.getppid,
                          on_orphan: Optional[Callable[[], None]] = None) -> Optional[threading.Thread]:
    """Exit when the process that launched us (the agent) goes away, so stdio servers never linger.

    POSIX only (the parent pid changes to the reaper when the parent dies). Returns the thread, or None.
    """
    if os.name != "posix":
        return None
    original = getppid()
    if original <= 1:
        return None

    def orphaned() -> None:
        log.info("parent process %s exited; stopping MCP server", original)
        os._exit(0)

    action = on_orphan or orphaned

    def watch() -> None:
        while True:
            time.sleep(interval)
            if getppid() != original:
                action()
                return

    t = threading.Thread(target=watch, name="mcp-parent-watchdog", daemon=True)
    t.start()
    return t
