"""`discover serve` (roadmap item M4.5) — fast tests; uvicorn is never started."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app

runner = CliRunner()


@pytest.fixture
def captured(monkeypatch) -> dict:
    import uvicorn

    calls: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app_, **kw: calls.update(app=app_, **kw))
    return calls


def _json(output: str) -> dict:
    return json.loads(output.strip().splitlines()[-1])


def test_serves_api_and_ui_on_one_port(tmp_path: Path, captured: dict) -> None:
    res = runner.invoke(app, ["--json", "-C", str(tmp_path), "serve", "--port", "0"])
    assert res.exit_code == 0, res.output
    body = _json(res.output)
    assert body["ok"] and body["host"] == "127.0.0.1" and body["port"] > 0
    assert body["url"] == f"http://127.0.0.1:{body['port']}" and body["indexed"] is False
    assert any("discover scan" in n for n in body["notes"]) and "token" not in body
    assert captured["host"] == "127.0.0.1" and captured["port"] == body["port"]
    from fastapi.testclient import TestClient

    c = TestClient(captured["app"])
    assert c.get("/api/v1/status").json()["status"] == "not_indexed"
    assert "oauth2" not in c.get("/docs/oauth2-redirect").text.lower()  # /docs/* is the SPA's, not swagger's


def test_non_loopback_needs_allow_remote(tmp_path: Path, captured: dict) -> None:
    res = runner.invoke(app, ["--json", "-C", str(tmp_path), "serve", "--host", "0.0.0.0"])
    assert res.exit_code == 2 and _json(res.output)["error"]["reason"] == "serve_remote_not_allowed"
    assert not captured  # nothing was started


def test_allow_remote_prints_token(tmp_path: Path, captured: dict) -> None:
    res = runner.invoke(app, ["--json", "-C", str(tmp_path), "serve", "--host", "0.0.0.0", "--port", "0",
                              "--allow-remote", "--token", "s3cret"])
    assert res.exit_code == 0, res.output
    body = _json(res.output)
    assert body["remote"] is True and body["token"] == "s3cret" and captured["app"].state.token == "s3cret"


def test_port_in_use_is_a_reasoned_error(tmp_path: Path, captured: dict) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        res = runner.invoke(app, ["--json", "-C", str(tmp_path), "serve", "--port", str(port)])
    err = _json(res.output)["error"]
    assert res.exit_code == 1 and err["reason"] == "port_in_use" and "--port" in err["hint"]
    assert not captured
