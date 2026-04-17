"""Tests for BPMNGenerator."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from ai_discovery.ai.flow_analyzer import ScenarioFlow
from ai_discovery.output.bpmn_generator import BPMNGenerator


def _make_flow(steps: list[dict] | None = None) -> ScenarioFlow:
    return ScenarioFlow(
        scenario_id="scenario_test_1",
        domain="orders",
        steps=steps or [
            {"step": 1, "name": "Validate Order", "type": "PROCESS", "description": "Check inputs"},
            {"step": 2, "name": "Persist Order", "type": "DB", "description": "Save to DB"},
            {"step": 3, "name": "Notify Customer", "type": "EXTERNAL", "description": "Send email"},
        ],
        input=["order_id", "customer_id"],
        process=["validate", "persist"],
        output=["confirmation"],
        data_flow=["order -> database", "database -> notification"],
    )


@pytest.fixture
def gen() -> BPMNGenerator:
    return BPMNGenerator()


# ---------------------------------------------------------------------------
# Mermaid sequence diagram
# ---------------------------------------------------------------------------


def test_mermaid_contains_real_arrows(gen: BPMNGenerator):
    flow = _make_flow()
    diagram = gen.generate_mermaid_sequence(flow)
    assert "->>" in diagram, "Expected real sequence arrows (->>) in Mermaid output"


def test_mermaid_starts_with_sequence_diagram(gen: BPMNGenerator):
    flow = _make_flow()
    diagram = gen.generate_mermaid_sequence(flow)
    assert diagram.startswith("sequenceDiagram")


def test_mermaid_declares_participants(gen: BPMNGenerator):
    flow = _make_flow()
    diagram = gen.generate_mermaid_sequence(flow)
    assert "participant" in diagram


def test_mermaid_gateway_uses_alt_block(gen: BPMNGenerator):
    flow = _make_flow(steps=[
        {"step": 1, "name": "Check Stock", "type": "GATEWAY", "description": "Available?"},
    ])
    diagram = gen.generate_mermaid_sequence(flow)
    assert "alt" in diagram


def test_mermaid_empty_steps_returns_fallback(gen: BPMNGenerator):
    flow = _make_flow(steps=[])
    diagram = gen.generate_mermaid_sequence(flow)
    assert "sequenceDiagram" in diagram


# ---------------------------------------------------------------------------
# BPMN 2.0 XML
# ---------------------------------------------------------------------------


def test_bpmn_is_valid_xml(gen: BPMNGenerator):
    flow = _make_flow()
    xml_str = gen.generate_bpmn_xml(flow)
    ET.fromstring(xml_str)  # raises if invalid


def test_bpmn_has_start_and_end_events(gen: BPMNGenerator):
    flow = _make_flow()
    xml_str = gen.generate_bpmn_xml(flow)
    assert "startEvent" in xml_str
    assert "endEvent" in xml_str


def test_bpmn_gateway_step_produces_exclusive_gateway(gen: BPMNGenerator):
    flow = _make_flow(steps=[
        {"step": 1, "name": "Check", "type": "GATEWAY", "description": "Decision"},
    ])
    xml_str = gen.generate_bpmn_xml(flow)
    assert "exclusiveGateway" in xml_str


def test_bpmn_user_task_step_produces_user_task(gen: BPMNGenerator):
    flow = _make_flow(steps=[
        {"step": 1, "name": "Approve", "type": "USER_TASK", "description": "Manual approval"},
    ])
    xml_str = gen.generate_bpmn_xml(flow)
    assert "userTask" in xml_str


def test_bpmn_special_chars_are_escaped(gen: BPMNGenerator):
    flow = _make_flow(steps=[
        {"step": 1, "name": 'Check "quota" & limits <max>', "type": "PROCESS", "description": ""},
    ])
    xml_str = gen.generate_bpmn_xml(flow)
    ET.fromstring(xml_str)  # would raise if unescaped < or & in attribute
    assert "&amp;" in xml_str or "&lt;" in xml_str or "&quot;" in xml_str


def test_bpmn_default_step_is_service_task(gen: BPMNGenerator):
    flow = _make_flow(steps=[
        {"step": 1, "name": "Process", "type": "PROCESS", "description": ""},
    ])
    xml_str = gen.generate_bpmn_xml(flow)
    assert "serviceTask" in xml_str


# ---------------------------------------------------------------------------
# PlantUML
# ---------------------------------------------------------------------------


def test_plantuml_starts_and_ends_correctly(gen: BPMNGenerator):
    flow = _make_flow()
    puml = gen.generate_plantuml(flow)
    assert puml.startswith("@startuml")
    assert puml.strip().endswith("@enduml")


def test_plantuml_gateway_uses_if_block(gen: BPMNGenerator):
    flow = _make_flow(steps=[
        {"step": 1, "name": "Is Valid?", "type": "GATEWAY", "description": ""},
    ])
    puml = gen.generate_plantuml(flow)
    assert "if (" in puml
    assert "endif" in puml


def test_plantuml_contains_step_names(gen: BPMNGenerator):
    flow = _make_flow()
    puml = gen.generate_plantuml(flow)
    assert "Validate Order" in puml
    assert "Persist Order" in puml


# ---------------------------------------------------------------------------
# IPO markdown
# ---------------------------------------------------------------------------


def test_ipo_markdown_contains_all_sections(gen: BPMNGenerator):
    flow = _make_flow()
    md = gen.generate_ipo_markdown(flow)
    assert "Input" in md
    assert "Process" in md
    assert "Output" in md


def test_ipo_markdown_includes_data_flow(gen: BPMNGenerator):
    flow = _make_flow()
    md = gen.generate_ipo_markdown(flow)
    assert "order -> database" in md


def test_ipo_markdown_empty_fields_show_dash(gen: BPMNGenerator):
    flow = ScenarioFlow(scenario_id="empty", input=[], process=[], output=[])
    md = gen.generate_ipo_markdown(flow)
    assert "—" in md
