"""Mermaid diagrams (sequence, flowchart), IPO markdown, entity FSM/backbone and flow statistics input.

There is no BPMN here (ADR-0007, M3.11). A scenario flow narrates one execution slice, and a call slice is not a
process model: it cannot tell sequence from choice, or see people, waits or timers. The diagrams draw steps in
reading order. A step carries a ``condition`` only when that condition is a parsed guard. It is drawn as an
``opt`` block (sequence) or an edge label (flowchart), never as a gateway with invented arms.
"""

from __future__ import annotations

import re

from ..ai.flow_analyzer import ScenarioFlow
from ..ai.flow_steps import OTHERWISE, normalize_flat_steps, normalize_structured_steps
from ..graph.models import CrossEntityTransitionLink, EntityStateMachine


# Map step types to Mermaid sequence diagram participants
_STEP_ACTOR: dict[str, str] = {
    "ENTRY": "User",
    "DB": "Database",
    "EXTERNAL": "ExternalAPI",
    "EXTERNAL_API": "ExternalAPI",
    "QUEUE": "Queue",
    "UNRESOLVED": "Unknown",
}
_DEFAULT_ACTOR = "System"


# Mermaid shape delimiters per entity_kind (Phase 2e-2 taxonomy).
# `transactional` here is the *no-parsed-transitions* case; with transitions,
# render as a subgraph of state circles instead.
_KIND_SHAPE: dict[str, tuple[str, str]] = {
    "transactional": ("((", "))"),   # circle — lifecycle node
    "master": ("[(", ")]"),          # cylinder — data store (user-maintained)
    "summary": ("[(", ")]"),         # cylinder — aggregate/view
    "key": ("{{", "}}"),             # hexagon — decision input (seeded reference)
    "staging": ("[/", "\\]"),        # parallelogram — infrastructure/ephemeral
    "event": (">", "]"),             # asymmetric — message/signal event
    "junction": ("((", "))"),        # small circle — relationship
    "config": ("[\\", "/]"),         # parallelogram alt — configuration
    "unknown": ("[", "]"),           # rectangle — fallback
}


_MERMAID_SAFE_RE = re.compile(r"[^A-Za-z0-9]")


def _safe_mermaid_id(text: str) -> str:
    """Collapse any string into a Mermaid-safe identifier."""
    clean = _MERMAID_SAFE_RE.sub("_", text or "")
    clean = re.sub(r"_+", "_", clean).strip("_")
    return clean or "n"


def _mermaid_label(text: str | None) -> str:
    """Escape a label for use inside a Mermaid "..."-quoted label."""
    if not text:
        return "?"
    return text.replace("\"", "'").replace("\n", " ").strip()


def _when(step: dict) -> str | None:
    cond = step.get("condition")
    if not cond:
        return None
    return OTHERWISE if cond == OTHERWISE else f"when {_mermaid_label(str(cond))}"


class FlowDiagrams:
    """Renders ScenarioFlow objects (and entity FSMs) as Mermaid / markdown."""

    # ------------------------------------------------------------------
    # Mermaid sequence diagram
    # ------------------------------------------------------------------

    def _step_actor(self, step: dict) -> str:
        return _STEP_ACTOR.get(step.get("type", ""), _DEFAULT_ACTOR)

    def generate_mermaid_sequence(self, flow: ScenarioFlow) -> str:
        """Mermaid sequence diagram with real message arrows.

        With `structured_steps`, LOOP nodes become `loop` blocks and PHASE wrappers are flattened. A step with a
        guard `condition` is wrapped in an `opt` block. Otherwise each flat step is rendered in order.
        """
        structured = normalize_structured_steps(getattr(flow, "structured_steps", None))
        steps = normalize_flat_steps(flow.steps)
        if structured:
            return self._sequence_from_structured(structured)
        if not steps:
            return "sequenceDiagram\n    Note over System: No steps"

        lines = ["sequenceDiagram", "    autonumber"]
        seen: dict[str, None] = {}
        for step in steps:
            seen[self._step_actor(step)] = None
        lines.extend(f"    participant {actor}" for actor in seen)
        prev = "User"
        for step in steps:
            prev = self._message(lines, "    ", prev, step)
        return "\n".join(lines)

    def _message(self, lines: list[str], pad: str, prev: str, step: dict) -> str:
        actor = self._step_actor(step)
        msg = f"{prev}->>{actor}: {_mermaid_label(step.get('name', 'Step'))}"
        when = _when(step)
        if when:
            lines.extend([f"{pad}opt {when}", f"{pad}    {msg}", f"{pad}end"])
        else:
            lines.append(f"{pad}{msg}")
        return actor

    def _sequence_from_structured(self, structured: list[dict]) -> str:
        """Sequence diagram from normalized hierarchical steps (see caller)."""
        seen: dict[str, None] = {"User": None}

        def collect(seq: list[dict]) -> None:
            for node in seq:
                if node["type"] not in ("PHASE", "LOOP"):
                    seen[self._step_actor(node)] = None
                collect(node.get("children", []))

        collect(structured)
        if len(seen) <= 1:  # only the synthetic User, no real steps
            return "sequenceDiagram\n    Note over System: No steps"

        lines = ["sequenceDiagram", "    autonumber"]
        lines.extend(f"    participant {actor}" for actor in seen)
        state = {"prev": "User"}

        def render(seq: list[dict], depth: int) -> None:
            pad = "    " + "    " * depth
            for node in seq:
                if node["type"] == "PHASE":
                    render(node.get("children", []), depth)
                elif node["type"] == "LOOP":
                    lines.append(f"{pad}loop {_mermaid_label(node.get('name', 'each item'))}")
                    render(node.get("children", []), depth + 1)
                    lines.append(f"{pad}end")
                else:
                    state["prev"] = self._message(lines, pad, state["prev"], node)
                    render(node.get("children", []), depth)

        render(structured, 0)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Mermaid flowchart activity diagram
    # ------------------------------------------------------------------

    def generate_mermaid_flowchart(self, flow: ScenarioFlow) -> str:
        """Mermaid flowchart: steps in reading order, a guard `condition` as the label of the edge into its step,
        LOOP nodes with a dashed `repeat` back-edge. No decision diamonds (ADR-0007).

        The terminal node is named `end_node` because `end` is a reserved keyword in Mermaid.
        """
        structured = normalize_structured_steps(getattr(flow, "structured_steps", None))
        if not structured:
            structured = [{k: v for k, v in s.items() if k != "step"} for s in normalize_flat_steps(flow.steps)]
        return self._flowchart_from_structured(structured)

    def _flowchart_from_structured(self, structured: list[dict]) -> str:
        nodes: list[str] = ['start(("Start"))']
        edges: list[str] = []
        ctr = {"n": 0}

        def new_id(prefix: str) -> str:
            ctr["n"] += 1
            return f"{prefix}_{ctr['n']}"

        def link(targets: list[str], dest: str, label: str | None) -> None:
            lbl = f"|{label}|" if label else ""
            for src in targets:
                edges.append(f"{src} -->{lbl} {dest}")

        def render(seq: list[dict], prev_ids: list[str]) -> list[str]:
            for node in seq:
                label = _mermaid_label(node.get("name", "Step"))
                if node["type"] == "PHASE":
                    prev_ids = render(node.get("children", []), prev_ids)
                elif node["type"] == "LOOP":
                    lid = new_id("loop")
                    nodes.append(f'{lid}["⟳ for each: {label}"]')
                    link(prev_ids, lid, _when(node))
                    body_exits = render(node.get("children", []), [lid])
                    for ex in body_exits:  # dashed back-edge conveys iteration without a real cycle
                        if ex != lid:
                            edges.append(f"{ex} -.->|repeat| {lid}")
                    prev_ids = body_exits or [lid]
                else:
                    sid = new_id("step")
                    nodes.append(f'{sid}["{label}"]')
                    link(prev_ids, sid, _when(node))
                    prev_ids = render(node.get("children", []), [sid])
            return prev_ids

        exit_ids = render(structured, ["start"])
        nodes.append('end_node(("End"))')
        link(exit_ids or ["start"], "end_node", None)

        out = ["flowchart TD"]
        out.extend(f"    {n}" for n in nodes)
        out.extend(f"    {e}" for e in edges)
        return "\n".join(out)

    # ------------------------------------------------------------------
    # IPO markdown table
    # ------------------------------------------------------------------

    def generate_ipo_markdown(self, flow: ScenarioFlow) -> str:
        """IPO in Markdown: Input/Output as a compact table, Process as a leveled bullet list from
        `structured_steps` (phases → steps, a guard as `when …:`), else the flat process list."""
        lines = [
            "### Input-Process-Output (IPO)",
            "| Component | Description |",
            "|-----------|-------------|",
            f"| **Input** | {', '.join(flow.input) or '—'} |",
            f"| **Output** | {', '.join(flow.output) or '—'} |",
        ]
        lines.append("")
        lines.append("#### Process")
        structured = normalize_structured_steps(getattr(flow, "structured_steps", None))
        if structured:
            lines.extend(self._render_process_tree(structured))
        elif flow.process:
            lines.extend(f"- {p}" for p in flow.process)
        else:
            lines.append("—")

        if flow.data_flow:
            lines.append("")
            lines.append("#### Data State Transitions")
            lines.extend(f"- {df}" for df in flow.data_flow)
        return "\n".join(lines)

    @staticmethod
    def _render_process_tree(structured: list[dict], indent: int = 0) -> list[str]:
        """Normalized hierarchical steps as a nested Markdown bullet list."""
        out: list[str] = []
        pad = "  " * indent
        for node in structured:
            name, ntype, desc = node.get("name", "Step"), node["type"], node.get("description", "")
            label = f"for each: {name}" if ntype == "LOOP" else (
                f"{name} ({ntype})" if ntype in ("DB", "EXTERNAL", "EXTERNAL_API", "QUEUE") else name)
            if desc and ntype != "PHASE":
                label = f"{label} — {desc}"
            if node.get("condition"):
                cond = node["condition"]
                label = f"{OTHERWISE if cond == OTHERWISE else 'when ' + str(cond)}: {label}"
            out.append(f"{pad}- {label}")
            out.extend(FlowDiagrams._render_process_tree(node.get("children", []), indent + 1))
        return out

    # ------------------------------------------------------------------
    # State Machine (FSM) diagram
    # ------------------------------------------------------------------

    def generate_fsm_diagram(self, state_transitions: list[dict]) -> str:
        """Generate a Mermaid state machine diagram from state transitions.

        Args:
            state_transitions: List of { entity, field, from_state, to_state, trigger_function }

        Returns:
            Mermaid stateDiagram syntax
        """
        if not state_transitions:
            return "stateDiagram-v2\n    [*] --> NoTransitions"

        lines = ["stateDiagram-v2"]
        seen_transitions: set[tuple[str, str, str]] = set()

        for trans in state_transitions:
            from_state = trans.get("from_state", "UNKNOWN")
            to_state = trans.get("to_state", "UNKNOWN")
            trigger = trans.get("trigger_function", "transition")

            # Avoid duplicate transitions
            key = (from_state, to_state, trigger)
            if key in seen_transitions:
                continue
            seen_transitions.add(key)

            # Mermaid format: State1 --> State2: trigger
            lines.append(f'    {from_state} --> {to_state}: {trigger}')

        # Ensure there's always a start state
        first_trans = state_transitions[0]
        start_state = first_trans.get("from_state", "START")
        lines.insert(1, f'    [*] --> {start_state}')

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Entity-backbone L1/L2 view (Phase 4: first consumer of entity_kind
    # + cross_entity_transitions)
    # ------------------------------------------------------------------

    def generate_entity_backbone_mermaid(
        self,
        fsms: list[EntityStateMachine],
        cross_links: list[CrossEntityTransitionLink] | None = None,
    ) -> str:
        """L1/L2 end-to-end process view, organized by entity rather than scenario.

        Each transactional FSM with parsed transitions becomes a subgraph of
        state circles; other kinds render as shape-coded data nodes (master/
        summary → cylinder, key → hexagon, staging/config → parallelogram,
        event → asymmetric, junction → small circle). Cross-entity correlator
        links draw dashed `triggers` edges between the relevant state nodes.

        Deterministic ordering (sorted by `entity_id`) keeps the output
        diff-stable across scans.
        """
        cross_links = cross_links or []
        if not fsms:
            return "flowchart LR\n    empty[\"No entities discovered\"]"

        sorted_fsms = sorted(fsms, key=lambda f: (f.entity_id or f.entity, f.entity))

        lines: list[str] = [
            "flowchart LR",
            "    %% Entity backbone — L1/L2 process view.",
            "    %% Subgraph = transactional FSM. Cylinder = data store.",
            "    %% Hexagon = key/reference data. Parallelogram = staging/config.",
            "    classDef transactional fill:#e3f2fd,stroke:#1976d2,color:#0d47a1",
            "    classDef master fill:#fff3e0,stroke:#ef6c00,color:#e65100",
            "    classDef summary fill:#f3e5f5,stroke:#7b1fa2,color:#4a148c",
            "    classDef key fill:#fce4ec,stroke:#c2185b,color:#880e4f",
            "    classDef staging fill:#eceff1,stroke:#546e7a,color:#263238,stroke-dasharray:3 3",
            "    classDef event fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20",
            "    classDef junction fill:#f5f5f5,stroke:#757575,color:#424242",
            "    classDef config fill:#fffde7,stroke:#f9a825,color:#f57f17",
            "    classDef unknown fill:#ffffff,stroke:#bdbdbd,color:#616161",
        ]

        # Map (group_key, state) → state-node id and group_key → entity-node id
        # so cross-entity edges can target the right node. `group_key` uses
        # `entity_id` when present (canonical after Phase 2d consolidation),
        # falling back to display name.
        state_node_ids: dict[tuple[str, str], str] = {}
        entity_node_ids: dict[str, str] = {}

        for fsm in sorted_fsms:
            kind = (fsm.metadata or {}).get("entity_kind") or "unknown"
            group_key = fsm.entity_id or fsm.entity
            safe = _safe_mermaid_id(group_key)
            display = _mermaid_label(fsm.entity)

            if kind == "transactional" and fsm.transitions:
                lines.append(f'    subgraph {safe}_fsm ["{display} (transactional)"]')
                # Collect ordered unique states (preserve first-seen order for readability)
                seen_states: list[str] = []
                for t in fsm.transitions:
                    for s in (t.from_state, t.to_state):
                        if s and s not in seen_states:
                            seen_states.append(s)
                for st in seen_states:
                    sid = f"{safe}_s_{_safe_mermaid_id(st)}"
                    state_node_ids[(group_key, st)] = sid
                    lines.append(f'        {sid}(("{_mermaid_label(st)}"))')
                # Dedupe transitions by (from, to); skip half-known edges
                seen_pairs: set[tuple[str, str]] = set()
                for t in fsm.transitions:
                    if not t.from_state or not t.to_state:
                        continue
                    pair = (t.from_state, t.to_state)
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    from_id = state_node_ids[(group_key, t.from_state)]
                    to_id = state_node_ids[(group_key, t.to_state)]
                    lines.append(f"        {from_id} --> {to_id}")
                lines.append("    end")
                for st in seen_states:
                    lines.append(f"    class {state_node_ids[(group_key, st)]} transactional")
            else:
                shape_open, shape_close = _KIND_SHAPE.get(kind, _KIND_SHAPE["unknown"])
                eid = f"{safe}_node"
                entity_node_ids[group_key] = eid
                label = f"{display}<br/><i>{kind}</i>"
                lines.append(f'    {eid}{shape_open}"{label}"{shape_close}')
                lines.append(f"    class {eid} {kind}")

        # Cross-entity trigger edges (dashed, with support count as label)
        for link in sorted(
            cross_links,
            key=lambda l: (
                l.from_entity_id,
                l.from_state or "",
                l.to_entity_id,
                l.to_state or "",
            ),
        ):
            from_id = (
                state_node_ids.get((link.from_entity_id, link.from_state or ""))
                or entity_node_ids.get(link.from_entity_id)
            )
            to_id = (
                state_node_ids.get((link.to_entity_id, link.to_state or ""))
                or entity_node_ids.get(link.to_entity_id)
            )
            if not from_id or not to_id:
                continue
            lines.append(f"    {from_id} -. triggers ({link.support}) .-> {to_id}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Pseudo Event Log (Phase 16 flow-statistics input)
    # ------------------------------------------------------------------

    def generate_pseudo_event_log(self, flow: ScenarioFlow) -> dict:
        """Generate a pseudo event log for Phase 16 flow statistics.

        Returns:
            { "case_id": scenario_id, "events": [{event_name, order}, ...] }
        """
        events = []
        for i, step in enumerate(flow.steps, 1):
            events.append({
                "order": i,
                "event_name": step.get("name", f"Step_{i}"),
                "event_type": step.get("type", "PROCESS"),
            })

        return {
            "case_id": flow.scenario_id,
            "process_name": flow.scenario_id,
            "variant": "main",
            "events": events,
        }
