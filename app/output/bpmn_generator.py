"""Generates BPMN 2.0, Mermaid, and PlantUML diagrams from ScenarioFlow."""

from __future__ import annotations

import html

from ..ai.flow_analyzer import ScenarioFlow

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


class BPMNGenerator:
    """Converts ScenarioFlow objects into visual/structured process models."""

    # ------------------------------------------------------------------
    # Mermaid sequence diagram
    # ------------------------------------------------------------------

    def _step_actor(self, step: dict) -> str:
        return _STEP_ACTOR.get(step.get("type", ""), _DEFAULT_ACTOR)

    def generate_mermaid_sequence(self, flow: ScenarioFlow) -> str:
        """Generate a Mermaid sequence diagram with real message arrows."""
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
            name = step.get("name", "Step")
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
            name_attr = html.escape(step.get("name", "Step"), quote=True)
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
    # PlantUML activity diagram
    # ------------------------------------------------------------------

    def generate_plantuml(self, flow: ScenarioFlow) -> str:
        """Generate a PlantUML activity diagram."""
        lines = ["@startuml", f"title {flow.scenario_id}", "start"]
        for step in flow.steps:
            name = step.get("name", "Step")
            if step.get("type") == "GATEWAY":
                lines.append(f"if ({name}?) then (yes)")
                lines.append("  :Continue;")
                lines.append("else (no)")
                lines.append("  :Skip;")
                lines.append("endif")
            else:
                lines.append(f":{name};")
        lines.extend(["stop", "@enduml"])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # IPO markdown table
    # ------------------------------------------------------------------

    def generate_ipo_markdown(self, flow: ScenarioFlow) -> str:
        """Generate IPO table in Markdown."""
        lines = [
            "### Input-Process-Output (IPO)",
            "| Component | Description |",
            "|-----------|-------------|",
            f"| **Input** | {', '.join(flow.input) or '—'} |",
            f"| **Process** | {', '.join(flow.process) or '—'} |",
            f"| **Output** | {', '.join(flow.output) or '—'} |",
        ]
        if flow.data_flow:
            lines.append("")
            lines.append("#### Data State Transitions")
            lines.extend(f"- {df}" for df in flow.data_flow)
        return "\n".join(lines)

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
    # Pseudo Event Log (for PM4Py / process mining)
    # ------------------------------------------------------------------

    def generate_pseudo_event_log(self, flow: ScenarioFlow) -> dict:
        """Generate a pseudo event log for process mining tools (PM4Py).

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
