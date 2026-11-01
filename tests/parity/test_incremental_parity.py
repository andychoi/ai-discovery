"""Incremental parity (roadmap item M3.6): pages after incremental scans == pages of a clean full scan.

Each scenario starts from the golden Spring/JSP fixture repo, scans it, then applies scripted commits —
scanning incrementally (the default: same state dir, parse cache warm) after each one. The final tree is
cloned into a fresh directory and scanned from scratch; every page row and page link of the two current
scans must be identical (scan ids aside).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from tests.golden.test_golden import _GIT_ENV, GOLDEN_REPOS, _build_repo

runner = CliRunner()
JAVA = "src/main/java/com/example"
PAGE_COLUMNS = ("page_id", "type", "title", "facts_json", "body_md", "links_json", "citations_json",
                "confidence", "stamp_json", "source_hashes_json", "freshness", "prose_ref")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env={**os.environ, **_GIT_ENV}, check=True, capture_output=True)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _scan(repo: Path) -> dict:
    res = runner.invoke(app, ["--json", "-C", str(repo), "scan", "--no-prose"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out["status"] == "completed", out
    assert all(h["ok"] for h in out["hooks"]), out["hooks"]
    return out


def _pages(repo: Path) -> tuple[dict, list]:
    conn = sqlite3.connect(repo / ".discovery" / "discovery.db")
    try:
        sid = conn.execute("SELECT scan_id FROM current_scan").fetchone()[0]
        rows = {r[0]: dict(zip(PAGE_COLUMNS, r)) for r in conn.execute(
            f"SELECT {', '.join(PAGE_COLUMNS)} FROM pages WHERE scan_id = ? ORDER BY page_id", (sid,))}
        links = conn.execute("SELECT src, dst, kind FROM page_links WHERE scan_id = ? ORDER BY src, dst, kind",
                             (sid,)).fetchall()
    finally:
        conn.close()
    return rows, links


def _assert_parity(incremental: Path, tmp_path: Path) -> None:
    clean = tmp_path / "clean"
    subprocess.run(["git", "clone", "-q", str(incremental), str(clean)], check=True, capture_output=True)
    _scan(clean)
    inc_rows, inc_links = _pages(incremental)
    clean_rows, clean_links = _pages(clean)
    assert sorted(inc_rows) == sorted(clean_rows), "page sets differ"
    diff = {pid: [c for c in PAGE_COLUMNS if inc_rows[pid][c] != clean_rows[pid][c]]
            for pid in inc_rows if inc_rows[pid] != clean_rows[pid]}
    assert diff == {}, f"pages differ (page → columns): {diff}"
    assert inc_links == clean_links


# ── scripted commit sequences ─────────────────────────────────────────────────

def _edit(repo: Path) -> list[Callable[[], str]]:
    svc = repo / JAVA / "service" / "ProductService.java"

    def add_call() -> str:
        text = svc.read_text().replace(
            "        return productRepository.save(product);",
            "        getAllProducts();\n        return productRepository.save(product);")
        svc.write_text(text.rstrip()[:-1] + "\n    public void discontinue(Long id) {\n"
                       "        productRepository.deleteById(id);\n    }\n}\n")
        return "edit: ProductService calls getAllProducts and gains discontinue()"

    def edit_again() -> str:
        svc.write_text(svc.read_text().replace("deleteById(id)", "delete(getProduct(id))"))
        return "edit: discontinue() looks the product up first"
    return [add_call, edit_again]


def _rename(repo: Path) -> list[Callable[[], str]]:
    def rename() -> str:
        old, new = repo / JAVA / "service" / "CustomerService.java", repo / JAVA / "service" / "ClientService.java"
        _git(repo, "mv", str(old.relative_to(repo)), str(new.relative_to(repo)))
        new.write_text(new.read_text().replace("class CustomerService", "class ClientService"))
        ctl = repo / JAVA / "controller" / "CustomerController.java"
        ctl.write_text(ctl.read_text().replace("CustomerService", "ClientService"))
        return "rename: CustomerService -> ClientService"
    return [rename]


def _delete_recreate(repo: Path) -> list[Callable[[], str]]:
    job = repo / JAVA / "batch" / "OrderExportJob.java"
    original = job.read_text()

    def delete() -> str:
        job.unlink()
        return "delete: OrderExportJob"

    def recreate() -> str:
        job.write_text(original.replace('"0 0 2 * * *"', '"0 30 3 * * *"'))
        return "recreate: OrderExportJob with a new schedule"
    return [delete, recreate]


@pytest.mark.parametrize("script", [_edit, _rename, _delete_recreate], ids=["edit", "rename", "delete_recreate"])
def test_incremental_pages_equal_clean_scan(script, tmp_path: Path) -> None:
    repo = _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])
    _scan(repo)
    for step in script(repo):
        _commit(repo, step())
        _scan(repo)                                   # incremental: same state dir, warm parse cache
    _assert_parity(repo, tmp_path)


def test_incremental_scans_really_reused_state(tmp_path: Path) -> None:
    """Guard against a vacuous pass: the incremental DB holds one scan per commit and reused the parse cache."""
    repo = _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])
    _scan(repo)
    for step in _edit(repo):
        _commit(repo, step())
        _scan(repo)
    conn = sqlite3.connect(repo / ".discovery" / "discovery.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM scan_runs WHERE status = 'completed'").fetchone()[0] == 3
        cached = conn.execute("SELECT COUNT(*) FROM file_parse_cache").fetchone()[0]
    finally:
        conn.close()
    assert cached > 0
    rows, _ = _pages(repo)
    product = rows["symbol:com.example.service.ProductService.getAllProducts"] \
        if "symbol:com.example.service.ProductService.getAllProducts" in rows else None
    assert any("discontinue" in r["body_md"] for r in rows.values()), "the edit reached the pages"
    assert product is None or "createProduct" in product["body_md"]


def test_markup_identity_is_repo_relative() -> None:
    """JSP/WebForms pages are identified by their file: the identity must not carry the checkout path."""
    from ai_discovery.graph.models import CodeNode
    from ai_discovery.stages.parse import _relative_identity

    page = CodeNode(file_path="/w/shop/Orders.aspx", language="webforms", node_type="ui_component", name="Orders",
                    qualified_name="/w/shop/Orders.aspx", source_code="", line_start=1, line_end=1,
                    calls=["/w/shop/Controls/Cart.ascx", "Shop.Orders.Save_Click"],
                    call_sites=[{"name": "/w/shop/Controls/Cart.ascx", "binding": "markup_register"},
                                {"name": "Shop.Orders.Save_Click", "binding": "markup_event"}])
    _relative_identity([page], ("/w/shop/",))
    assert page.qualified_name == "Orders.aspx"
    assert page.calls == ["Controls/Cart.ascx", "Shop.Orders.Save_Click"]
    assert [s["name"] for s in page.call_sites] == ["Controls/Cart.ascx", "Shop.Orders.Save_Click"]
