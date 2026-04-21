"""Phase 2.1: aggregate per-function StateTransitions into per-entity FSMs.

Every transition observed in the repo is evidence of one entity's lifecycle.
The rollup groups those observations by `entity_id` (Phase 2.4: a unique
qualified key — class qualified_name, or `enclosing_fn::var` for classless
receivers) and yields an `EntityStateMachine` per logical entity — the
canonical artifact the spec's state-first backbone hangs off. Downstream
BPMN / DMN / EARS generation consume these, not raw scenarios.

`entity` (short name) is a *display* label; `entity_id` is the grouping key.
Two classes named `Order` in different modules produce two distinct FSMs.

Deduplication key within an FSM: `(field, from_state, to_state, trigger_function)`.
Two transitions that reach the same state change from different functions are
kept as separate evidence (they'll become distinct BPMN lanes and DMN rows).
The same trigger seen twice (e.g. via multiple scenario walks) is collapsed,
preferring the observation with the higher confidence and more entry points.
"""

from __future__ import annotations

from collections import defaultdict

from .models import CodeNode, EntityStateMachine, StateTransition


def build_entity_state_machines(
    transitions: list[StateTransition],
    node_file_index: dict[str, str] | None = None,
) -> list[EntityStateMachine]:
    """Group `transitions` by `entity_id` into `EntityStateMachine` records.

    `node_file_index` maps a `trigger_function` qualified name to its source
    file path; when provided, FSMs accumulate `source_files` for provenance.
    Pipeline passes `{node.qualified_name: node.file_path for node in nodes}`.
    """
    by_key: dict[str, list[StateTransition]] = defaultdict(list)
    for t in transitions:
        if not t.entity:
            continue
        # Fall back to the short name when `entity_id` is missing — keeps old
        # fixtures working; production parsers always populate `entity_id`.
        key = t.entity_id or t.entity
        by_key[key].append(t)

    fsms: list[EntityStateMachine] = []
    for entity_id, entity_transitions in by_key.items():
        deduped = _dedupe_transitions(entity_transitions)
        # Display name: majority-vote across observed transitions. All should
        # agree in practice; if a misparse produced divergent shorts, the most
        # common one is the least surprising display.
        display = _majority_entity_name(deduped) or entity_id
        fsms.append(_build_fsm(display, entity_id, deduped, node_file_index or {}))

    # Sort by entity_id for stable diffs — `entity` (display) may be disambiguated
    # later by consolidation, but entity_id is stable at rollup time.
    fsms.sort(key=lambda f: (f.entity_id, f.entity))
    return fsms


def build_fsms_from_sql_nodes(sql_nodes: list[CodeNode]) -> list[EntityStateMachine]:
    """Synthesize placeholder FSMs for SQL-derived entities (Phase 2.5.1).

    SQL extractors find tables and views but not transitions — without a
    placeholder FSM, those entities never participate in consolidation. This
    emits one FSM per synthetic node with `fields` populated and an empty
    `transitions` list. The Phase 2.4 consolidator's Pass 1 treats `sql_table`
    and `sql_view` as class-like (see `fsm_identity._CLASS_LIKE_NODE_TYPES`),
    so when a class-backed FSM exists with overlapping fields and a matching
    stem, the two merge — and the real class's transitions carry through.

    A placeholder that never merges survives as a standalone entity record,
    which is the correct outcome for SQL-first codebases where no matching
    class exists. Confidence is capped below class-backed FSMs so downstream
    reviewers can filter by evidence strength.
    """
    fsms: list[EntityStateMachine] = []
    for node in sql_nodes:
        if not node.fields:
            continue
        fsms.append(EntityStateMachine(
            entity=node.name,
            entity_id=node.qualified_name,
            transitions=[],
            states=set(),
            fields=set(node.fields),
            source_files={node.file_path} if node.file_path else set(),
            confidence=0.7,
            metadata={
                "discovered_by": "sql_extractor",
                "node_type": node.node_type,
                "sql_ops": list(node.framework_hints.get("sql_ops", [])),
            },
        ))
    fsms.sort(key=lambda f: (f.entity_id, f.entity))
    return fsms


def _majority_entity_name(transitions: list[StateTransition]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for t in transitions:
        if t.entity:
            counts[t.entity] += 1
    if not counts:
        return ""
    # Tie-break alphabetically so output is deterministic.
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def _dedupe_transitions(transitions: list[StateTransition]) -> list[StateTransition]:
    """Collapse identical (field, from, to, trigger_function) observations.

    When duplicates exist, keep the one with the highest confidence; if tied,
    keep the one with the most entry_points (that scenario walk saw more of
    the call graph). This gives the canonical transition the richest evidence.
    """
    by_key: dict[tuple, StateTransition] = {}
    for t in transitions:
        key = (t.field, t.from_state, t.to_state, t.trigger_function)
        existing = by_key.get(key)
        if existing is None or _is_richer(t, existing):
            by_key[key] = t
    return list(by_key.values())


def _is_richer(candidate: StateTransition, incumbent: StateTransition) -> bool:
    if candidate.confidence > incumbent.confidence:
        return True
    if candidate.confidence < incumbent.confidence:
        return False
    return len(candidate.entry_points) > len(incumbent.entry_points)


def _build_fsm(
    entity: str,
    entity_id: str,
    transitions: list[StateTransition],
    node_file_index: dict[str, str],
) -> EntityStateMachine:
    states: set[str] = set()
    fields: set[str] = set()
    source_files: set[str] = set()
    for t in transitions:
        if t.from_state:
            states.add(t.from_state)
        if t.to_state:
            states.add(t.to_state)
        if t.field:
            fields.add(t.field)
        if t.trigger_function:
            fp = node_file_index.get(t.trigger_function)
            if fp:
                source_files.add(fp)

    # Average confidence over observed evidence; stable and cheap. Phase 2 exit
    # criterion asks for diffable artifacts, not a formal scoring model — that's
    # an Open Question (§Part 6) in the spec.
    confidence = (
        sum(t.confidence for t in transitions) / len(transitions)
        if transitions
        else 1.0
    )

    return EntityStateMachine(
        entity=entity,
        entity_id=entity_id,
        transitions=transitions,
        states=states,
        fields=fields,
        source_files=source_files,
        confidence=confidence,
    )
