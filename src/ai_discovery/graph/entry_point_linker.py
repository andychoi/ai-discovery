"""Phase 1.3: link state transitions to their triggering entry points.

Walks the call graph backward from each transition's `trigger_function` until
it reaches nodes whose `node_type` identifies them as external entry points
(API endpoints, UI components, batch jobs, event consumers, CLI commands).
Each reachable entry is recorded on `StateTransition.entry_points` with the
kind, qualified name, min-edge confidence along the path, and hop count.

This answers the core state-first question: *"what UI/API/job causes this
transition?"* — the anchor for downstream BPMN / DMN / EARS generation.
"""

from __future__ import annotations

from collections import defaultdict, deque

from .models import CallEdge, CodeNode, StateTransition

_ENTRY_NODE_TYPES_TO_KIND: dict[str, str] = {
    "endpoint": "API",
    "ui_component": "UI",
    "batch_job": "Batch",
    "event_consumer": "Event",
    "cli_command": "CLI",
}

_MAX_HOPS = 6


def link_entry_points(
    transitions: list[StateTransition],
    nodes: list[CodeNode],
    edges: list[CallEdge],
) -> None:
    """Populate `entry_points` on each transition (in-place).

    For every transition whose `trigger_function` matches a known node, walk
    the call graph backward (callee -> caller) up to `_MAX_HOPS` hops. Every
    node encountered whose `node_type` is an entry kind is recorded, keeping
    the shortest-hop instance when the same entry is reachable via multiple
    paths. Transitions without a resolvable trigger are left untouched.
    """
    nodes_by_qn: dict[str, CodeNode] = {n.qualified_name: n for n in nodes}
    reverse_adj: dict[str, list[CallEdge]] = defaultdict(list)
    for edge in edges:
        reverse_adj[edge.callee].append(edge)

    for transition in transitions:
        if not transition.trigger_function:
            continue
        transition.entry_points = _bfs_entries(
            transition.trigger_function, nodes_by_qn, reverse_adj
        )


def _bfs_entries(
    start: str,
    nodes_by_qn: dict[str, CodeNode],
    reverse_adj: dict[str, list[CallEdge]],
) -> list[dict]:
    """BFS backward from `start`, returning entries sorted by hop_count asc."""
    entries_by_qn: dict[str, dict] = {}
    visited: dict[str, int] = {start: 0}
    queue: deque[tuple[str, int, float]] = deque([(start, 0, 1.0)])

    while queue:
        qn, hops, conf = queue.popleft()

        node = nodes_by_qn.get(qn)
        if node is not None:
            kind = _ENTRY_NODE_TYPES_TO_KIND.get(node.node_type)
            if kind is not None:
                existing = entries_by_qn.get(qn)
                if existing is None or hops < existing["hop_count"]:
                    entries_by_qn[qn] = {
                        "kind": kind,
                        "qualified_name": qn,
                        "confidence": round(conf, 3),
                        "hop_count": hops,
                    }

        if hops >= _MAX_HOPS:
            continue

        for edge in reverse_adj.get(qn, []):
            caller = edge.caller
            new_hops = hops + 1
            if caller in visited and visited[caller] <= new_hops:
                continue
            visited[caller] = new_hops
            new_conf = min(conf, edge.confidence)
            queue.append((caller, new_hops, new_conf))

    return sorted(entries_by_qn.values(), key=lambda e: (e["hop_count"], e["qualified_name"]))
