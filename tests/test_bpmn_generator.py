"""Tests for BPMNGenerator."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from ai_discovery.ai.flow_analyzer import ScenarioFlow
from ai_discovery.graph.models import (
    CrossEntityTransitionLink,
    EntityStateMachine,
    StateTransition,
)
from ai_discovery.generators.bpmn_generator import BPMNGenerator


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
# Mermaid flowchart
# ---------------------------------------------------------------------------


def test_mermaid_flowchart_declares_direction_and_endpoints(gen: BPMNGenerator):
    flow = _make_flow()
    fc = gen.generate_mermaid_flowchart(flow)
    assert fc.startswith("flowchart TD")
    assert 'start(("Start"))' in fc
    assert 'end_node(("End"))' in fc


def test_mermaid_flowchart_gateway_renders_diamond_with_labeled_edges(gen: BPMNGenerator):
    flow = _make_flow(steps=[
        {"step": 1, "name": "Is Valid?", "type": "GATEWAY", "description": ""},
    ])
    fc = gen.generate_mermaid_flowchart(flow)
    # Diamond uses {"label"} syntax in Mermaid.
    assert 'gw_0{"Is Valid?"}' in fc
    assert "gw_0 -->|yes| gw_0_yes" in fc
    assert "gw_0 -->|no| gw_0_no" in fc
    # Both branches must reconverge on the terminal node.
    assert "gw_0_yes --> end_node" in fc
    assert "gw_0_no --> end_node" in fc


def test_mermaid_flowchart_contains_step_names(gen: BPMNGenerator):
    flow = _make_flow()
    fc = gen.generate_mermaid_flowchart(flow)
    assert "Validate Order" in fc
    assert "Persist Order" in fc


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


# ---------------------------------------------------------------------------
# Entity-backbone Mermaid (L1/L2 view, Phase 4)
# ---------------------------------------------------------------------------


def _fsm(
    entity: str,
    kind: str,
    *,
    entity_id: str | None = None,
    transitions: list[StateTransition] | None = None,
) -> EntityStateMachine:
    return EntityStateMachine(
        entity=entity,
        entity_id=entity_id or f"mod.{entity}",
        transitions=transitions or [],
        metadata={"entity_kind": kind},
    )


def _transition(entity: str, from_state: str | None, to_state: str) -> StateTransition:
    return StateTransition(
        entity=entity,
        entity_id=f"mod.{entity}",
        field="status",
        from_state=from_state,
        to_state=to_state,
        trigger_function=f"mod.{entity}_trans",
    )


def test_backbone_empty_returns_fallback(gen: BPMNGenerator):
    out = gen.generate_entity_backbone_mermaid([])
    assert out.startswith("flowchart LR")
    assert "No entities discovered" in out


def test_backbone_transactional_renders_subgraph_with_states(gen: BPMNGenerator):
    fsm = _fsm(
        "Order",
        "transactional",
        transitions=[
            _transition("Order", "draft", "submitted"),
            _transition("Order", "submitted", "approved"),
        ],
    )
    out = gen.generate_entity_backbone_mermaid([fsm])
    assert "subgraph mod_Order_fsm" in out
    assert "Order (transactional)" in out
    assert "mod_Order_s_draft((\"draft\"))" in out
    assert "mod_Order_s_submitted((\"submitted\"))" in out
    assert "mod_Order_s_draft --> mod_Order_s_submitted" in out
    assert "class mod_Order_s_draft transactional" in out


def test_backbone_transactional_no_transitions_falls_back_to_single_node(gen: BPMNGenerator):
    """Classifier can mark an entity transactional via columns alone (status +
    timestamps). With no parsed transitions, render as a single circle."""
    fsm = _fsm("FeatureFlag", "transactional")
    out = gen.generate_entity_backbone_mermaid([fsm])
    assert "subgraph" not in out
    assert 'mod_FeatureFlag_node(("FeatureFlag<br/><i>transactional</i>"))' in out
    assert "class mod_FeatureFlag_node transactional" in out


def test_backbone_master_renders_cylinder(gen: BPMNGenerator):
    out = gen.generate_entity_backbone_mermaid([_fsm("Customer", "master")])
    assert "mod_Customer_node[(\"Customer<br/><i>master</i>\")]" in out
    assert "class mod_Customer_node master" in out


def test_backbone_key_renders_hexagon(gen: BPMNGenerator):
    out = gen.generate_entity_backbone_mermaid([_fsm("order_type", "key")])
    assert 'mod_order_type_node{{"order_type<br/><i>key</i>"}}' in out
    assert "class mod_order_type_node key" in out


def test_backbone_summary_renders_cylinder(gen: BPMNGenerator):
    out = gen.generate_entity_backbone_mermaid([_fsm("sales_rollup", "summary")])
    assert 'mod_sales_rollup_node[("sales_rollup<br/><i>summary</i>")]' in out
    assert "class mod_sales_rollup_node summary" in out


def test_backbone_staging_renders_parallelogram(gen: BPMNGenerator):
    out = gen.generate_entity_backbone_mermaid([_fsm("stg_orders", "staging")])
    assert 'mod_stg_orders_node[/"stg_orders<br/><i>staging</i>"\\]' in out
    assert "class mod_stg_orders_node staging" in out


def test_backbone_event_renders_asymmetric(gen: BPMNGenerator):
    out = gen.generate_entity_backbone_mermaid([_fsm("audit_log", "event")])
    assert 'mod_audit_log_node>"audit_log<br/><i>event</i>"]' in out
    assert "class mod_audit_log_node event" in out


def test_backbone_junction_renders_circle(gen: BPMNGenerator):
    out = gen.generate_entity_backbone_mermaid([_fsm("order_item", "junction")])
    assert 'mod_order_item_node(("order_item<br/><i>junction</i>"))' in out
    assert "class mod_order_item_node junction" in out


def test_backbone_config_renders_parallelogram_alt(gen: BPMNGenerator):
    out = gen.generate_entity_backbone_mermaid([_fsm("app_settings", "config")])
    assert 'mod_app_settings_node[\\"app_settings<br/><i>config</i>"/]' in out


def test_backbone_unknown_renders_rectangle(gen: BPMNGenerator):
    out = gen.generate_entity_backbone_mermaid([_fsm("thing", "unknown")])
    assert 'mod_thing_node["thing<br/><i>unknown</i>"]' in out


def test_backbone_cross_entity_link_renders_dashed_edge(gen: BPMNGenerator):
    order = _fsm("Order", "transactional", transitions=[_transition("Order", "draft", "submitted")])
    invoice = _fsm("Invoice", "transactional", transitions=[_transition("Invoice", None, "pending")])
    link = CrossEntityTransitionLink(
        from_entity_id="mod.Order",
        from_entity="Order",
        from_field="status",
        from_state="submitted",
        to_entity_id="mod.Invoice",
        to_entity="Invoice",
        to_field="status",
        to_state="pending",
        support=3,
        directional_confidence=1.0,
    )
    out = gen.generate_entity_backbone_mermaid([order, invoice], [link])
    assert "mod_Order_s_submitted -. triggers (3) .-> mod_Invoice_s_pending" in out


def test_backbone_cross_link_to_non_transactional_falls_back_to_entity_node(gen: BPMNGenerator):
    """If the target entity has no FSM subgraph, the edge terminates on its data-store node."""
    order = _fsm("Order", "transactional", transitions=[_transition("Order", "draft", "submitted")])
    customer = _fsm("Customer", "master")
    link = CrossEntityTransitionLink(
        from_entity_id="mod.Order",
        from_entity="Order",
        from_field="status",
        from_state="submitted",
        to_entity_id="mod.Customer",
        to_entity="Customer",
        to_field="status",
        to_state=None,
        support=2,
        directional_confidence=0.9,
    )
    out = gen.generate_entity_backbone_mermaid([order, customer], [link])
    assert "mod_Order_s_submitted -. triggers (2) .-> mod_Customer_node" in out


def test_backbone_output_is_deterministic(gen: BPMNGenerator):
    """Re-ordering inputs must not change output — critical for diff-stability."""
    a = _fsm("A", "master")
    b = _fsm("B", "key")
    c = _fsm("C", "transactional", transitions=[_transition("C", "new", "done")])
    first = gen.generate_entity_backbone_mermaid([c, a, b])
    second = gen.generate_entity_backbone_mermaid([b, c, a])
    assert first == second


def test_backbone_skips_malformed_cross_link(gen: BPMNGenerator):
    """A cross link whose endpoints aren't in the FSM set should be dropped, not crash."""
    order = _fsm("Order", "transactional", transitions=[_transition("Order", "draft", "submitted")])
    stray = CrossEntityTransitionLink(
        from_entity_id="mod.Ghost",
        from_entity="Ghost",
        from_field="status",
        from_state="x",
        to_entity_id="mod.Phantom",
        to_entity="Phantom",
        to_field="status",
        to_state="y",
        support=1,
        directional_confidence=1.0,
    )
    out = gen.generate_entity_backbone_mermaid([order], [stray])
    assert "triggers" not in out


def test_backbone_sanitizes_unsafe_identifiers(gen: BPMNGenerator):
    """entity_id may contain dots, slashes, or other punctuation; must emit safe Mermaid ids."""
    fsm = _fsm("Order", "master", entity_id="src.billing.Order")
    out = gen.generate_entity_backbone_mermaid([fsm])
    assert "src_billing_Order_node" in out
    assert "src.billing.Order_node" not in out


def test_user_task_steps_marked_inferred():
    """HIGH-9: LLM-invented USER_TASK steps render visibly distinct from
    code-grounded steps so they aren't read as documented fact."""
    from ai_discovery.generators.bpmn_generator import _step_display_name
    assert _step_display_name({"name": "Manager Approval", "type": "USER_TASK"}) == "Manager Approval ⚠ inferred"
    # Code-grounded step types are unchanged.
    assert _step_display_name({"name": "Save Order", "type": "DB"}) == "Save Order"
    assert _step_display_name({"name": "Validate", "type": "PROCESS"}) == "Validate"
