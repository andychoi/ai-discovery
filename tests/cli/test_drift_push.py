"""`discover drift` and `discover push` (roadmap item M2.6)."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.cli.commands.push import doc_id, run_push
from ai_discovery.config import DiscoveryConfig, PushConfig
from ai_discovery.core.errors import DiscoveryError
from ai_discovery.core.paths import ProjectLayout
from tests.golden.test_golden import GOLDEN_REPOS, _build_repo

runner = CliRunner()
SERVICE = "src/main/java/com/example/service/OrderService.java"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])
    res = runner.invoke(app, ["--json", "-C", str(r), "scan"])
    assert res.exit_code == 0, res.output
    return r


def _cli(repo: Path, *args: str):
    return runner.invoke(app, ["--json", "-C", str(repo), *args])


# ── drift ─────────────────────────────────────────────────────────────────────

def test_drift_clean_then_changed_then_deleted(repo: Path) -> None:
    res = _cli(repo, "drift")
    out = json.loads(res.stdout)
    assert res.exit_code == 0 and out["status"] == "clean" and out["checked"] > 0 and out["drifted"] == 0
    (repo / SERVICE).write_text((repo / SERVICE).read_text() + "// edited\n")
    res = _cli(repo, "drift")
    out = json.loads(res.stdout)
    assert res.exit_code == 6 and out["status"] == "drift_detected" and out["drifted"] >= 1
    assert all(p["reason"] == f"changed: {SERVICE}" for p in out["pages"])
    assert any(p["page_id"] == f"file:{SERVICE}" for p in out["pages"])
    (repo / SERVICE).unlink()
    out = json.loads(_cli(repo, "drift").stdout)
    assert any(p["reason"] == f"deleted: {SERVICE}" for p in out["pages"])


def test_drift_screen_filter_and_nothing_to_check(repo: Path) -> None:
    # this fixture's screens come from menu.json with no component mapped: no screen has sources to check
    res = _cli(repo, "drift", "--type", "screen")
    assert res.exit_code == 5 and json.loads(res.stdout)["error"]["reason"] == "nothing_to_check"
    import hashlib

    db = repo / ".discovery" / "discovery.db"
    conn = sqlite3.connect(db)
    digest = hashlib.sha256((repo / SERVICE).read_bytes()).hexdigest()
    conn.execute("UPDATE pages SET source_hashes_json = ? WHERE page_id = 'screen:orders-detail'",
                 (json.dumps({SERVICE: digest}),))
    conn.commit()
    (repo / SERVICE).write_text((repo / SERVICE).read_text() + "// edited\n")
    res = _cli(repo, "drift", "--type", "screen")
    out = json.loads(res.stdout)
    assert res.exit_code == 6 and out["checked"] == 1 and [p["page_id"] for p in out["pages"]] == ["screen:orders-detail"]
    conn.execute("UPDATE pages SET source_hashes_json = '{}'")
    conn.commit()
    conn.close()
    res = _cli(repo, "drift")
    assert res.exit_code == 5 and json.loads(res.stdout)["error"]["reason"] == "nothing_to_check"


def test_drift_not_indexed_is_never_clean(tmp_path: Path) -> None:
    res = _cli(tmp_path, "drift")
    assert res.exit_code == 5 and json.loads(res.stdout)["error"]["reason"] == "not_indexed"


# ── push ──────────────────────────────────────────────────────────────────────

def _cfg(**push) -> SimpleNamespace:
    return SimpleNamespace(push=PushConfig(**push))


def test_push_dry_run_writes_the_export_and_sends_nothing(repo: Path) -> None:
    res = _cli(repo, "push", "--target", "dochub", "--dry-run")
    out = json.loads(res.stdout)
    assert res.exit_code == 0 and out["dry_run"] and out["selected"] > 50 and out["pushed"] == 0
    assert (repo / ".discovery" / "exports" / "markdown" / "overview" / "system.md").is_file()


def test_push_needs_a_configured_url(repo: Path) -> None:
    res = _cli(repo, "push", "--target", "gitea")
    assert res.exit_code == 2 and json.loads(res.stdout)["error"]["reason"] == "config_invalid_value"
    assert "push.gitea_url" in json.loads(res.stdout)["error"]["hint"]


def test_push_dochub_batches_records_and_retries_failures(repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCOVERY_DOCHUB_TOKEN", "k3y")
    layout = ProjectLayout.for_repo(repo)
    seen: list[dict] = []
    reject = doc_id(layout.slug, "overview:system")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "k3y"
        assert request.url.path == f"/api/projects/{layout.slug}/docs/ingest/batch"
        docs = json.loads(request.content)["docs"]
        seen.append({"n": len(docs)})
        return httpx.Response(200, json={
            "results": [{"doc_id": d["doc_id"], "url": f"/docs/{d['doc_id']}"} for d in docs if d["doc_id"] != reject],
            "errors": [{"doc_id": d["doc_id"], "error": "bad front matter"} for d in docs if d["doc_id"] == reject]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = run_push(layout, _cfg(dochub_url="https://dochub.test"), "dochub", batch_size=40, client=client)
    assert out["failed"] == 1 and out["pushed"] == out["selected"] - 1
    assert all(s["n"] <= 40 for s in seen) and len(seen) == -(-out["selected"] // 40)
    assert out["failures"] == [{"page_id": "overview:system", "error": "bad front matter"}]
    conn = sqlite3.connect(layout.db_path)
    rows = dict(conn.execute("SELECT status, COUNT(*) FROM page_push WHERE target = 'dochub' GROUP BY status").fetchall())
    conn.close()
    assert rows == {"pushed": out["pushed"], "failed": 1}
    reject = "none"                                                           # the retry succeeds
    seen.clear()
    again = run_push(layout, _cfg(dochub_url="https://dochub.test"), "dochub", only_failed=True, client=client)
    assert (again["selected"], again["pushed"], again["failed"]) == (1, 1, 0) and seen == [{"n": 1}]


def test_push_auth_refusal_is_push_auth_failed(repo: Path) -> None:
    layout = ProjectLayout.for_repo(repo)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(DiscoveryError) as exc:
        run_push(layout, _cfg(dochub_url="https://dochub.test"), "dochub", client=client)
    assert exc.value.reason == "push_auth_failed" and "DISCOVERY_DOCHUB_TOKEN" in exc.value.hint


def test_push_gitea_creates_and_updates_files(repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCOVERY_GITEA_TOKEN", "gt")
    layout = ProjectLayout.for_repo(repo)
    puts: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "token gt"
        path = request.url.path.split("/contents/", 1)[1]
        assert request.url.path.startswith("/api/v1/repos/docs-org/wiki/contents/projects/")
        if request.method == "GET":
            return httpx.Response(200, json={"sha": "abc"}) if path.endswith("overview/system.md") else httpx.Response(404)
        puts[path] = json.loads(request.content)
        return httpx.Response(201, json={"content": {"html_url": f"https://git.test/{path}"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = run_push(layout, _cfg(gitea_url="https://git.test", gitea_owner="docs-org", gitea_repo="wiki"), "gitea",
                   client=client)
    assert out["failed"] == 0 and out["pushed"] == out["selected"] == len(puts)
    overview = puts[f"projects/{layout.slug}/overview/system.md"]
    assert overview["sha"] == "abc" and base64.b64decode(overview["content"]).decode().startswith("---\npage_id:")
    assert all("sha" not in body for p, body in puts.items() if not p.endswith("overview/system.md"))
