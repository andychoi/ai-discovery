"""Golden characterization harness (roadmap item M0.2).

Runs the *current* pipeline end-to-end on two fixture repos with a
deterministic stub LLM and compares a structural snapshot against the
committed golden file. It pins behaviour so the strangler refactor
(M1.6–M1.8, ADR-0003) can prove "no behaviour change".

Snapshot = per-table row counts, sorted call-edge tuples, sorted screen ids,
sorted output file paths, LLM call counts per method/tier. LLM prose is
deliberately not asserted.

Update the golden files only for an intended behaviour change, and record the
justification in docs/roadmap/PROGRESS.md:

    UPDATE_GOLDEN=1 pytest -q tests/golden
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_discovery.config import DiscoveryConfig

from .stub_llm import StubLLM

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "fixtures"
SNAPSHOTS = HERE / "snapshots"
RECORDED = HERE / "recorded_llm.json"

# name -> list of (fixture source dir, destination subdir inside the repo)
GOLDEN_REPOS: dict[str, list[tuple[Path, str]]] = {
    "java-spring-jsp": [
        (FIXTURES / "projects" / "spring-boot-app", "."),
        (FIXTURES / "jsp", "src/main/webapp/WEB-INF/views"),
    ],
    "dotnet-webforms": [
        (FIXTURES / "projects" / "aspnet-core-app", "."),
        (FIXTURES / "webforms", "WebForms"),
    ],
}

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "golden", "GIT_AUTHOR_EMAIL": "golden@example.com",
    "GIT_COMMITTER_NAME": "golden", "GIT_COMMITTER_EMAIL": "golden@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


def _build_repo(dest: Path, parts: list[tuple[Path, str]]) -> Path:
    for src, sub in parts:
        target = dest / sub
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("ground_truth.json"))
    env = {**os.environ, **_GIT_ENV}
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "golden fixture"]):
        subprocess.run(cmd, cwd=dest, env=env, check=True, capture_output=True)
    return dest


def _scan(name: str, workdir: Path) -> tuple[Path, Path, Path, StubLLM]:
    repo = _build_repo(workdir / "repo", GOLDEN_REPOS[name])
    output_dir, docs_root = workdir / "out", workdir / "docs"
    stub = StubLLM.from_file(RECORDED)
    # Same seams as tests/test_integration.py: sqlite-vec embedding/search and
    # self-review persistence are bypassed (known DB-lock limitation there).
    from ai_discovery.ai.self_review import ReviewClaim

    with (
        patch("ai_discovery.ai.llm_client.LLMClient", return_value=stub),
        patch("ai_discovery.rag.embedder.embed_chunks", return_value={"embedded": 0, "dim": 256}),
        patch("ai_discovery.rag.retriever.search", return_value=[]),
        patch("ai_discovery.ai.self_review.review_document",
              return_value=[ReviewClaim(claim_text="stub claim", status="verified")]),
        patch("ai_discovery.ai.self_review.persist_claims"),
    ):
        from ai_discovery.pipeline import run_pipeline

        run_pipeline(
            repo=str(repo), branch="main", project_slug=name,
            output_dir=output_dir, docs_root=docs_root,
            config=DiscoveryConfig(provider="bedrock", budget_limit_usd=100.0, max_concurrent=1),
        )
    return repo, output_dir, docs_root, stub


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    counts = {}
    for name in names:
        try:
            counts[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.OperationalError:
            continue  # vec0 virtual tables need the sqlite-vec extension loaded
    return counts


def _files(root: Path, skip_suffixes: tuple[str, ...] = ()) -> list[str]:
    if not root.exists():
        return []
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*")
        if p.is_file() and not p.name.endswith(skip_suffixes)
    )


def _rel(path: str, repo: Path) -> str:
    """code_nodes stores absolute paths; make them repo-relative so tmp dirs don't leak in."""
    for root in (str(repo.resolve()), str(repo)):
        if path.startswith(root):
            return path[len(root):].lstrip("/")
    return path


_LEAK = "<abs-path-segment>"


def _leaked_segments(repo: Path) -> set[str]:
    """Segments of the repo's absolute parent path (e.g. 'private', 'var', 'tmp').

    Known bug (follow-up, lane C): the domain classifier can name a domain after
    a segment of the *absolute* path, so the name depends on where the repo was
    checked out. Normalise those names so the snapshot is machine-independent
    while still recording that the leak happens.
    """
    segs: set[str] = set()
    for parent in {repo.parent, repo.resolve().parent}:
        segs.update(p.lower() for p in parent.parts if p not in ("/", ""))
    return segs


def _norm_name(value: str, leaked: set[str]) -> str:
    return _LEAK if value.lower() in leaked else value


def _norm_file(path: str, leaked: set[str]) -> str:
    stem, dot, ext = path.rpartition("/")[2].partition(".")
    if stem.lower() in leaked:
        return path[: len(path) - len(path.rpartition("/")[2])] + _LEAK + dot + ext
    return path


def snapshot(name: str, repo: Path, output_dir: Path, docs_root: Path, stub: StubLLM) -> dict:
    db_path = output_dir / f"output-{name}" / f"discovery-{name}.db"
    assert db_path.exists(), f"scan produced no DB at {db_path}"
    conn = sqlite3.connect(str(db_path))
    try:
        status = conn.execute("SELECT status FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
        edges = conn.execute(
            """SELECT c.file_path, c.name, COALESCE(t.name, e.callee_name), e.edge_type,
                      e.resolved_by, ROUND(e.confidence, 3)
               FROM call_edges e JOIN code_nodes c ON c.id = e.caller_id
               LEFT JOIN code_nodes t ON t.id = e.callee_id"""
        ).fetchall()
        # The `screens` table is never written by the current pipeline (audit
        # follow-up); screen identity lives in `screen_specs`.
        screens = [r[0] for r in conn.execute("SELECT screen_id FROM screen_specs")]
        domains = [r[0] for r in conn.execute("SELECT name FROM domains")]
        counts = _table_counts(conn)
    finally:
        conn.close()
    leaked = _leaked_segments(repo)
    return {
        "scan_status": status,
        "domains": sorted(_norm_name(d, leaked) for d in domains),
        "table_counts": counts,
        "call_edges": sorted(([_rel(e[0], repo), *e[1:]] for e in edges), key=lambda e: [str(x) for x in e]),
        "screen_ids": sorted(screens),
        "intermediate_files": _files(output_dir / f"output-{name}",
                                     skip_suffixes=(".db", ".db-wal", ".db-shm", ".db-journal")),
        "doc_files": sorted(_norm_file(f, leaked) for f in _files(docs_root)),
        "llm_calls": stub.call_counts(),
    }


@pytest.mark.parametrize("name", sorted(GOLDEN_REPOS))
def test_golden_snapshot(name: str, tmp_path: Path) -> None:
    snap_a = snapshot(name, *_scan(name, tmp_path / "a"))
    snap_b = snapshot(name, *_scan(name, tmp_path / "b"))
    assert snap_a == snap_b, "two scans of the same tree produced different snapshots (nondeterminism)"

    golden = SNAPSHOTS / f"{name}.json"
    rendered = json.dumps(snap_a, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        SNAPSHOTS.mkdir(exist_ok=True)
        golden.write_text(rendered, encoding="utf-8")
        pytest.skip(f"golden snapshot written: {golden}")
    assert golden.exists(), f"missing {golden}; run UPDATE_GOLDEN=1 pytest -q tests/golden"
    expected = json.loads(golden.read_text(encoding="utf-8"))
    assert snap_a == expected, (
        "pipeline behaviour changed vs golden snapshot. If intended, run "
        "UPDATE_GOLDEN=1 pytest -q tests/golden and justify it in docs/roadmap/PROGRESS.md"
    )
