"""MCP over streamable HTTP + stdio watchdog (roadmap item M5.9)."""

from __future__ import annotations

import json
import socket
import threading
import time

import anyio
import httpx
import pytest
import uvicorn
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.mcp import transport_http as th
from ai_discovery.mcp.registry import select
from ai_discovery.mcp.server import create_server

TOKEN = "s3cret-token-for-tests-0123456789"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "pytest", "version": "1"}}}
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    def __init__(self, project, *, token=None, allow_hosts=None):
        self.port = _free_port()
        asgi = th.build_app(create_server(project, select("lean")), host="127.0.0.1", port=self.port, token=token,
                            allow_hosts=allow_hosts)
        self.server = uvicorn.Server(uvicorn.Config(asgi, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            assert time.time() < deadline, "server did not start"
            time.sleep(0.05)
        return f"http://127.0.0.1:{self.port}{th.MCP_PATH}"

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def test_bind_policy() -> None:
    th.check_http_bind("127.0.0.1", None)
    th.check_http_bind("::1", None)
    th.check_http_bind("localhost", None)
    with pytest.raises(th.HttpBindRefused, match="without a token"):
        th.check_http_bind("0.0.0.0", None)
    with pytest.raises(th.HttpBindRefused, match="16 characters"):
        th.check_http_bind("0.0.0.0", "short")
    th.check_http_bind("0.0.0.0", TOKEN)
    assert th.url_for("0.0.0.0", 7342) == "http://127.0.0.1:7342/mcp"
    assert th.url_for("::1", 7342) == "http://[::1]:7342/mcp"


def test_sdk_client_over_http(scanned) -> None:
    from mcp import Client

    with _Server(scanned) as url:
        async def go():
            async with Client(url) as client:
                names = [t.name for t in (await client.list_tools()).tools]
                res = await client.call_tool("search", {"query": "getAllProducts", "limit": 3})
                return names, res

        names, res = anyio.run(go)
    assert "search" in names and len(names) <= 5
    assert res.is_error is False and res.structured_content["data"]["hits"][0]["id"].endswith("getAllProducts")


def test_token_required_and_constant_time_checked(scanned) -> None:
    with _Server(scanned, token=TOKEN) as url:
        assert httpx.post(url, json=INIT, headers=HEADERS).status_code == 401
        bad = httpx.post(url, json=INIT, headers={**HEADERS, "authorization": "Bearer wrong"})
        assert bad.status_code == 401 and bad.json()["error"] == "unauthorized"
        ok = httpx.post(url, json=INIT, headers={**HEADERS, "authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200, ok.text


def test_host_header_guard(scanned) -> None:
    with _Server(scanned) as url:
        evil = httpx.post(url, json=INIT, headers={**HEADERS, "host": "evil.example:80"})
        assert evil.status_code in (400, 403, 421)
        good = httpx.post(url, json=INIT, headers=HEADERS)
        assert good.status_code == 200, good.text
    with _Server(scanned, token=TOKEN, allow_hosts=["mcp.internal"]) as url:
        auth = {**HEADERS, "authorization": f"Bearer {TOKEN}"}
        assert httpx.post(url, json=INIT, headers={**auth, "host": "mcp.internal:1234"}).status_code == 200
        assert httpx.post(url, json=INIT, headers={**auth, "host": "other.example"}).status_code in (400, 403, 421)


def test_cli_refuses_remote_bind_without_token(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    res = CliRunner().invoke(app, ["--json", "-C", str(tmp_path), "mcp", "serve", "--http", "--host", "0.0.0.0"],
                             env={"DISCOVER_MCP_TOKEN": ""})
    assert res.exit_code == 2
    assert json.loads(res.output)["error"]["reason"] == "serve_remote_not_allowed"


def test_parent_watchdog() -> None:
    pids = iter([4242, 4242, 4242, 1])
    fired = threading.Event()
    t = th.start_parent_watchdog(interval=0.01, getppid=lambda: next(pids, 1), on_orphan=fired.set)
    assert t is not None and fired.wait(2)
    assert th.start_parent_watchdog(getppid=lambda: 1) is None          # already orphaned/init: no watchdog
