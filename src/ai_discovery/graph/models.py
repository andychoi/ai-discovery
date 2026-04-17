from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CodeNode:
    file_path: str
    language: str
    node_type: str  # class, method, endpoint, db_model, batch_job, ui_component, function
    name: str
    qualified_name: str
    source_code: str
    line_start: int
    line_end: int
    calls: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    return_type: str | None = None
    framework_hints: dict = field(default_factory=dict)
    domain: str | None = None


@dataclass
class CodeChunk:
    text: str
    chunk_index: int
    chunk_type: str  # class, method, config, schema, test
    file_path: str
    language: str
    qualified_name: str
    parent_class: str | None = None
    domain: str | None = None
    node_type: str = ""
    annotations: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    called_by: list[str] = field(default_factory=list)
    framework_hints: dict = field(default_factory=dict)
    token_estimate: int = 0


@dataclass
class CallEdge:
    caller: str
    callee: str
    edge_type: str  # direct_call, interface_impl, di_injection, event_emit, http_call
    confidence: float = 1.0


@dataclass
class StateTransition:
    entity: str
    field: str
    from_state: str | None = None
    to_state: str | None = None
    trigger_function: str | None = None
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)


@dataclass
class ExecutionNode:
    id: str
    type: str  # ENTRY, FUNCTION, DB, EXTERNAL_API, QUEUE, FILE, MANUAL, TRANSITION
    name: str
    qualified_name: str | None = None
    file_path: str | None = None
    line_number: int | None = None
    summary: str | None = None
    domain: str | None = None
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    state_transition: StateTransition | None = None


@dataclass
class ExecutionEdge:
    from_node: str
    to_node: str
    edge_type: str  # CALL, ASYNC, DATA_FLOW, CONDITIONAL, TRANSITION_LINK
    condition: str | None = None
    data_payload: dict = field(default_factory=dict)
    confidence: float = 1.0


@dataclass
class Scenario:
    scenario_id: str
    name: str
    entry_point: str  # qualified_name or route
    trigger_type: str  # HTTP, SCHEDULED, EVENT, CLI, UI
    nodes: list[ExecutionNode] = field(default_factory=list)
    edges: list[ExecutionEdge] = field(default_factory=list)
    primary_path: list[str] = field(default_factory=list)  # list of node IDs
    alternate_paths: list[dict] = field(default_factory=list)  # list of {condition: str, path: list[str]}
    external_interfaces: list[str] = field(default_factory=list)
    domain: str | None = None
    confidence: float = 1.0


@dataclass
class Domain:
    name: str
    nodes: list[CodeNode] = field(default_factory=list)
    internal_edges: list[CallEdge] = field(default_factory=list)
    external_edges: list[CallEdge] = field(default_factory=list)
    entry_points: list[CodeNode] = field(default_factory=list)
    db_models: list[CodeNode] = field(default_factory=list)
    tech_stack: dict = field(default_factory=dict)
