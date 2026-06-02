"""Tests for processing-logic level structuring (hierarchical ScenarioFlow steps).

Covers the flatten derivation that keeps legacy renderers working, plus the
leveled IPO / flowchart / sequence rendering that reads `structured_steps`.
"""

from __future__ import annotations

from ai_discovery.ai.flow_analyzer import ScenarioFlow, _flatten_structured_steps
from ai_discovery.generators.bpmn_generator import BPMNGenerator


# A representative hierarchy: two phases, a gateway with two real branch arms,
# and a loop body.
STRUCTURED = [
    {
        "name": "Validate & Price",
        "type": "PHASE",
        "children": [
            {"name": "Validate order items", "type": "PROCESS", "description": "check inventory",
             "source_ref": "svc/order.py:42"},
            {
                "name": "Apply pricing",
                "type": "GATEWAY",
                "description": "tier-based pricing",
                "branches": [
                    {"condition": "order.total > 1000",
                     "steps": [{"name": "Apply premium discount", "type": "PROCESS"}]},
                    {"condition": "else",
                     "steps": [{"name": "Apply standard pricing", "type": "PROCESS"}]},
                ],
            },
        ],
    },
    {
        "name": "Persist & Notify",
        "type": "PHASE",
        "children": [
            {"name": "Save order", "type": "DB", "description": "INSERT orders"},
            {
                "name": "line items",
                "type": "LOOP",
                "children": [{"name": "Reserve stock", "type": "PROCESS"}],
            },
        ],
    },
]


def _flow(structured=None, steps=None, process=None) -> ScenarioFlow:
    return ScenarioFlow(
        scenario_id="s1",
        domain="orders",
        steps=steps or [],
        structured_steps=structured or [],
        input=["order_id"],
        process=process or [],
        output=["confirmation"],
        data_flow=[],
    )


# ---------------------------------------------------------------------------
# _flatten_structured_steps
# ---------------------------------------------------------------------------

def test_flatten_emits_leaf_steps_in_order():
    flat = _flatten_structured_steps(STRUCTURED)
    names = [s["name"] for s in flat]
    assert names == [
        "Validate order items",
        "Apply pricing",            # GATEWAY marker retained
        "Apply premium discount",   # branch arm inlined
        "Apply standard pricing",
        "Save order",
        "Reserve stock",            # loop body inlined
    ]


def test_flatten_omits_structural_wrappers():
    flat = _flatten_structured_steps(STRUCTURED)
    types = {s["type"] for s in flat}
    # PHASE and LOOP are wrappers — never emitted as flat steps.
    assert "PHASE" not in types
    assert "LOOP" not in types
    assert "GATEWAY" in types  # decision marker preserved for legacy renderers


def test_flatten_numbers_steps_sequentially():
    flat = _flatten_structured_steps(STRUCTURED)
    assert [s["step"] for s in flat] == list(range(1, len(flat) + 1))


def test_flatten_handles_flat_input_unchanged():
    # An LLM that ignored nesting and returned a flat list still flattens cleanly.
    flat = _flatten_structured_steps([
        {"name": "A", "type": "PROCESS"},
        {"name": "B", "type": "DB"},
    ])
    assert [s["name"] for s in flat] == ["A", "B"]


def test_flatten_empty_is_empty():
    assert _flatten_structured_steps([]) == []
    assert _flatten_structured_steps(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# IPO markdown — leveled Process section
# ---------------------------------------------------------------------------

def test_ipo_process_renders_nested_bullets():
    md = BPMNGenerator().generate_ipo_markdown(_flow(structured=STRUCTURED))
    assert "#### Process" in md
    # Phase headings, indented children, and branch arms with REAL conditions.
    assert "- Validate & Price" in md
    assert "  - Validate order items" in md
    assert "if order.total > 1000:" in md
    assert "else:" in md
    assert "- Apply premium discount" in md
    assert "for each: line items" in md


def test_ipo_process_falls_back_to_flat_when_no_hierarchy():
    md = BPMNGenerator().generate_ipo_markdown(
        _flow(process=["validate", "persist"])
    )
    assert "#### Process" in md
    assert "- validate" in md
    assert "- persist" in md


def test_ipo_input_output_still_present():
    md = BPMNGenerator().generate_ipo_markdown(_flow(structured=STRUCTURED))
    assert "**Input**" in md
    assert "**Output**" in md


# ---------------------------------------------------------------------------
# Flowchart — real branch condition labels
# ---------------------------------------------------------------------------

def test_flowchart_uses_real_condition_labels():
    chart = BPMNGenerator().generate_mermaid_flowchart(_flow(structured=STRUCTURED))
    assert chart.startswith("flowchart TD")
    # Edge label carries the actual source condition, not a generic yes/no.
    assert "|order.total > 1000|" in chart
    assert "|else|" in chart
    # Gateway diamond present.
    assert "{" in chart and "}" in chart


def test_flowchart_loop_has_repeat_backedge():
    chart = BPMNGenerator().generate_mermaid_flowchart(_flow(structured=STRUCTURED))
    assert "repeat" in chart
    assert "for each: line items" in chart


def test_flowchart_falls_back_to_flat_when_no_hierarchy():
    flat = [{"step": 1, "name": "Do Thing", "type": "PROCESS"}]
    chart = BPMNGenerator().generate_mermaid_flowchart(_flow(steps=flat))
    assert chart.startswith("flowchart TD")
    assert "Do Thing" in chart


# ---------------------------------------------------------------------------
# Sequence — alt/else blocks with conditions, loop block
# ---------------------------------------------------------------------------

def test_sequence_renders_alt_else_with_conditions():
    seq = BPMNGenerator().generate_mermaid_sequence(_flow(structured=STRUCTURED))
    assert seq.startswith("sequenceDiagram")
    assert "alt order.total > 1000" in seq
    assert "else else" in seq or "else" in seq
    assert "loop line items" in seq


def test_sequence_falls_back_to_flat():
    flat = [{"step": 1, "name": "Validate", "type": "PROCESS"}]
    seq = BPMNGenerator().generate_mermaid_sequence(_flow(steps=flat))
    assert "->>" in seq
