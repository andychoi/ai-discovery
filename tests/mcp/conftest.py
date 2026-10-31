"""Shared fixtures for the MCP server tests (roadmap item M5.3)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import anyio
import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app as cli_app
from ai_discovery.query import Project
from tests.golden.test_golden import _GIT_ENV

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="session")
def scanned(tmp_path_factory) -> Project:
    """The spring-boot fixture, git-committed and scanned keyless."""
    repo = tmp_path_factory.mktemp("mcp") / "repo"
    shutil.copytree(FIXTURES / "projects" / "spring-boot-app", repo)
    (repo / ".gitignore").write_text(".discovery/\n")
    env = {**os.environ, **_GIT_ENV}
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "i"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True)
    res = CliRunner().invoke(cli_app, ["-q", "-C", str(repo), "scan"])
    assert res.exit_code == 0, res.output
    return Project.open(repo)


def call(project: Project, name: str, args: Optional[dict] = None, *, tools: str = "all",
         max_tokens: Optional[int] = None) -> dict[str, Any]:
    """One tool call through the real SDK, in process. Returns the structured answer (and checks isError)."""
    from mcp import Client

    from ai_discovery.mcp.registry import select
    from ai_discovery.mcp.server import create_server

    server = create_server(project, select(tools), max_tokens=max_tokens)

    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, args or {})

    result = anyio.run(go)
    assert result.is_error is False
    return result.structured_content


def list_tools(project: Project, tools: str = "default") -> list:
    from mcp import Client

    from ai_discovery.mcp.registry import select
    from ai_discovery.mcp.server import create_server

    async def go():
        async with Client(create_server(project, select(tools))) as client:
            return (await client.list_tools()).tools

    return anyio.run(go)
