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
    # Phase 1.1: structured call sites. Each entry: {"name": str, "receiver": str | None}.
    # The resolver prefers `call_sites` over `calls` when present, since `calls` is
    # deduped by short name and loses per-site receiver information.
    call_sites: list[dict] = field(default_factory=list)
    # Phase 1.1: module imports. Each entry: {"module": str, "name": str | None, "alias": str | None}.
    # `name` is None for a plain `import X`; set for `from X import Y`.
    # `alias` is set for `import X as Z` or `from X import Y as Z`.
    imports: list[dict] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    # Phase 2d: class attribute names (instance fields + class-level attrs/
    # properties). Populated on `class` / `db_model` / `ui_component` nodes only.
    # Used by `fsm_identity` to merge Order / OrderEntity / Orders into one FSM
    # via field-set fingerprinting.
    fields: list[str] = field(default_factory=list)
    # Phase 2d: superclass / interface / base-type names. `fsm_identity`
    # uses this to hard-exclude parent-child merges and to subtract inherited
    # fields before computing similarity — so Order {id, created_at, status}
    # and BaseEntity {id, created_at} don't get merged just because BaseEntity
    # contributed the shared fields. Names are unqualified (e.g. "BaseEntity"),
    # matching the granularity of other language-agnostic signals.
    bases: list[str] = field(default_factory=list)
    return_type: str | None = None
    framework_hints: dict = field(default_factory=dict)
    domain: str | None = None
    # A-2 incremental re-scan: SHA256 of the source file's bytes, stamped at
    # parse time. Empty for nodes without a backing file (OpenAPI/GraphQL/proto
    # extractor nodes) — blank hashes never participate in cross-scan reuse.
    file_hash: str = ""


@dataclass
class EntityRelationship:
    """A foreign-key / association edge between two entities (tables/models).

    Phase 1 of the FK-aware table-docs design (docs/specs/2026-05-31-fk-aware-table-docs.md):
    extracted deterministically from SQL `REFERENCES`, JPA `@JoinColumn`/`@ManyToOne`,
    or EF navigation properties, so table docs can reason over a table's FK
    neighborhood instead of documenting each table in isolation.

    Entity names are bare (schema/package stripped) to match how db_model and
    sql_table nodes are keyed elsewhere. `inferred=True` marks edges guessed from
    naming convention (e.g. `order_id` → `orders`) rather than a declared FK;
    those carry lower confidence and must be rendered as inferred, never verified.
    """
    from_entity: str
    to_entity: str
    from_field: str = ""        # FK column / owning field
    to_field: str = ""          # referenced column (usually the PK)
    cardinality: str = ""       # "N:1" | "1:N" | "1:1" | "N:M" | ""
    source: str = "sql"         # "sql" | "jpa" | "ef"
    source_file: str = ""
    source_line: int = 0
    confidence: float = 1.0
    inferred: bool = False

    def key(self) -> tuple[str, str, str, str]:
        """Dedup key: a relationship is identified by its endpoints + columns."""
        return (self.from_entity, self.from_field, self.to_entity, self.to_field)


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
    # Phase 1.1: resolver evidence. Keys include: "resolved_by" (stage name),
    # "receiver" (call-site receiver text), "import" (matched import dict).
    metadata: dict = field(default_factory=dict)


@dataclass
class StateTransition:
    # `entity` is the short display name (e.g. "Order"). It may collide across
    # modules — the unique dedup key is `entity_id`.
    entity: str
    field: str
    # Phase 2d: unique qualified identifier that never collides across modules.
    # - Class-backed transitions (`self.X`, `this.X`, `cls.X` inside a class):
    #   the enclosing class's `qualified_name` (e.g. "src.billing.order.Order").
    # - Classless/duck-typed transitions (`obj.X` where `obj` is a local/param):
    #   `"{enclosing_function.qualified_name}::{var}"`.
    # Defaults to `entity` so parser/rollup migration is incremental — once all
    # parsers populate it, callers should treat the empty default as a bug.
    entity_id: str = ""
    from_state: str | None = None
    to_state: str | None = None
    trigger_function: str | None = None
    confidence: float = 1.0
    guard_expr: str | None = None
    entry_points: list[dict] = field(default_factory=list)
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


@dataclass
class EntityStateMachine:
    """Aggregated lifecycle for a single business entity (Phase 2a).

    Holds every `StateTransition` observed on this entity across the repo, plus
    convenience aggregates (`states`, `fields`). Entity identity starts as the
    parser-supplied `entity` name; Phase 2d (`fsm_identity.consolidate_entities`)
    then merges name-variants (`Order` / `OrderEntity` / `Orders`) into one FSM
    via field-fingerprint matching, recording provenance in `metadata`.
    """
    entity: str
    # Phase 2d: unique grouping key. Matches each contained transition's
    # `entity_id`. Rollup groups on this; display uses `entity`. After
    # consolidation, the canonical `entity` may be a disambiguated form like
    # "Order (billing)" when two distinct entities share the short name.
    entity_id: str = ""
    transitions: list[StateTransition] = field(default_factory=list)
    states: set[str] = field(default_factory=set)
    fields: set[str] = field(default_factory=set)
    source_files: set[str] = field(default_factory=set)
    confidence: float = 1.0
    # Phase 2d provenance. Populated only on merged FSMs. Keys:
    #   - "consolidated_from": list[str] — original entity names that merged here
    #   - "source_node_types": list[str | None] — node_type per consolidated name
    #   - "merge_rule": str — e.g. "pass1:jaccard_0.93"
    #   - "projection_links": list[str] — related projection entities (DTOs, etc.)
    # Phase 2e-2 / 3.1a additions (see entity_classifier / entity_correlator):
    #   - "entity_kind", "entity_kind_confidence", "entity_kind_signals"
    #   - "denormalized_fields": list[dict] — copies from other entities
    metadata: dict = field(default_factory=dict)


@dataclass
class EntityConditionCorrelation:
    """Phase 3d: when a transition fires on entity Y, what state was entity X
    known to hold at that moment (from X's most recent transition earlier in
    the same scenario walk)? When a specific context state consistently
    precedes the target transition across scenarios, that's evidence the
    target is *conditioned* on the context — a natural DMN rule input.

    Example: across 5 scenarios, every time `Invoice` transitioned to `pending`,
    `Order` was in `submitted`. consistency=1.0, support=5 → DMN rule:
    "WHEN Order.status = submitted, Invoice.status → pending".

    `consistency` = support / (times the target fired while context had any
    known state for that field). Unlike `CrossEntityTransitionLink`'s
    `directional_confidence`, consistency measures *which specific context
    state* predicts the target, not just the direction of causality.
    """
    target_entity_id: str
    target_entity: str
    target_field: str
    target_to_state: str | None
    context_entity_id: str
    context_entity: str
    context_field: str
    context_state: str
    support: int
    consistency: float
    metadata: dict = field(default_factory=dict)


@dataclass
class CrossEntityTransitionLink:
    """Phase 3b: an ordered pair of transitions on two *different* entities
    that scenario walks consistently show co-occurring in the same direction.

    Example: across 4 out of 5 scenarios that include both, `Order` reaches
    `submitted` before `Invoice` reaches `pending`. That's strong evidence
    of a causal / sequential relationship that downstream generators should
    render as a BPMN sequence flow crossing swim lanes.

    Fields capture both endpoints symmetrically so BPMN / DMN generators can
    label the edge (`from_state` → `to_state`) without re-parsing the FSM.
    `support` and `directional_confidence` let consumers filter — for strict
    docs, require support ≥ 3 and directional ≥ 0.9; for exploratory views,
    include weaker links.
    """
    from_entity_id: str
    from_entity: str
    from_field: str
    from_state: str | None   # the to_state of the triggering transition
    to_entity_id: str
    to_entity: str
    to_field: str
    to_state: str | None     # the to_state of the triggered transition
    support: int             # scenarios where the pair appears in this order
    directional_confidence: float  # support / (support + reverse_count)
    metadata: dict = field(default_factory=dict)
