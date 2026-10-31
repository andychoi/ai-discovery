"""Impact + change impact (roadmap item M5.2) on a hand-built graph.

The graph (callers point at callees; reverse traversal starts at ``svc.A.leaf``)::

    E.deeper → D.deep → C.top(endpoint) ⇒ B.mid → A.leaf ← T.testLeaf (test)
                             (framework)   ↑   ↑  ↖ HUB.hub ← 30 callers
                              J.job(batch) ┘   │   ↖ G.guess (global_unique) ← H.h
                             X.x (global_unique)┘
    B.mid writes ORDERS; B.mid is the trigger of entity model.Order; A.leaf → C.top closes a cycle.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_discovery import api
from ai_discovery.core.paths import ProjectLayout
from ai_discovery.query import Project
from ai_discovery.query import session as query_session
from ai_discovery.query.impact import parse_unified_diff
from tests.golden.test_golden import _GIT_ENV

NOW = datetime.now(timezone.utc).isoformat()

# name → (file, node_type, line_start, line_end)
NODES = {
    "svc.A": ("svc/A.java", "class", 1, 30),
    "svc.A.leaf": ("svc/A.java", "method", 10, 20),
    "svc.B.mid": ("svc/B.java", "method", 5, 15),
    "svc.C.top": ("svc/C.java", "endpoint", 5, 15),
    "svc.D.deep": ("svc/D.java", "method", 1, 10),
    "svc.E.deeper": ("svc/E.java", "method", 1, 10),
    "svc.G.guess": ("svc/G.java", "method", 1, 10),
    "svc.H.h": ("svc/H.java", "method", 1, 10),
    "svc.X.x": ("svc/X.java", "method", 1, 10),
    "svc.J.job": ("svc/J.java", "batch_job", 1, 10),
    "svc.HUB.hub": ("svc/Hub.java", "method", 1, 10),
    "svc.K.leaf": ("svc/K.java", "method", 1, 10),
    "test.ATest.testLeaf": ("src/test/ATest.java", "method", 1, 10),
    "dbo.ORDERS": ("db/schema.sql", "db_model", 1, 5),
    "model.Order": ("model/Order.java", "db_model", 1, 20),
    "svc.Z.z": ("svc/Z.java", "method", 1, 10),
    **{f"svc.callers.C{i}.c": (f"svc/callers/C{i}.java", "method", 1, 10) for i in range(30)},
}
# (caller, callee, origin, edge_kind, evidence_kind)
EDGES = [
    ("svc.B.mid", "svc.A.leaf", "receiver_typed", "calls", "static"),
    ("svc.C.top", "svc.B.mid", "framework", "framework_binds", "framework"),
    ("svc.D.deep", "svc.C.top", "receiver_typed", "calls", "static"),
    ("svc.E.deeper", "svc.D.deep", "receiver_typed", "calls", "static"),
    ("svc.G.guess", "svc.A.leaf", "global_unique", "calls", "static"),
    ("svc.H.h", "svc.G.guess", "receiver_typed", "calls", "static"),
    ("svc.X.x", "svc.B.mid", "global_unique", "calls", "static"),
    ("test.ATest.testLeaf", "svc.A.leaf", "receiver_typed", "calls", "static"),
    ("svc.B.mid", "dbo.ORDERS", "", "writes_table", "static"),
    ("svc.HUB.hub", "svc.A.leaf", "receiver_typed", "calls", "static"),
    ("svc.J.job", "svc.B.mid", "receiver_typed", "calls", "static"),
    ("svc.A.leaf", "svc.C.top", "receiver_typed", "calls", "static"),
    ("svc.B.mid", "model.Order", "receiver_typed", "calls", "static"),
    ("svc.Z.z", "svc.A.leaf", "", "co_change", "co_change"),
    *[(f"svc.callers.C{i}.c", "svc.HUB.hub", "receiver_typed", "calls", "static") for i in range(30)],
]


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, env={**os.environ, **_GIT_ENV}, check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def _build(repo: Path) -> Project:
    repo.mkdir(parents=True)
    for qn, (path, *_rest) in NODES.items():
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            f.write_text("".join(f"// {path} line {i}\n" for i in range(1, 41)))
    (repo / ".gitignore").write_text(".discovery/\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    head = _git(repo, "rev-parse", "HEAD")

    from ai_discovery.db import get_conn, init_db
    from ai_discovery.store.scans import set_current_scan

    layout = ProjectLayout.for_repo(repo)
    init_db(layout.db_path)
    conn = get_conn(layout.db_path)
    sid = conn.execute("INSERT INTO scan_runs (repo_path, commit_sha, started_at, finished_at, status) "
                       "VALUES (?, ?, ?, ?, 'completed')", (str(repo), head, NOW, NOW)).lastrowid
    ids = {}
    for qn, (path, ntype, lo, hi) in NODES.items():
        ids[qn] = conn.execute(
            "INSERT INTO code_nodes (scan_id, file_path, language, node_type, name, qualified_name, line_start, "
            "line_end) VALUES (?, ?, 'java', ?, ?, ?, ?, ?)",
            (sid, str(repo / path), ntype, qn.rsplit(".", 1)[-1], qn, lo, hi)).lastrowid
    for src, dst, origin, kind, evidence in EDGES:
        conn.execute("INSERT INTO call_edges (scan_id, caller_id, callee_id, callee_name, edge_type, confidence, "
                     "resolved_by, edge_kind, evidence_kind) VALUES (?, ?, ?, ?, 'call', 0.9, ?, ?, ?)",
                     (sid, ids[src], ids[dst], dst, origin, kind, evidence))
    conn.executemany("INSERT INTO unresolved_calls (scan_id, caller_id, callee_name, reason, file, line) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     [(sid, ids["svc.X.x"], "leaf", "dynamic", str(repo / "svc/X.java"), 3),
                      (sid, ids["svc.H.h"], "mid", "not_found", str(repo / "svc/H.java"), 4),
                      (sid, ids["svc.H.h"], "println", "builtin", str(repo / "svc/H.java"), 5)])
    conn.execute("INSERT INTO state_transitions (scan_id, entity, entity_id, field, from_state, to_state, "
                 "trigger_function) VALUES (?, 'Order', 'model.Order', 'status', 'NEW', 'PAID', 'svc.B.mid')", (sid,))
    conn.execute("INSERT INTO entity_state_machines (scan_id, entity, entity_id, states_json) VALUES "
                 "(?, 'Order', 'model.Order', '[\"NEW\", \"PAID\"]')", (sid,))
    pages = [
        ("endpoint:GET-/api/c/{id}", "endpoint", "GET /api/c/{id}",
         {"facts": {"handler": "svc.C.top", "method": "GET", "path": "/api/c/{id}"}}, []),
        ("table:orders", "table", "orders", {"facts": {"columns": ["id"]}}, [{"path": "model/Order.java"}]),
        ("screen:s1", "screen", "S1", {"facts": {}}, []),
    ]
    for pid, ptype, title, facts, cites in pages:
        conn.execute("INSERT INTO pages (scan_id, page_id, type, title, facts_json, body_md, citations_json) "
                     "VALUES (?, ?, ?, ?, ?, '', ?)", (sid, pid, ptype, title, json.dumps(facts), json.dumps(cites)))
    specs = {
        "s1": {"screen_id": "s1", "be_services": [{"component_type": "service", "class_name": "B",
                                                    "file_path": "svc/B.java", "methods": ["mid"], "confidence": 0.8}]},
        "s2": {"screen_id": "s2", "fe_api_calls": [{"method": "GET", "path": "/api/c/:id", "confidence": 0.9}]},
        "s3": {"screen_id": "s3", "db_tables": ["orders"]},
    }
    for screen_id, spec in specs.items():
        conn.execute("INSERT INTO screen_specs (scan_id, screen_id, spec_json, created_at) VALUES (?, ?, ?, ?)",
                     (sid, screen_id, json.dumps(spec), NOW))
    conn.execute("INSERT INTO db_relationship (scan_id, from_entity, to_entity, from_field, to_field, cardinality, "
                 "source, inferred) VALUES (?, 'order_items', 'orders', 'order_id', 'id', 'N:1', 'fk', 0)", (sid,))
    set_current_scan(conn, sid)
    conn.commit()
    conn.close()
    return Project(layout)


@pytest.fixture()
def project(tmp_path) -> Project:
    query_session._meta_cache.clear()
    return _build(tmp_path / "repo")


def _items(ans: dict) -> dict[str, dict]:
    assert ans["status"] == "ok", ans
    return {i["node"]: i for i in ans["data"]["items"]}


def _unknown_kinds(ans: dict) -> set[str]:
    return {u["kind"] for u in ans["data"]["unknowns"]}


# ── impact ────────────────────────────────────────────────────────────────

def test_impact_reverse_traversal(project) -> None:
    ans = api.impact(project, "svc.A.leaf")
    items = _items(ans)
    assert items["svc.B.mid"]["depth"] == 1 and items["svc.B.mid"]["certainty"] == "likely"
    assert items["svc.C.top"]["depth"] == 2 and items["svc.C.top"]["kind"] == "endpoint"
    assert items["svc.C.top"]["page_id"] == "endpoint:GET-/api/c/{id}"
    assert items["svc.D.deep"]["depth"] == 3 and "svc.E.deeper" not in items          # depth cap 3
    assert items["svc.J.job"]["kind"] == "batch_job"
    assert items["test.ATest.testLeaf"]["kind"] == "test" and ans["data"]["tests_reached"] == ["test.ATest.testLeaf"]
    # low-confidence edge from a seed: reported as possible, its callers not walked
    assert items["svc.G.guess"]["certainty"] == "possible" and "svc.H.h" not in items
    # a low-confidence edge into a non-seed is not followed
    assert "svc.X.x" not in items
    # hubs are reported but not expanded
    assert "svc.HUB.hub" in items and not any(n.startswith("svc.callers.") for n in items)
    # data writes and entity state machines
    assert items["dbo.ORDERS"]["kind"] == "table" and items["dbo.ORDERS"]["via"][0]["edge_kind"] == "writes_table"
    assert items["entity:model.Order"]["kind"] == "entity"
    # screens via backend component (s1) and via endpoint route with a different param syntax (s2)
    assert {"screen:s1", "screen:s2"} <= set(items) and "screen:s3" not in items
    eps = set(ans["data"]["entry_points_reached"])
    assert {"endpoint:GET-/api/c/{id}", "screen:s1", "screen:s2", "batch_job:svc.J.job"} <= eps
    assert all(i["may_break"] is True for i in items.values())
    assert _unknown_kinds(ans) >= {"hub_not_expanded", "possible_not_expanded", "low_confidence_edge_not_followed",
                                   "dynamic_hint", "unresolved_call"}
    # co-change is never an impact item; missing overlays are named
    assert "svc.Z.z" not in items and ans["data"]["co_change"] == []
    assert ans["data"]["overlays_missing"] == ["coverage", "runtime"]
    assert set(ans["meta"]["evidence_kinds_used"]) >= {"static", "framework"}


def test_impact_ranking_and_via_paths(project) -> None:
    ans = api.impact(project, "svc.A.leaf")
    scores = [i["score"] for i in ans["data"]["items"]]
    assert scores == sorted(scores, reverse=True)
    items = _items(ans)
    assert items["svc.B.mid"]["score"] == pytest.approx(0.8 * 0.93, abs=1e-3)
    assert items["dbo.ORDERS"]["score"] > items["svc.C.top"]["score"]     # data write outranks a deeper call
    via = items["svc.D.deep"]["via"]
    assert [v["src"] for v in via] == ["svc.B.mid", "svc.C.top", "svc.D.deep"]
    assert via[1]["origin"] == "framework" and via[1]["evidence_kind"] == "framework"


def test_impact_depth_include_and_cap(project) -> None:
    assert "svc.E.deeper" in _items(api.impact(project, "svc.A.leaf", depth=4))
    assert api.impact(project, "svc.A.leaf", depth=99)["data"]["depth"] == 6
    static_only = _items(api.impact(project, "svc.A.leaf", include=["static"]))
    assert "svc.C.top" not in static_only and "svc.B.mid" in static_only   # framework edge excluded
    capped = api.impact(project, "svc.A.leaf", max_items=3)
    assert len(capped["data"]["items"]) == 3 and capped["truncated"]
    omitted = {o["field"]: o for o in capped["omitted"]}
    assert omitted["items"]["total"] == capped["totals"]["items"] > 3


def test_impact_targets(project) -> None:
    assert _items(api.impact(project, "file:svc/A.java"))["svc.B.mid"]["depth"] == 1   # class seed → members
    table = _items(api.impact(project, "table:orders"))
    assert "svc.B.mid" in table and "screen:s3" in table and "table:order_items" in table
    entity = api.impact(project, "entity:Order")
    assert entity["data"]["target"] == "entity:model.Order"
    assert {"svc.C.top", "svc.J.job"} <= set(_items(entity))
    screen = _items(api.impact(project, "screen:s1"))
    assert "svc.C.top" in screen and "svc.J.job" in screen
    endpoint = _items(api.impact(project, "endpoint:GET-/api/c/{id}"))
    assert "svc.D.deep" in endpoint and "screen:s2" in endpoint


def test_impact_not_found_and_ambiguous(project) -> None:
    amb = api.impact(project, "leaf")
    assert amb["status"] == "unknown" and {c["id"] for c in amb["data"]["candidates"]} == {
        "symbol:svc.A.leaf", "symbol:svc.K.leaf"}
    nf = api.impact(project, "nothing.like.this")
    assert nf["status"] == "not_found" and nf["data"]["remedy"]
    assert api.impact(project, "table:nope")["status"] == "not_found"
    assert api.impact(project, "entity:Nope")["status"] == "not_found"


# ── change impact ─────────────────────────────────────────────────────────

DIFF = """diff --git a/svc/A.java b/svc/A.java
index 1..2 100644
--- a/svc/A.java
+++ b/svc/A.java
@@ -12 +12 @@ class A {
-old
+new
@@ -25,0 +26,2 @@
+x
+y
diff --git a/svc/Old.java b/svc/New.java
similarity index 90%
rename from svc/Old.java
rename to svc/New.java
diff --git a/svc/Gone.java b/svc/Gone.java
deleted file mode 100644
--- a/svc/Gone.java
+++ /dev/null
@@ -1,3 +0,0 @@
diff --git a/svc/Brand.java b/svc/Brand.java
new file mode 100644
--- /dev/null
+++ b/svc/Brand.java
@@ -0,0 +1,2 @@
"""


def test_parse_unified_diff() -> None:
    files = {(f.old_path, f.new_path): f for f in parse_unified_diff(DIFF)}
    a = files[("svc/A.java", "svc/A.java")]
    assert a.status == "modified" and a.old_ranges == [(12, 12), (25, 25)] and a.new_ranges == [(12, 12), (26, 27)]
    assert files[("svc/Old.java", "svc/New.java")].status == "renamed"
    assert files[("svc/Gone.java", None)].status == "deleted"
    assert files[(None, "svc/Brand.java")].status == "added"


def test_change_impact_working_tree(project) -> None:
    repo = project.layout.repo
    a = repo / "svc/A.java"
    lines = a.read_text().splitlines(keepends=True)
    lines[11] = "// changed inside leaf\n"            # line 12, inside svc.A.leaf (10-20) and class svc.A (1-30)
    a.write_text("".join(lines))
    (repo / "svc/Fresh.java").write_text("class Fresh {}\n")
    ans = api.change_impact(project, working_tree=True)
    assert ans["status"] == "ok", ans
    data = ans["data"]
    assert data["source"]["kind"] == "working_tree"
    assert [s["id"] for s in data["changed_symbols"]] == ["svc.A.leaf"]     # innermost symbol only
    assert {f["path"] for f in data["changed_files"]} == {"svc/A.java", "svc/Fresh.java"}
    items = {i["node"] for i in data["impact"]["items"]}
    assert {"svc.B.mid", "svc.C.top", "screen:s1"} <= items
    assert "screen:s1" in data["screens_affected"]
    assert data["tests_to_run"]["status"] == "unknown" and data["tests_to_run"]["tests"] == ["test.ATest.testLeaf"]
    unmapped = [u for u in data["impact"]["unknowns"] if u["kind"] == "unmapped_change"]
    assert unmapped and unmapped[0]["where"] == "svc/Fresh.java"
    assert "svc/A.java" in (ans["meta"]["stale_warning"] or "")


def test_change_impact_range_and_diff(project) -> None:
    repo = project.layout.repo
    base = _git(repo, "rev-parse", "HEAD")
    b = repo / "svc/B.java"
    lines = b.read_text().splitlines(keepends=True)
    lines[6] = "// changed mid\n"                        # line 7, inside svc.B.mid (5-15)
    b.write_text("".join(lines))
    _git(repo, "commit", "-qam", "change mid")
    ans = api.change_impact(project, range=f"{base}..HEAD")
    assert ans["status"] == "ok" and [s["id"] for s in ans["data"]["changed_symbols"]] == ["svc.B.mid"]
    assert "diff_base_differs_from_index" not in ans["meta"]["degraded"]
    assert "svc.C.top" in {i["node"] for i in ans["data"]["impact"]["items"]}
    raw = subprocess.run(["git", "diff", base, "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout
    via_diff = api.change_impact(project, diff=raw)
    assert [s["id"] for s in via_diff["data"]["changed_symbols"]] == ["svc.B.mid"]
    assert "diff_base_unknown" in via_diff["meta"]["degraded"]


def test_change_impact_bad_arguments(project) -> None:
    both = api.change_impact(project, diff="", working_tree=True)
    assert both["status"] == "unknown" and both["data"]["reason"] == "bad_arguments"
    assert api.change_impact(project)["data"]["reason"] == "bad_arguments"
    bad = api.change_impact(project, range="--output=/tmp/x")
    assert bad["status"] == "unknown" and bad["data"]["reason"] == "bad_range"
    missing = api.change_impact(project, range="nosuchref..HEAD")
    assert missing["status"] == "unknown" and missing["data"]["reason"] == "git_failed"
