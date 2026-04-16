"""Build call graph from CodeNode.calls references."""

from __future__ import annotations

from collections import defaultdict

from .models import CallEdge, CodeNode, ExecutionEdge, ExecutionNode, Scenario, StateTransition


def build_call_graph(nodes: list[CodeNode]) -> list[CallEdge]:
    """Build call graph from CodeNode.calls references.

    1. Build a name-resolution index: qualified_name -> CodeNode, plus short name -> [CodeNode]
    2. For each node with calls, try to resolve each call:
       - Exact match on qualified_name -> confidence=1.0, edge_type="direct_call"
       - Match on short name (method name) -> confidence=0.8 (ambiguous)
       - No match -> still record edge with confidence=0.5 (unresolved/external)
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
            # Try short name match
            elif call_ref in short_name_index:
                candidates = short_name_index[call_ref]
                if len(candidates) == 1:
                    target = candidates[0]
                    edges.append(
                        CallEdge(
                            caller=node.qualified_name,
                            callee=target.qualified_name,
                            edge_type="direct_call",
                            confidence=0.8,
                        )
                    )
                else:
                    # Multiple candidates — create edge for each
                    for target in candidates:
                        if target.qualified_name == node.qualified_name:
                            continue  # skip self-reference
                        edges.append(
                            CallEdge(
                                caller=node.qualified_name,
                                callee=target.qualified_name,
                                edge_type="direct_call",
                                confidence=0.8,
                            )
                        )
            else:
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


def identify_scenarios(nodes: list[CodeNode]) -> list[CodeNode]:
    """Identify entry points for execution scenarios (endpoints, batch jobs)."""
    scenarios: list[CodeNode] = []
    for node in nodes:
        if node.node_type in ("endpoint", "batch_job"):
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
        """Perform bounded BFS/DFS to extract a scenario-specific slice."""
        scenario_id = f"scenario_{entry.name}_{entry.line_start}"
        scenario = Scenario(
            scenario_id=scenario_id,
            name=f"Flow: {entry.name}",
            entry_point=entry.qualified_name,
            trigger_type=self._get_trigger_type(entry),
            domain=entry.domain,
        )

        visited = set()
        queue = [(entry.qualified_name, 0)]  # (node_name, depth)
        
        while queue:
            node_name, depth = queue.pop(0)
            if node_name in visited or depth > max_depth:
                continue
            visited.add(node_name)

            # Resolve node (could be internal or external/unresolved)
            node = self.nodes.get(node_name)
            
            exec_node = self._create_execution_node(node_name, node)
            scenario.nodes.append(exec_node)

            if not node:
                continue

            # Process outgoing edges
            for edge in self.edges_by_caller.get(node_name, []):
                scenario.edges.append(ExecutionEdge(
                    from_node=node_name,
                    to_node=edge.callee,
                    edge_type="CALL",
                    confidence=edge.confidence
                ))
                queue.append((edge.callee, depth + 1))

        # After building graph, identify primary path (simplification)
        scenario.primary_path = [n.id for n in scenario.nodes]
        
        # Extract external interfaces
        scenario.external_interfaces = list(set(
            n.name for n in scenario.nodes if n.type in ("DB", "EXTERNAL_API", "QUEUE")
        ))

        return scenario

    def _create_execution_node(self, node_name: str, node: CodeNode | None) -> ExecutionNode:
        """Convert a CodeNode into an ExecutionNode, enriching with hints."""
        if not node:
            # Unresolved/External
            return ExecutionNode(
                id=node_name,
                type="EXTERNAL_API", # default assumption for unresolved
                name=node_name.split(".")[-1],
                qualified_name=node_name,
                confidence=0.5
            )

        node_type = "FUNCTION"
        if node.node_type == "endpoint":
            node_type = "ENTRY"
        elif node.node_type == "batch_job":
            node_type = "ENTRY"
            
        # Enrich from hints
        hints = node.framework_hints
        boundaries = hints.get("boundaries", [])
        if boundaries:
            # If it calls DB/External, it's a boundary node
            b_type = boundaries[0]["type"]
            node_type = b_type

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
        return "CLI"
