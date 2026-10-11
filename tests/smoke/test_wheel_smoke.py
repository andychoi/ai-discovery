"""Install-from-wheel smoke: keyless init → scan → serve with no Node on PATH (M4.5).

Builds the wheel (the SPA must already be built by ``scripts/build_web.py``),
installs it into a fresh venv, and drives the installed ``discover`` against a
copy of the spring-boot fixture. Needs ``uv`` and ``git``.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "projects" / "spring-boot-app"
STATIC_INDEX = ROOT / "src" / "ai_discovery" / "server" / "static" / "index.html"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}

pytestmark = pytest.mark.slow


def _without_node(path: str) -> str:
    """PATH minus every directory that provides node or npm."""
    keep = [d for d in path.split(os.pathsep)
            if d and not any((Path(d) / exe).exists() for exe in ("node", "npm", "node.exe", "npm.cmd"))]
    return os.pathsep.join(keep)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 — loopback only
        return r.status, r.read().decode("utf-8")


def test_wheel_init_scan_serve_without_node(tmp_path: Path) -> None:
    uv, git = shutil.which("uv"), shutil.which("git")
    if not uv or not git:
        pytest.skip("needs uv and git on PATH")
    if not STATIC_INDEX.is_file():
        pytest.skip("SPA not built: run `python scripts/build_web.py` first")

    dist = tmp_path / "dist"
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(dist), str(ROOT)], check=True,
                   capture_output=True)
    wheel = next(dist.glob("ai_discovery-*.whl"))
    venv = tmp_path / "venv"
    subprocess.run([uv, "venv", "-q", "--python", sys.executable, str(venv)], check=True)
    bindir = venv / ("Scripts" if os.name == "nt" else "bin")
    py = bindir / ("python.exe" if os.name == "nt" else "python")
    subprocess.run([uv, "pip", "install", "-q", "--python", str(py), str(wheel)], check=True)

    env = {**os.environ, **GIT_ENV, "PATH": os.pathsep.join([str(bindir), _without_node(os.environ["PATH"])])}
    env.pop("VIRTUAL_ENV", None)
    assert shutil.which("node", path=env["PATH"]) is None and shutil.which("npm", path=env["PATH"]) is None
    discover = shutil.which("discover", path=env["PATH"])
    assert discover and Path(discover).parent == bindir

    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".gitignore").write_text(".discovery/\n")
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        subprocess.run([git, *cmd], cwd=repo, env=env, check=True)

    def run(*args: str) -> subprocess.CompletedProcess:
        res = subprocess.run([discover, "-C", str(repo), *args], env=env, capture_output=True, text=True,
                             timeout=300)
        assert res.returncode == 0, f"discover {' '.join(args)} failed:\n{res.stdout}\n{res.stderr}"
        return res

    run("init", "--yes", "--no-prose", "--no-mcp")
    run("-q", "scan")
    status = json.loads(run("--json", "status").stdout)
    assert status["ok"]

    port = _free_port()
    proc = subprocess.Popen([discover, "-C", str(repo), "serve", "--port", str(port)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 60
        while True:
            try:
                code, body = _get(f"{base}/api/v1/status")
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    out = proc.stdout.read() if proc.stdout else ""
                    pytest.fail(f"serve did not come up (exit={proc.poll()}):\n{out}")
                time.sleep(0.3)
        assert code == 200 and json.loads(body)["status"] == "ok"
        code, html = _get(f"{base}/")
        assert code == 200 and '<div id="root">' in html and "/assets/" in html
        asset = html.split('src="', 1)[1].split('"', 1)[0]
        assert _get(f"{base}{asset}")[0] == 200
        code, page = _get(f"{base}/api/v1/pages/table%3Aorders")
        assert code == 200 and json.loads(page)["data"]["type"] == "table"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
