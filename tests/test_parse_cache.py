"""Per-file parse cache (roadmap item M1.9, ADR-0002): incremental == full."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.graph.models import CodeNode
from ai_discovery.parsers.java import JavaParser
from ai_discovery.store.parse_cache import ParseCache, decode_nodes, encode_nodes

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "projects" / "spring-boot-app"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
SERVICE = "src/main/java/com/example/service/OrderService.java"
runner = CliRunner()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env={**os.environ, **GIT_ENV}, check=True, capture_output=True)


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    shutil.copytree(FIXTURE, repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _scan(repo: Path, state: Path, *extra: str) -> None:
    res = runner.invoke(app, ["-q", "--json", "-C", str(repo), "--state-dir", str(state), "scan", *extra])
    assert res.exit_code == 0, res.output


def _edit(repo: Path) -> None:
    path = repo / SERVICE
    text = path.read_text()
    idx = text.rindex("}")
    path.write_text(text[:idx] + "    public int countOrders() { return getOrders().size(); }\n" + text[idx:])
    _git(repo, "commit", "-qam", "edit")


def _snapshot(state: Path, repo: Path) -> dict:
    """Scan output with ids, scan ids and absolute repo paths factored out."""
    conn = sqlite3.connect(state / "discovery.db")
    sid = conn.execute("SELECT MAX(id) FROM scan_runs WHERE status = 'completed'").fetchone()[0]
    root = str(repo.resolve())

    def rel(p):
        return (p or "").replace(root, "<repo>").replace(str(repo), "<repo>")

    out = {
        "nodes": sorted((rel(r[0]), *r[1:]) for r in conn.execute(
            "SELECT file_path, language, node_type, name, qualified_name, line_start, line_end, "
            "source_code, annotations, params, return_type, framework_hints, domain, file_hash "
            "FROM code_nodes WHERE scan_id = ?", (sid,))),
        "edges": sorted(conn.execute(
            "SELECT a.qualified_name, COALESCE(b.qualified_name, ''), ce.callee_name, ce.edge_type, "
            "ce.confidence, ce.resolved_by FROM call_edges ce JOIN code_nodes a ON a.id = ce.caller_id "
            "LEFT JOIN code_nodes b ON b.id = ce.callee_id WHERE ce.scan_id = ?", (sid,))),
        "domains": sorted(conn.execute(
            "SELECT name, node_count, entry_points FROM domains WHERE scan_id = ?", (sid,))),
        "pages": sorted((r[0], r[1]) for r in conn.execute(
            "SELECT page_id, title FROM pages WHERE scan_id = (SELECT MAX(scan_id) FROM pages)")),
    }
    conn.close()
    return out


@pytest.fixture
def parse_calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = JavaParser.parse_file

    def counting(self, path):
        import sys

        # only the parse stage's calls (screen mapping parses some files on its own)
        if sys._getframe(1).f_code.co_filename.endswith(os.path.join("stages", "parse.py")):
            calls.append(Path(path).name)
        return real(self, path)

    monkeypatch.setattr(JavaParser, "parse_file", counting)
    return calls


def test_incremental_scan_equals_a_full_scan_of_the_edited_tree(tmp_path: Path, parse_calls) -> None:
    repo = _repo(tmp_path)
    state = tmp_path / "state"
    _scan(repo, state)
    first = len(parse_calls)
    assert first == 14                                  # every .java file in the fixture

    _edit(repo)
    parse_calls.clear()
    _scan(repo, state)                                  # new commit, cache warm
    assert parse_calls == ["OrderService.java"]         # only the edited file is re-parsed

    clean = tmp_path / "clean-state"
    _scan(repo, clean)                                  # fresh state dir: no cache
    assert _snapshot(state, repo) == _snapshot(clean, repo)


def test_full_ignores_the_cache(tmp_path: Path, parse_calls) -> None:
    repo = _repo(tmp_path)
    state = tmp_path / "state"
    _scan(repo, state)
    parse_calls.clear()
    _scan(repo, state, "--full")
    assert len(parse_calls) == 14


def test_parser_version_change_invalidates(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    state = tmp_path / "state"
    _scan(repo, state)
    cache = ParseCache(state / "discovery.db")
    assert cache._entries and cache.get(SERVICE, cache._entries[SERVICE][0], cache._entries[SERVICE][1], "x")

    import ai_discovery.store.parse_cache as pc
    monkeypatch.setattr(pc, "parser_version", lambda: "different")
    assert ParseCache(state / "discovery.db")._entries == {}


def test_only_exact_round_trips_are_cached() -> None:
    ok = CodeNode(file_path="/r/A.java", language="java", node_type="method", name="a", qualified_name="A.a",
                  source_code="void a(){}", line_start=1, line_end=1, framework_hints={"k": [1, 2]})
    text = encode_nodes([ok], "/r/A.java")
    assert text is not None and '"file_path": null' in text
    back = decode_nodes(text, "/moved/A.java")
    assert back[0].file_path == "/moved/A.java" and back[0].framework_hints == {"k": [1, 2]}
    bad = CodeNode(file_path="/r/A.java", language="java", node_type="method", name="a", qualified_name="A.a",
                   source_code="", line_start=1, line_end=1, framework_hints={"k": (1, 2)})
    assert encode_nodes([bad], "/r/A.java") is None      # tuple would come back as a list


def test_encoding_fallback_is_still_reported_on_cache_hits(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    korean = repo / "src/main/java/com/example/Legacy.java"
    korean.write_bytes("// 주문 처리\npublic class Legacy { void run() {} }\n".encode("cp949"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cp949")
    state = tmp_path / "state"
    _scan(repo, state)
    _edit(repo)
    _scan(repo, state)
    conn = sqlite3.connect(state / "discovery.db")
    rows = conn.execute("SELECT scan_id, count FROM degradations WHERE kind = 'encoding_fallback' "
                        "ORDER BY scan_id").fetchall()
    conn.close()
    assert rows == [(1, 1), (2, 1)]
