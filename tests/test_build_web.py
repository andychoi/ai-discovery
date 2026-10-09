"""SPA build guard: no external resource loads (roadmap item M4.2).

The full `npm` build is exercised by `python scripts/build_web.py`; these tests
cover the offline check itself and, when a build is present, that the server
serves it.
"""

from __future__ import annotations

import importlib.util
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_web", ROOT / "scripts" / "build_web.py")
build_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_web)


def _write(root: Path, name: str, text: str) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("name,text", [
    ("index.html", '<script src="https://cdn.example.com/x.js"></script>'),
    ("index.html", "<link href='//fonts.googleapis.com/css?family=Inter' rel=stylesheet>"),
    ("assets/a.css", "@font-face{src:url(https://fonts.gstatic.com/x.woff2)}"),
    ("assets/a.css", '@import "https://cdn.example.com/theme.css";'),
    ("assets/a.js", 'const m = await import("https://esm.sh/mermaid");'),
    ("assets/a.js", 'import x from "https://unpkg.com/x";'),
])
def test_external_loads_are_flagged(tmp_path: Path, name: str, text: str) -> None:
    _write(tmp_path, name, text)
    assert build_web.find_external_loads(tmp_path), text


@pytest.mark.parametrize("name,text", [
    ("assets/a.js", 'const ns = "http://www.w3.org/2000/svg"; // see https://mermaid.js.org/docs'),
    ("index.html", '<script type="module" src="/assets/index.js"></script>'),
    ("assets/a.css", "src:url(/assets/inter.woff2)"),
    ("assets/a.js", 'fetch("http://localhost:7341/api")'),
])
def test_local_references_and_plain_strings_pass(tmp_path: Path, name: str, text: str) -> None:
    _write(tmp_path, name, text)
    assert build_web.find_external_loads(tmp_path) == []


STATIC = ROOT / "src" / "ai_discovery" / "server" / "static"


@pytest.mark.skipif(not (STATIC / "index.html").exists(), reason="SPA not built (python scripts/build_web.py)")
def test_built_spa_is_offline_and_served(tmp_path: Path) -> None:
    assert build_web.find_external_loads(STATIC) == []
    warnings.filterwarnings("ignore", message=".*httpx.*")
    from fastapi.testclient import TestClient

    from ai_discovery.core.paths import ProjectLayout
    from ai_discovery.server import create_app

    client = TestClient(create_app(ProjectLayout.for_repo(tmp_path)))
    html = client.get("/").text
    assert '<div id="root"></div>' in html
    asset = next(p.name for p in (STATIC / "assets").glob("index-*.js"))
    assert client.get(f"/assets/{asset}").status_code == 200
