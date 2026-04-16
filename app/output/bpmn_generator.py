"""Generates BPMN 2.0 and Mermaid diagrams from ScenarioFlow."""

from __future__ import annotations

from typing import Any
from ..ai.flow_analyzer import ScenarioFlow


class BPMNGenerator:
    """Converts ScenarioFlow objects into visual/structured process models."""

    def generate_mermaid_sequence(self, flow: ScenarioFlow) -> str:
        """Generate Mermaid sequence diagram string."""
        lines = ["sequenceDiagram", "    autonumber"]
        
        # Identify participants (lanes)
        participants = set()
        for step in flow.steps:
            p = step.get("type", "System")
            participants.add(p)
            
        for p in sorted(participants):
            lines.append(f"    participant {p}")
            
        # Draw steps
        for step in flow.steps:
            name = step.get("name", "Unknown Step")
            p_type = step.get("type", "System")
            desc = step.get("description", "")
            lines.append(f"    Note over {p_type}: {name}")
            
        return "\n".join(lines)

    def generate_bpmn_xml(self, flow: ScenarioFlow) -> str:
        """Generate a minimal valid BPMN 2.0 XML."""
        xml = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" targetNamespace="http://bpmn.io/schema/bpmn">',
            f'  <bpmn:process id="{flow.scenario_id}" isExecutable="false">',
            '    <bpmn:startEvent id="StartEvent_1"/>'
        ]
        
        prev_id = "StartEvent_1"
        for i, step in enumerate(flow.steps):
            step_id = f"Activity_{i}"
            name = step.get("name", "Step")
            xml.append(f'    <bpmn:serviceTask id="{step_id}" name="{name}"/>')
            xml.append(f'    <bpmn:sequenceFlow id="Flow_{i}" sourceRef="{prev_id}" targetRef="{step_id}"/>')
            prev_id = step_id
            
        xml.append(f'    <bpmn:endEvent id="EndEvent_1"/>')
        xml.append(f'    <bpmn:sequenceFlow id="Flow_End" sourceRef="{prev_id}" targetRef="EndEvent_1"/>')
        xml.append('  </bpmn:process>')
        xml.append('</bpmn:definitions>')
        
        return "\n".join(xml)

    def generate_ipo_markdown(self, flow: ScenarioFlow) -> str:
        """Generate IPO table in Markdown."""
        lines = [
            "### Input-Process-Output (IPO)",
            "| Component | Description |",
            "|-----------|-------------|",
            f"| **Input** | {', '.join(flow.input)} |",
            f"| **Process** | {', '.join(flow.process)} |",
            f"| **Output** | {', '.join(flow.output)} |",
            "",
            "#### Data State Transitions",
            "\n".join([f"- {df}" for df in flow.data_flow])
        ]
        return "\n".join(lines)
