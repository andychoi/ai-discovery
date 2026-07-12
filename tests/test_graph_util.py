"""Tests for graph.util — shared UnionFind, jaccard, bfs_layers.

These primitives replaced three divergent union-find copies (one of them a
greedy first-fit impostor in federation) and two conflicting jaccard helpers.
The tests pin the behavior the whole graph layer now shares.
"""

from __future__ import annotations

from ai_discovery.graph.util import UnionFind, jaccard, bfs_layers


# ── jaccard ──────────────────────────────────────────────────────────────────


def test_jaccard_empty_convention():
    assert jaccard(set(), set()) == 1.0
    assert jaccard(set(), {"a"}) == 0.0
    assert jaccard({"a"}, set()) == 0.0


def test_jaccard_standard():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3
    assert jaccard({"a"}, {"b"}) == 0.0


# ── UnionFind ────────────────────────────────────────────────────────────────


def test_unionfind_basic_grouping():
    uf = UnionFind(range(5))
    uf.union(0, 1)
    uf.union(3, 4)
    groups = {frozenset(v) for v in uf.groups().values()}
    assert groups == {frozenset({0, 1}), frozenset({2}), frozenset({3, 4})}


def test_unionfind_transitivity():
    """A~B, B~C ⇒ A,B,C one group. This is the property federation's former
    greedy grouping violated (P0-7)."""
    uf = UnionFind(range(3))
    uf.union(0, 1)
    uf.union(1, 2)
    assert uf.connected(0, 2)
    assert len(uf.groups()) == 1


def test_unionfind_adds_unknown_labels_on_reference():
    uf = UnionFind()
    uf.union("order", "orders")   # both added implicitly
    assert uf.connected("order", "orders")
    assert uf.find("never-seen") == "never-seen"  # lazily added, own root


def test_unionfind_string_labels_and_root_map():
    uf = UnionFind()
    uf.union("a.py", "b.py")
    uf.union("b.py", "c.py")
    rm = uf.root_map()
    assert rm["a.py"] == rm["b.py"] == rm["c.py"]


def test_unionfind_groups_are_deterministic_insertion_order():
    uf = UnionFind()
    for x in ["x", "y", "z"]:
        uf.add(x)
    uf.union("x", "z")
    # Members appear in first-seen order.
    members = uf.groups()[uf.find("x")]
    assert members == ["x", "z"]


# ── bfs_layers ───────────────────────────────────────────────────────────────


def test_bfs_layers_order_and_dedup():
    graph = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}
    order = bfs_layers("a", lambda n: graph.get(n, []))
    assert order[0] == "a"
    assert set(order) == {"a", "b", "c", "d"}
    assert order.count("d") == 1  # visited-guarded, not enqueued twice


def test_bfs_layers_respects_max_depth():
    graph = {"a": ["b"], "b": ["c"], "c": ["d"], "d": []}
    assert bfs_layers("a", lambda n: graph.get(n, []), max_depth=0) == ["a"]
    assert bfs_layers("a", lambda n: graph.get(n, []), max_depth=1) == ["a", "b"]


def test_bfs_layers_handles_cycle():
    graph = {"a": ["b"], "b": ["a"]}
    order = bfs_layers("a", lambda n: graph.get(n, []))
    assert order == ["a", "b"]  # terminates, no infinite loop
