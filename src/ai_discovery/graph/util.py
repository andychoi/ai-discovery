"""Shared graph primitives.

The graph layer previously carried three copies of union-find (one int-indexed
class, one inline closure, and — in federation — a *greedy first-fit grouping
mislabeled as union-find*), plus two ``_jaccard`` helpers that disagreed on the
empty-set case. That divergence was a latent correctness trap (see
docs/reviews/02 §5 and 04 P0-7/P0-8). This module is the single home for those
primitives so every pass shares the same, tested behavior.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Hashable, Iterable, TypeVar

T = TypeVar("T", bound=Hashable)


def jaccard(a: set, b: set) -> float:
    """Jaccard similarity |a∩b| / |a∪b|.

    Empty-set convention (textbook): J(∅, ∅) = 1.0 (two empty sets are
    identical), J(∅, X) = 0.0 for non-empty X. This is the convention both
    former copies now agree on.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class UnionFind:
    """Disjoint-set over arbitrary hashable labels (rank + path compression).

    Generic over the label type so it serves every graph-layer caller: int
    fingerprint indices (fsm_identity), file-path strings (call_graph
    communities), and (slug, fsm) grouping keys (federation). Unknown labels
    are added on first reference, so callers need not pre-declare the universe.
    """

    __slots__ = ("_parent", "_rank")

    def __init__(self, labels: Iterable[T] = ()) -> None:
        self._parent: dict[T, T] = {}
        self._rank: dict[T, int] = {}
        for x in labels:
            self.add(x)

    def add(self, x: T) -> None:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0

    def find(self, x: T) -> T:
        self.add(x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression.
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, x: T, y: T) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self._rank[rx] < self._rank[ry]:
            self._parent[rx] = ry
        elif self._rank[rx] > self._rank[ry]:
            self._parent[ry] = rx
        else:
            self._parent[ry] = rx
            self._rank[rx] += 1

    def connected(self, x: T, y: T) -> bool:
        return self.find(x) == self.find(y)

    def groups(self) -> dict[T, list[T]]:
        """Return {root: [members…]} for every label seen. Deterministic:
        members are in insertion order within each group."""
        out: dict[T, list[T]] = {}
        for x in self._parent:
            out.setdefault(self.find(x), []).append(x)
        return out

    def root_map(self) -> dict[T, T]:
        """Return {label: root} for every label seen."""
        return {x: self.find(x) for x in self._parent}


def bfs_layers(
    start: T,
    neighbors: Callable[[T], Iterable[T]],
    *,
    max_depth: int | None = None,
) -> list[T]:
    """Breadth-first traversal from *start*, visited-guarded at enqueue time.

    Returns nodes in BFS order (including *start*). Uses a ``deque`` and marks
    nodes visited when enqueued — not when popped — so the frontier can't grow
    super-linearly on dense graphs (the O(n²) trap the old list-based
    ``queue.pop(0)`` traversals fell into). ``max_depth`` bounds the walk
    (0 = just *start*); ``None`` is unbounded.
    """
    order: list[T] = []
    seen: set[T] = {start}
    queue: deque[tuple[T, int]] = deque([(start, 0)])
    while queue:
        node, depth = queue.popleft()
        order.append(node)
        if max_depth is not None and depth >= max_depth:
            continue
        for nxt in neighbors(node):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, depth + 1))
    return order
