"""Generates BPMN 2.0 and Mermaid (sequence + flowchart) diagrams from ScenarioFlow."""

from __future__ import annotations

import html
import re

from ..ai.flow_analyzer import ScenarioFlow
from ..graph.models import CrossEntityTransitionLink, EntityStateMachine


def _step_display_name(step: dict, default: str = "Step") -> str:
    """Step name, annotated when it is an LLM-inferred manual task.

    HIGH-9: USER_TASK steps are invented by the LLM (approval/review), not
    grounded in source. They must render visibly distinct from code-grounded
    steps so a business reader doesn't read an inferred step as a documented
    fact. Other step types pass through unchanged.
    """
    name = step.get("name", default)
    if step.get("type") == "USER_TASK":
        return f"{name} ⚠ inferred"
    return name


# Map step types to Mermaid sequence diagram participants
_STEP_ACTOR: dict[str, str] = {
    "USER_TASK": "User",
    "ENTRY": "User",
    "DB": "Database",
    "EXTERNAL": "ExternalAPI",
    "EXTERNAL_API": "ExternalAPI",
    "QUEUE": "Queue",
    "UNRESOLVED": "Unknown",
}
_DEFAULT_ACTOR = "System"

# Priority order for BPMN element selection
_BPMN_ELEMENT: dict[str, str] = {
    "GATEWAY": "exclusiveGateway",
    "USER_TASK": "userTask",
}
_DEFAULT_BPMN_ELEMENT = "serviceTask"

# Map step types to swimlane assignment
_STEP_LANE: dict[str, str] = {
    "USER_TASK": "User",
    "ENTRY": "User",
    "DB": "External",
    "EXTERNAL_API": "External",
    "QUEUE": "External",
    "GATEWAY": "System",
}
_DEFAULT_LANE = "System"


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


class BPMNGenerator:
    """Converts ScenarioFlow objects into visual/structured process models."""

    # ------------------------------------------------------------------
    # Mermaid sequence diagram
    # ------------------------------------------------------------------

    def _step_actor(self, step: dict) -> str:
        return _STEP_ACTOR.get(step.get("type", ""), _DEFAULT_ACTOR)

    def generate_mermaid_sequence(self, flow: ScenarioFlow) -> str:
        """Generate a Mermaid sequence diagram with real message arrows.

        With `structured_steps`, GATEWAY arms render as `alt/else <condition>`
        blocks and LOOP nodes as `loop` blocks, nesting their substeps — so the
        diagram shows the conditional/iterative structure rather than a flat
        message stream. Falls back to the flat per-step rendering otherwise.
        """
        structured = getattr(flow, "structured_steps", None)
        if structured:
            return self._sequence_from_structured(structured)

        if not flow.steps:
            return "sequenceDiagram\n    Note over System: No steps"

        lines = ["sequenceDiagram", "    autonumber"]

        # Declare participants in order of first appearance, deduped
        seen: dict[str, None] = {}
        for step in flow.steps:
            seen[self._step_actor(step)] = None
        for actor in seen:
            lines.append(f"    participant {actor}")

        prev_actor = "User"
        for step in flow.steps:
            actor = self._step_actor(step)
            name = _step_display_name(step)
            desc = step.get("description", "")
            step_type = step.get("type", "")

            if step_type == "GATEWAY":
                lines.append(f"    alt {name}")
                lines.append(f"        {prev_actor}->>{actor}: {desc or name}")
                lines.append("    end")
            else:
                lines.append(f"    {prev_actor}->>{actor}: {name}")

            prev_actor = actor

        return "\n".join(lines)

    def _sequence_from_structured(self, structured: list[dict]) -> str:
        """Render a Mermaid sequence diagram from hierarchical steps (see caller)."""
        _WRAPPERS = {"PHASE", "GATEWAY", "LOOP"}

        # Declare participants in first-appearance order across the whole tree.
        seen: dict[str, None] = {"User": None}

        def collect(seq: list[dict]) -> None:
            for node in seq or []:
                if not isinstance(node, dict):
                    continue
                ntype = (node.get("type") or "PROCESS").upper()
                if ntype not in _WRAPPERS:
                    seen[self._step_actor(node)] = None
                for branch in node.get("branches", []) or []:
                    collect(branch.get("steps", []))
                collect(node.get("children", []))

        collect(structured)
        if len(seen) <= 1:  # only the synthetic User, no real steps
            return "sequenceDiagram\n    Note over System: No steps"

        lines = ["sequenceDiagram", "    autonumber"]
        lines.extend(f"    participant {actor}" for actor in seen)

        state = {"prev": "User"}

        def render(seq: list[dict], depth: int) -> None:
            pad = "    " + "    " * depth
            for node in seq or []:
                if not isinstance(node, dict):
                    continue
                ntype = (node.get("type") or "PROCESS").upper()
                if ntype == "PHASE":
                    render(node.get("children", []), depth)
                elif ntype == "GATEWAY" and node.get("branches"):
                    for i, branch in enumerate(node.get("branches", []) or []):
                        raw = str(branch.get("condition", "?"))
                        cond = _mermaid_label(raw)
                        if i == 0:
                            header = f"alt {cond}"
                        elif raw.strip().lower() == "else":
                            header = "else"  # avoid redundant "else else"
                        else:
                            header = f"else {cond}"
                        lines.append(f"{pad}{header}")
                        render(branch.get("steps", []), depth + 1)
                    lines.append(f"{pad}end")
                elif ntype == "LOOP":
                    lines.append(f"{pad}loop {_mermaid_label(node.get('name', 'each item'))}")
                    render(node.get("children", []), depth + 1)
                    lines.append(f"{pad}end")
                else:
                    actor = self._step_actor(node)
                    name = _step_display_name(node)
                    lines.append(f"{pad}{state['prev']}->>{actor}: {name}")
                    state["prev"] = actor
                    if node.get("children"):
                        render(node.get("children", []), depth)

        render(structured, 0)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # BPMN 2.0 XML
    # ------------------------------------------------------------------

    def generate_bpmn_xml(self, flow: ScenarioFlow) -> str:
        """Generate BPMN 2.0 XML with swimlanes and proper element types.

        Structure:
          - collaboration (if lanes present)
            - participant (per lane)
          - process
            - lanes (User, System, External)
            - startEvent, activities, endEvent
            - sequenceFlow
        """
        # Detect which lanes are actually used
        lanes_used: set[str] = {"System"}
        for step in flow.steps:
            step_type = step.get("type", "PROCESS")
            lane = _STEP_LANE.get(step_type, _DEFAULT_LANE)
            lanes_used.add(lane)

        lanes_list = sorted(lanes_used)
        use_lanes = len(lanes_list) > 1

        xml: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"'
            ' targetNamespace="http://bpmn.io/schema/bpmn">',
        ]

        if use_lanes:
            # Collaboration with participants (one per lane)
            xml.append('  <bpmn:collaboration id="Collaboration_1">')
            for lane in lanes_list:
                lane_id = lane.replace(" ", "")
                xml.append(f'    <bpmn:participant id="Participant_{lane_id}" name="{lane}" processRef="{html.escape(flow.scenario_id, quote=True)}"/>')
            xml.append("  </bpmn:collaboration>")

        xml.append(f'  <bpmn:process id="{html.escape(flow.scenario_id, quote=True)}" isExecutable="false">')

        # Add lanes (containers for activities)
        if use_lanes:
            xml.append('    <bpmn:laneSet id="LaneSet_1">')
            for lane in lanes_list:
                lane_id = lane.replace(" ", "")
                xml.append(f'      <bpmn:lane id="Lane_{lane_id}" name="{lane}">')
                # Placeholder for flowNodeRef assignments (per spec, optional for this minimal gen)
                xml.append(f'      </bpmn:lane>')
            xml.append("    </bpmn:laneSet>")

        # Generate process elements
        xml.append('    <bpmn:startEvent id="StartEvent_1" name="Start"/>')

        prev_id = "StartEvent_1"
        for i, step in enumerate(flow.steps):
            step_id = f"Activity_{i}"
            name_attr = html.escape(_step_display_name(step), quote=True)
            step_type = step.get("type", "PROCESS")
            element = _BPMN_ELEMENT.get(step_type, _DEFAULT_BPMN_ELEMENT)
            xml.append(f'    <bpmn:{element} id="{step_id}" name="{name_attr}"/>')
            xml.append(f'    <bpmn:sequenceFlow id="Flow_{i}" sourceRef="{prev_id}" targetRef="{step_id}"/>')
            prev_id = step_id

        xml.append('    <bpmn:endEvent id="EndEvent_1" name="End"/>')
        xml.append(f'    <bpmn:sequenceFlow id="Flow_End" sourceRef="{prev_id}" targetRef="EndEvent_1"/>')
        xml.append("  </bpmn:process>")
        xml.append("</bpmn:definitions>")

        return "\n".join(xml)

    # ------------------------------------------------------------------
    # Mermaid flowchart activity diagram
    # ------------------------------------------------------------------

    def generate_mermaid_flowchart(self, flow: ScenarioFlow) -> str:
        """Generate a Mermaid flowchart activity diagram.

        When `structured_steps` is present, renders the leveled hierarchy:
        GATEWAY diamonds carry their real branch conditions as edge labels and
        each branch's actual steps render inside the arm before reconverging;
        LOOP nodes get a dashed `repeat` back-edge. Falls back to the flat
        per-step rendering (generic yes/no arms) when no hierarchy was inferred.

        The terminal node is named `end_node` because `end` is a reserved
        keyword in Mermaid (used to close subgraph blocks).
        """
        structured = getattr(flow, "structured_steps", None)
        if structured:
            return self._flowchart_from_structured(structured)

        nodes: list[str] = ['start(("Start"))']
        edges: list[str] = []

        prev_ids: list[str] = ["start"]

        for i, step in enumerate(flow.steps):
            label = _mermaid_label(_step_display_name(step))
            if step.get("type") == "GATEWAY":
                gw_id = f"gw_{i}"
                yes_id = f"gw_{i}_yes"
                no_id = f"gw_{i}_no"
                nodes.append(f'{gw_id}{{"{label}"}}')
                nodes.append(f'{yes_id}["Continue"]')
                nodes.append(f'{no_id}["Skip"]')
                edges.extend(f"{p} --> {gw_id}" for p in prev_ids)
                edges.append(f"{gw_id} -->|yes| {yes_id}")
                edges.append(f"{gw_id} -->|no| {no_id}")
                prev_ids = [yes_id, no_id]
            else:
                step_id = f"step_{i}"
                nodes.append(f'{step_id}["{label}"]')
                edges.extend(f"{p} --> {step_id}" for p in prev_ids)
                prev_ids = [step_id]

        nodes.append('end_node(("End"))')
        edges.extend(f"{p} --> end_node" for p in prev_ids)

        out = ["flowchart TD"]
        out.extend(f"    {n}" for n in nodes)
        out.extend(f"    {e}" for e in edges)
        return "\n".join(out)

    def _flowchart_from_structured(self, structured: list[dict]) -> str:
        """Render a Mermaid flowchart from hierarchical steps (see caller)."""
        nodes: list[str] = ['start(("Start"))']
        edges: list[str] = []
        ctr = {"n": 0}

        def new_id(prefix: str) -> str:
            ctr["n"] += 1
            return f"{prefix}_{ctr['n']}"

        def link(targets: list[str], dest: str, label: str | None) -> None:
            lbl = f"|{_mermaid_label(label)}|" if label else ""
            for src in targets:
                edges.append(f"{src} -->{lbl} {dest}")

        def render(seq: list[dict], prev_ids: list[str], first_label: str | None = None) -> list[str]:
            """Return exit node ids after rendering `seq` chained from prev_ids.
            `first_label` annotates only the edge(s) into the first node drawn."""
            pending_label = first_label
            for node in seq or []:
                if not isinstance(node, dict):
                    continue
                ntype = (node.get("type") or "PROCESS").upper()
                label = _mermaid_label(node.get("name", "Step"))

                if ntype == "PHASE":
                    prev_ids = render(node.get("children", []), prev_ids, pending_label)
                    pending_label = None
                elif ntype == "GATEWAY" and node.get("branches"):
                    gid = new_id("gw")
                    nodes.append(f'{gid}{{"{label}"}}')
                    link(prev_ids, gid, pending_label)
                    pending_label = None
                    exits: list[str] = []
                    for branch in node.get("branches", []) or []:
                        cond = str(branch.get("condition", "?"))
                        exits.extend(render(branch.get("steps", []), [gid], cond) or [gid])
                    prev_ids = exits or [gid]
                elif ntype == "LOOP":
                    lid = new_id("loop")
                    nodes.append(f'{lid}["⟳ for each: {label}"]')
                    link(prev_ids, lid, pending_label)
                    pending_label = None
                    body_exits = render(node.get("children", []), [lid])
                    # dashed back-edge conveys iteration without a real cycle
                    for ex in body_exits:
                        if ex != lid:
                            edges.append(f"{ex} -.->|repeat| {lid}")
                    prev_ids = body_exits or [lid]
                else:
                    sid = new_id("step")
                    nodes.append(f'{sid}["{label}"]')
                    link(prev_ids, sid, pending_label)
                    pending_label = None
                    prev_ids = [sid]
                    if node.get("children"):
                        prev_ids = render(node.get("children", []), prev_ids)
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
        """Generate IPO in Markdown.

        Input/Output stay as a compact table. Process is rendered as a LEVELED
        bullet list from `structured_steps` (phases → steps → conditional branch
        arms) when available, so the processing logic reads at an appropriate
        altitude instead of collapsing into one comma-joined cell. Falls back to
        the flat comma-joined list when no hierarchy was inferred.
        """
        lines = [
            "### Input-Process-Output (IPO)",
            "| Component | Description |",
            "|-----------|-------------|",
            f"| **Input** | {', '.join(flow.input) or '—'} |",
            f"| **Output** | {', '.join(flow.output) or '—'} |",
        ]
        lines.append("")
        lines.append("#### Process")
        structured = getattr(flow, "structured_steps", None)
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
        """Render hierarchical steps as a nested Markdown bullet list.

        PHASE/LOOP wrappers and leaf steps become bullets; GATEWAY branch arms
        render as `if <condition>:` / `else:` sub-bullets containing their steps.
        """
        out: list[str] = []
        pad = "  " * indent
        for node in structured or []:
            if not isinstance(node, dict):
                continue
            name = node.get("name", "Step")
            ntype = (node.get("type") or "PROCESS").upper()
            desc = node.get("description", "")
            label = name
            if ntype == "LOOP":
                label = f"for each: {name}"
            elif ntype in ("DB", "EXTERNAL", "EXTERNAL_API", "QUEUE", "USER_TASK"):
                label = f"{name} ({ntype})"
            if desc and ntype not in ("PHASE",):
                label = f"{label} — {desc}"
            out.append(f"{pad}- {label}")

            # GATEWAY: render each branch arm with its real condition.
            for bi, branch in enumerate(node.get("branches", []) or []):
                cond = branch.get("condition", "?")
                kw = "else" if str(cond).strip().lower() == "else" else (
                    f"if {cond}" if bi == 0 else f"elif {cond}")
                out.append(f"{pad}  - {kw}:")
                out.extend(
                    BPMNGenerator._render_process_tree(branch.get("steps", []), indent + 2)
                )
            # Nested children (PHASE / LOOP bodies).
            out.extend(
                BPMNGenerator._render_process_tree(node.get("children", []), indent + 1)
            )
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
