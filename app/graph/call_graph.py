"""Build call graph from CodeNode.calls references."""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from .models import CallEdge, CodeNode, ExecutionEdge, ExecutionNode, Scenario, StateTransition


def build_call_graph(nodes: list[CodeNode]) -> list[CallEdge]:
    """Build call graph from CodeNode.calls references.

    1. Build a name-resolution index: qualified_name -> CodeNode, plus short name -> [CodeNode]
    2. For each node with calls, try to resolve each call:
       - Exact match on qualified_name -> confidence=1.0
       - Same class/module contextual match -> confidence=0.95/0.90
       - Unique suffix match -> confidence=0.85
       - Ambiguous short-name match -> confidence=0.6
       - No match -> record unresolved edge with confidence=0.5
    3. Return list of CallEdge
    """
    # Build indices
    qualified_index: dict[str, CodeNode] = {}
    short_name_index: dict[str, list[CodeNode]] = defaultdict(list)

    for node in nodes:
        qualified_index[node.qualified_name] = node
        short_name_index[node.name].append(node)

    edges: list[CallEdge] = []

    for node in nodes:
        if not node.calls:
            continue

        for call_ref in node.calls:
            # Try exact match on qualified name
            if call_ref in qualified_index:
                target = qualified_index[call_ref]
                edges.append(
                    CallEdge(
                        caller=node.qualified_name,
                        callee=target.qualified_name,
                        edge_type="direct_call",
                        confidence=1.0,
                    )
                )
                continue

            resolved = _resolve_contextual_targets(node, call_ref, short_name_index)
            if resolved:
                for target, confidence in resolved:
                    if target.qualified_name == node.qualified_name:
                        continue
                    edges.append(
                        CallEdge(
                            caller=node.qualified_name,
                            callee=target.qualified_name,
                            edge_type="direct_call",
                            confidence=confidence,
                        )
                    )
                continue

            # Unresolved / external call
            edges.append(
                CallEdge(
                    caller=node.qualified_name,
                    callee=call_ref,
                    edge_type="direct_call",
                    confidence=0.5,
                )
            )

    return edges


def _resolve_contextual_targets(
    caller: CodeNode,
    call_ref: str,
    short_name_index: dict[str, list[CodeNode]],
) -> list[tuple[CodeNode, float]]:
    candidates = list(short_name_index.get(call_ref, []))
    if not candidates:
        if "." in call_ref:
            suffix_matches = _suffix_matches(call_ref, short_name_index)
            if len(suffix_matches) == 1:
                return [(suffix_matches[0], 0.85)]
            if len(suffix_matches) > 1:
                return [(target, 0.7) for target in suffix_matches if target.qualified_name != caller.qualified_name]
        return []

    same_class = _same_class_matches(caller, call_ref, candidates)
    if same_class:
        return [(target, 0.95) for target in same_class]

    same_file = [target for target in candidates if target.file_path == caller.file_path]
    if len(same_file) == 1:
        return [(same_file[0], 0.9)]
    if len(same_file) > 1:
        return [(target, 0.75) for target in same_file if target.qualified_name != caller.qualified_name]

    same_module = _same_module_matches(caller, call_ref, candidates)
    if len(same_module) == 1:
        return [(same_module[0], 0.85)]
    if len(same_module) > 1:
        return [(target, 0.7) for target in same_module if target.qualified_name != caller.qualified_name]

    if len(candidates) == 1:
        return [(candidates[0], 0.8)]

    ranked = _rank_by_prefix_overlap(caller, candidates)
    if ranked and ranked[0][1] > 0:
        best_score = ranked[0][1]
        best = [target for target, score in ranked if score == best_score and target.qualified_name != caller.qualified_name]
        if len(best) == 1:
            return [(best[0], 0.75)]
        if best:
            return [(target, 0.65) for target in best]

    return [(target, 0.6) for target in candidates if target.qualified_name != caller.qualified_name]


def _same_class_matches(caller: CodeNode, call_ref: str, candidates: list[CodeNode]) -> list[CodeNode]:
    owner = _class_owner_prefix(caller.qualified_name)
    if not owner:
        return []
    expected = f"{owner}.{call_ref}"
    return [target for target in candidates if target.qualified_name == expected]


def _same_module_matches(caller: CodeNode, call_ref: str, candidates: list[CodeNode]) -> list[CodeNode]:
    module_prefixes = _module_prefixes(caller.qualified_name)
    for prefix in module_prefixes:
        expected = f"{prefix}.{call_ref}"
        matches = [target for target in candidates if target.qualified_name == expected]
        if matches:
            return matches
    return []


def _suffix_matches(call_ref: str, short_name_index: dict[str, list[CodeNode]]) -> list[CodeNode]:
    suffix = f".{call_ref}"
    matches: list[CodeNode] = []
    seen: set[str] = set()
    for nodes in short_name_index.values():
        for node in nodes:
            if node.qualified_name.endswith(suffix) and node.qualified_name not in seen:
                matches.append(node)
                seen.add(node.qualified_name)
    return matches


def _class_owner_prefix(qualified_name: str) -> str | None:
    parts = qualified_name.split(".")
    if len(parts) >= 3:
        return ".".join(parts[:-1])
    return None


def _module_prefixes(qualified_name: str) -> list[str]:
    parts = qualified_name.split(".")
    if len(parts) <= 1:
        return []
    # Try narrower prefixes first, but exclude the direct class owner handled separately.
    prefixes: list[str] = []
    for idx in range(max(1, len(parts) - 2), 0, -1):
        prefixes.append(".".join(parts[:idx]))
    return prefixes


def _rank_by_prefix_overlap(caller: CodeNode, candidates: list[CodeNode]) -> list[tuple[CodeNode, int]]:
    caller_parts = caller.qualified_name.split(".")
    ranked: list[tuple[CodeNode, int]] = []
    for target in candidates:
        target_parts = target.qualified_name.split(".")
        overlap = 0
        for left, right in zip(caller_parts, target_parts):
            if left != right:
                break
            overlap += 1
        # Prefer same directory when prefix overlap ties.
        if PurePosixPath(target.file_path).parent == PurePosixPath(caller.file_path).parent:
            overlap += 1
        ranked.append((target, overlap))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


_ENTRY_TYPES = frozenset({"endpoint", "batch_job", "cli_command", "event_consumer"})
_CLI_HINTS = frozenset({"main", "cli", "command", "cmd"})
_EVENT_HINTS = frozenset({"consumer", "listener", "subscriber", "handler"})


def identify_scenarios(nodes: list[CodeNode]) -> list[CodeNode]:
    """Identify entry points for execution scenarios.

    Includes HTTP endpoints, batch jobs, CLI entry points, and event consumers.
    """
    scenarios: list[CodeNode] = []
    for node in nodes:
        if node.node_type in _ENTRY_TYPES:
            scenarios.append(node)
        elif node.node_type in ("function", "method"):
            name_lower = node.name.lower()
            if any(h in name_lower for h in _CLI_HINTS):
                scenarios.append(node)
            elif any(h in name_lower for h in _EVENT_HINTS):
                scenarios.append(node)
    return scenarios


class ExecutionSliceBuilder:
    """Builds bounded execution slices starting from entry points."""

    def __init__(self, nodes: list[CodeNode], call_edges: list[CallEdge]):
        self.nodes = {n.qualified_name: n for n in nodes}
        self.edges_by_caller = defaultdict(list)
        for edge in call_edges:
            self.edges_by_caller[edge.caller].append(edge)

    def build_all_scenarios(self) -> list[Scenario]:
        entry_points = identify_scenarios(list(self.nodes.values()))
        scenarios = []
        for entry in entry_points:
            scenarios.append(self.build_scenario(entry))
        return scenarios

    def build_scenario(self, entry: CodeNode, max_depth: int = 5) -> Scenario:
        """Perform bounded BFS to extract a scenario-specific slice."""
        scenario_id = f"scenario_{entry.name}_{entry.line_start}"
        scenario = Scenario(
            scenario_id=scenario_id,
            name=f"Flow: {entry.name}",
            entry_point=entry.qualified_name,
            trigger_type=self._get_trigger_type(entry),
            domain=entry.domain,
        )

        visited: set[str] = set()
        # BFS queue: (node_name, depth, prev_exec_node)
        queue: list[tuple[str, int, ExecutionNode | None]] = [
            (entry.qualified_name, 0, None)
        ]
        # Track state writes for read-after-write scoring: field -> value
        state_writes: dict[str, str] = {}

        while queue:
            node_name, depth, prev_exec = queue.pop(0)
            if node_name in visited or depth > max_depth:
                continue
            visited.add(node_name)

            node = self.nodes.get(node_name)
            exec_node = self._create_execution_node(node_name, node)

            # Multi-signal scoring
            exec_node.confidence = self._score_node(exec_node, depth, prev_exec, state_writes)

            # Track state writes for downstream read-after-write detection
            if exec_node.state_transition:
                t = exec_node.state_transition
                if t.field and t.to_state:
                    state_writes[t.field] = t.to_state

            scenario.nodes.append(exec_node)

            if not node:
                continue

            for edge in self.edges_by_caller.get(node_name, []):
                scenario.edges.append(ExecutionEdge(
                    from_node=node_name,
                    to_node=edge.callee,
                    edge_type="CALL",
                    confidence=edge.confidence,
                ))
                queue.append((edge.callee, depth + 1, exec_node))

        # Primary path: top-scored nodes, capped at 15 for readable BPMN output
        sorted_nodes = sorted(scenario.nodes, key=lambda n: n.confidence, reverse=True)
        scenario.primary_path = [n.id for n in sorted_nodes[:15]]

        scenario.external_interfaces = list(set(
            n.name for n in scenario.nodes if n.type in ("DB", "EXTERNAL_API", "QUEUE")
        ))

        return scenario

    @staticmethod
    def _score_node(
        exec_node: ExecutionNode,
        depth: int,
        prev_node: ExecutionNode | None,
        state_writes: dict[str, str],
    ) -> float:
        """Compute a multi-signal confidence score for a node in a scenario.

        Signals (from ENHANCEMENT_PLAN Pillar B):
          +5 call order  (entry/shallow nodes score higher)
          +4 state transition present
          +3 read-after-write detected (node reads a field written upstream)
          +3 data boundary (DB or QUEUE)
          +2 external API
        """
        score = 0.0
        # Call order: entry (depth=0) gets +5, each level costs 1 point
        score += max(0.0, 5.0 - depth)
        # State transition signal
        if exec_node.state_transition:
            score += 4.0
        # Data boundary signals
        if exec_node.type in ("DB", "QUEUE"):
            score += 3.0
        elif exec_node.type == "EXTERNAL_API":
            score += 2.0
        # Read-after-write: if this node's source mentions a field written upstream
        if state_writes and exec_node.file_path:
            for field, value in state_writes.items():
                # Simple heuristic: if the node name or type suggests a read on this state
                if field.lower() in exec_node.name.lower():
                    score += 3.0
                    break
        return score

    def _create_execution_node(self, node_name: str, node: CodeNode | None) -> ExecutionNode:
        """Convert a CodeNode into an ExecutionNode, enriching with hints."""
        if not node:
            # Unresolved/External
            return ExecutionNode(
                id=node_name,
                type="UNRESOLVED",
                name=node_name.split(".")[-1],
                qualified_name=node_name,
                confidence=0.5
            )

        node_type = "FUNCTION"
        if node.node_type in ("endpoint", "batch_job", "cli_command", "event_consumer"):
            node_type = "ENTRY"

        # Enrich from hints — pick the highest-priority boundary type
        hints = node.framework_hints
        boundaries = hints.get("boundaries", [])
        if boundaries:
            _BOUNDARY_PRIORITY: dict[str, int] = {"DB": 3, "QUEUE": 2, "EXTERNAL_API": 1}
            best = max(boundaries, key=lambda b: _BOUNDARY_PRIORITY.get(b["type"], 0))
            node_type = best["type"]

        # State transition handling
        transition = None
        transitions = hints.get("transitions", [])
        if transitions:
            t = transitions[0]
            transition = StateTransition(
                entity=t["entity"],
                field=t["field"],
                to_state=t["value"],
                trigger_function=node.qualified_name
            )

        return ExecutionNode(
            id=node.qualified_name,
            type=node_type,
            name=node.name,
            qualified_name=node.qualified_name,
            file_path=node.file_path,
            line_number=node.line_start,
            summary=None, # will be filled by Tier 1/2
            domain=node.domain,
            state_transition=transition
        )

    def _get_trigger_type(self, node: CodeNode) -> str:
        if node.node_type == "endpoint":
            return "HTTP"
        if node.node_type == "batch_job":
            return "SCHEDULED"
        if node.node_type == "event_consumer":
            return "EVENT"
        if node.node_type == "cli_command":
            return "CLI"
        name_lower = node.name.lower()
        if any(h in name_lower for h in _EVENT_HINTS):
            return "EVENT"
        return "CLI"
