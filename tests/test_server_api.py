"""Wiki web server: API v1 + exposure rules (roadmap item M4.1)."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import warnings
from pathlib import Path

import pytest
from typer.testing import CliRunner

warnings.filterwarnings("ignore", message=".*httpx.*", category=DeprecationWarning)
from fastapi.testclient import TestClient  # noqa: E402

from ai_discovery.cli import app as cli_app  # noqa: E402
from ai_discovery.core.paths import ProjectLayout  # noqa: E402
from ai_discovery.server import BindRefused, check_bind, create_app  # noqa: E402
from tests.golden.test_golden import _GIT_ENV  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ENVELOPE = {"status", "data", "totals", "truncated", "omitted", "meta"}


@pytest.fixture(scope="module")
def layout(tmp_path_factory) -> ProjectLayout:
    repo = tmp_path_factory.mktemp("srv") / "repo"
    shutil.copytree(FIXTURES / "projects" / "spring-boot-app", repo)
    (repo / ".gitignore").write_text(".discovery/\n")
    env = {**os.environ, **_GIT_ENV}
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "i"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True)
    res = CliRunner().invoke(cli_app, ["-q", "-C", str(repo), "scan"])
    assert res.exit_code == 0, res.output
    return ProjectLayout.for_repo(repo)


@pytest.fixture(scope="module")
def client(layout) -> TestClient:
    return TestClient(create_app(layout))


def _get(client, url: str, status: str = "ok") -> dict:
    r = client.get(url)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == ENVELOPE and body["status"] == status, body
    return body


# ── read endpoints ────────────────────────────────────────────────────────

def test_status(client) -> None:
    body = _get(client, "/api/v1/status")
    assert body["data"]["scans"][0]["status"] == "completed" and body["data"]["pages"] > 0
    meta = body["meta"]
    assert meta["contract_version"] == "1" and meta["behind_by"] == 0 and meta["dirty"] is False
    assert meta["stale_pages"] == 0 and meta["scan_id"] == body["data"]["scans"][0]["scan_id"]


def test_pages_filter_and_paging(client) -> None:
    tables = _get(client, "/api/v1/pages?type=table")
    assert tables["totals"]["items"] == len(tables["data"]["items"]) == 4
    page1 = _get(client, "/api/v1/pages?limit=5")
    assert len(page1["data"]["items"]) == 5 and page1["truncated"] is True
    page2 = _get(client, "/api/v1/pages?limit=5&offset=5")
    assert not {i["page_id"] for i in page1["data"]["items"]} & {i["page_id"] for i in page2["data"]["items"]}
    pre = _get(client, "/api/v1/pages?prefix=screen:orders")
    assert pre["data"]["items"] and all(i["page_id"].startswith("screen:orders") for i in pre["data"]["items"])
    assert client.get("/api/v1/pages?limit=5000").status_code == 422


def test_page_detail(client) -> None:
    body = _get(client, "/api/v1/pages/table:orders")["data"]
    assert body["type"] == "table" and "## Columns" in body["body_md"]
    assert {b["src"] for b in body["backlinks"]} >= {"screen:orders-list"}
    assert body["citations"] and body["freshness"] == "fresh" and body["stamp"]["builder"] == "business.tables"


def test_missing_and_tombstoned_pages(client, layout) -> None:
    assert _get(client, "/api/v1/pages/file:nope.java", "not_found")["data"] == {"page_id": "file:nope.java"}
    from ai_discovery.db import get_conn
    from ai_discovery.pages.notes import record_alias

    conn = get_conn(layout.db_path)
    record_alias(conn, "file:src/Old.java", "module:src")
    conn.commit()
    conn.close()
    assert _get(client, "/api/v1/pages/file:src/Old.java", "not_found")["data"]["successor"] == "module:src"


def test_tree_nests_files_under_modules(client) -> None:
    data = _get(client, "/api/v1/tree")["data"]
    assert {"module", "screen", "table", "endpoint"} <= set(data)
    assert "symbol" not in data

    def walk(nodes):
        for n in nodes:
            yield n
            yield from walk(n["children"])
    all_nodes = list(walk(data["module"]))
    assert any(n["page_id"].startswith("file:") for n in all_nodes)
    assert all(n["page_id"].startswith("module:") for n in data["module"])  # roots are modules


def test_search(client) -> None:
    hits = _get(client, "/api/v1/search?q=orders")["data"]["hits"]
    assert "table:orders" in {h["page_id"] for h in hits}
    tables_only = _get(client, "/api/v1/search?q=orders&kinds=table")["data"]["hits"]
    assert tables_only and all(h["type"] == "table" for h in tables_only)
    # FTS syntax in user input is neutralised, not an error
    _get(client, '/api/v1/search?q=orders" OR (NEAR')
    assert _get(client, "/api/v1/search?q=%20%21%21")["data"]["hits"] == []


def test_graph_neighborhood_and_caps(client) -> None:
    node = "com.example.controller.OrderController.create"
    full = _get(client, f"/api/v1/graph/neighborhood?node={node}&depth=2")
    assert full["data"]["nodes"][0]["label"] == node and full["data"]["edges"]
    assert full["data"]["nodes"][0]["file"].startswith("src/")  # repo-relative
    capped = _get(client, f"/api/v1/graph/neighborhood?node={node}&depth=2&limit=2")
    assert len(capped["data"]["nodes"]) == 2 and capped["totals"]["nodes"] == full["totals"]["nodes"]
    assert capped["truncated"] is (full["totals"]["nodes"] > 2)
    assert _get(client, "/api/v1/graph/neighborhood?node=nope", "not_found")
    assert client.get(f"/api/v1/graph/neighborhood?node={node}&depth=3").status_code == 422


def test_graph_path(client) -> None:
    body = _get(client, "/api/v1/graph/neighborhood?node=com.example.controller.OrderController.create")
    callee = next(e["dst"] for e in body["data"]["edges"] if e["src"] == body["data"]["focus"])
    path = _get(client, f"/api/v1/graph/path?from=com.example.controller.OrderController.create&to={callee}")
    assert path["data"]["paths"] and path["data"]["paths"][0][-1] == callee
    assert _get(client, "/api/v1/graph/path?from=nope&to=x", "not_found")


def test_screens_and_business_views(client) -> None:
    menu = _get(client, "/api/v1/screens")
    assert menu["totals"]["screens"] == 11
    labels = [c["label"] for c in menu["data"]["children"]]
    assert "Catalog Management" in labels
    s = _get(client, "/api/v1/screens/products-list")["data"]
    assert s["label"] == "Products" and "products" in s["chain"]["tables"]
    _get(client, "/api/v1/screens/nope", "not_found")
    t = _get(client, "/api/v1/tables/orders")["data"]
    assert "status" in t["columns"] and t["relationships"]
    _get(client, "/api/v1/tables/nope", "not_found")
    _get(client, "/api/v1/entities/nope", "not_found")


def test_not_indexed(tmp_path: Path) -> None:
    empty = ProjectLayout.for_repo(tmp_path)
    body = _get(TestClient(create_app(empty)), "/api/v1/pages", "not_indexed")
    assert "discover scan" in body["data"]["hint"]


# ── writes and exposure ───────────────────────────────────────────────────

def test_notes_round_trip(client) -> None:
    r = client.post("/api/v1/pages/table:orders/notes", json={"author": "andy", "body": "owned by billing"})
    assert r.status_code == 200 and r.json()["data"]["note_id"]
    notes = _get(client, "/api/v1/pages/table:orders")["data"]["notes"]
    assert [n["body"] for n in notes][-1] == "owned by billing"
    assert client.post("/api/v1/pages/not-an-id/notes", json={"body": "x"}).status_code == 422
    assert client.post("/api/v1/pages/table:orders/notes", json={"body": ""}).status_code == 422


def test_remote_clients_refused_by_default(layout) -> None:
    remote = TestClient(create_app(layout), client=("10.1.2.3", 5555))
    r = remote.get("/api/v1/status")
    assert r.status_code == 403 and r.json()["data"]["reason"] == "serve_remote_not_allowed"


def test_allow_remote_requires_token_for_writes(layout) -> None:
    app = create_app(layout, allow_remote=True, token="s3cret")
    remote = TestClient(app, client=("10.1.2.3", 5555))
    assert remote.get("/api/v1/status").status_code == 200
    assert remote.post("/api/v1/pages/table:orders/notes", json={"body": "x"}).status_code == 401
    ok = remote.post("/api/v1/pages/table:orders/notes", json={"body": "x"},
                     headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_check_bind() -> None:
    assert check_bind("127.0.0.1", False) is None and check_bind("::1", False) is None
    with pytest.raises(BindRefused, match="--allow-remote"):
        check_bind("0.0.0.0", False)
    assert "WARNING" in check_bind("0.0.0.0", True)


# ── static SPA + traversal ────────────────────────────────────────────────

def test_spa_routes_and_path_traversal(layout, tmp_path: Path) -> None:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><div id=app>SPA</div>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    c = TestClient(create_app(layout, static_dir=static))
    assert "SPA" in c.get("/").text and "SPA" in c.get("/pages/table:orders").text  # client-side routes
    assert c.get("/assets/app.js").text == "console.log(1)"
    for evil in ("/assets/../../secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        r = c.get(evil)
        assert "TOP SECRET" not in r.text, evil
    assert c.get("/api/v1/nope").status_code == 404


def test_placeholder_without_spa(layout, tmp_path: Path) -> None:
    c = TestClient(create_app(layout, static_dir=tmp_path / "missing"))
    assert "not built yet" in c.get("/").text and c.get("/api/openapi.json").status_code == 200
