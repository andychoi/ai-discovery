"""`discover export` (roadmap item M3.10): json, html, markdown."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.export import html as hexport
from tests.golden.test_golden import GOLDEN_REPOS, _build_repo

runner = CliRunner()


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    r = _build_repo(tmp_path_factory.mktemp("export") / "repo", GOLDEN_REPOS["java-spring-jsp"])
    res = runner.invoke(app, ["--json", "-C", str(r), "scan"])
    assert res.exit_code == 0, res.output
    return r


def _export(repo: Path, fmt: str, out: Path) -> dict:
    res = runner.invoke(app, ["--json", "-C", str(repo), "export", "--format", fmt, "--out", str(out)])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


# ── json ─────────────────────────────────────────────────────────────────────

def test_export_json_is_versioned_portable_and_has_no_source(repo: Path, tmp_path: Path) -> None:
    a = _export(repo, "json", tmp_path / "a")
    b = _export(repo, "json", tmp_path / "b")
    text = Path(a["path"]).read_text()
    assert text == Path(b["path"]).read_text()                           # byte-identical re-export
    doc = json.loads(text)
    assert (doc["schema"], doc["schema_version"]) == ("ai-discovery/knowledge-graph", 2)
    assert doc["counts"]["nodes"] == len(doc["nodes"]) > 0 and doc["counts"]["pages"] == len(doc["pages"]) > 0
    assert {"nodes", "edges", "unresolved_calls", "domains", "relationships", "state_machines", "pages"} <= set(doc)
    assert "source_code" not in text and "body_md" not in text            # no source bodies, no page bodies
    assert str(repo) not in text and str(repo.resolve()) not in text      # repo-relative paths only
    assert all(n["file"] is None or not n["file"].startswith("/") for n in doc["nodes"])
    flow = next(p for p in doc["pages"] if p["type"] == "flow")
    assert flow["links"] and {"to", "kind"} <= set(flow["links"][0])


# ── html ─────────────────────────────────────────────────────────────────────

needs_bundle = pytest.mark.skipif(not (hexport.BUNDLE / "index.html").is_file(),
                                  reason="export bundle not built (scripts/build_web.py)")


def _data(out: Path) -> dict:
    raw = (out / "data.js").read_text()
    m = re.fullmatch(r"window\.__DISCOVERY_DATA__ = (.*);\n", raw, re.S)
    assert m, raw[:200]
    return json.loads(m.group(1))


@needs_bundle
def test_export_html_inlines_every_answer_for_file_urls(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    res = _export(repo, "html", out)
    index = (out / "index.html").read_text()
    assert 'type="module"' not in index                                   # file:// refuses module scripts
    assert index.index('src="./data.js"') < index.index('src="./assets/app.js"')
    assert (out / "assets" / "app.js").is_file()
    data = _data(out)
    assert {"/status", "/tree", "/screens"} <= set(data["answers"])
    page_ids = [s["page_id"] for s in data["search"]]
    assert len(page_ids) == res["pages"] > 50
    for pid in page_ids:
        ans = data["answers"][hexport.page_path(pid)]
        assert ans["status"] == "ok" and ans["data"]["page_id"] == pid
    raw = (out / "data.js").read_text()
    assert "</script" not in raw.lower()                                  # page text cannot close the script
    assert str(repo.resolve()) not in raw and data["answers"]["/status"]["data"]["project"]["repo"] is None
    assert "fetch(" not in raw


@needs_bundle
def test_export_html_page_paths_match_encode_uri_component() -> None:
    assert hexport.page_path("symbol:a.B.c@src/B.java:3") == "/pages/symbol%3Aa.B.c%40src%2FB.java%3A3"
    assert hexport.page_path("flow:x (y)!*'~") == "/pages/flow%3Ax%20(y)!*'~"


# ── markdown + errors ────────────────────────────────────────────────────────

def test_export_markdown_one_file_per_page_with_front_matter(repo: Path, tmp_path: Path) -> None:
    res = _export(repo, "markdown", tmp_path / "md")
    files = sorted((tmp_path / "md").rglob("*.md"))
    assert len(files) == res["pages"] > 50
    text = (tmp_path / "md" / "overview" / "system.md").read_text()
    assert text.startswith('---\npage_id: "overview:system"\n') and "\n# System overview\n" in text


def test_export_rejects_bad_format_and_unscanned_repos(tmp_path: Path) -> None:
    res = runner.invoke(app, ["--json", "-C", str(tmp_path), "export", "--format", "pdf"])
    assert res.exit_code == 2 and json.loads(res.stdout)["error"]["reason"] == "contradictory_flags"
    res = runner.invoke(app, ["--json", "-C", str(tmp_path), "export", "--format", "json"])
    assert res.exit_code == 5 and json.loads(res.stdout)["error"]["reason"] == "not_indexed"
