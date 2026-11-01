"""Leveled scenario-flow steps (ScenarioFlow.structured_steps) and their rendering.

Since M3.11 (ADR-0007) a step may be grouped (PHASE), repeated (LOOP) and carry a guard `condition`. It is
never a GATEWAY with branch arms, and never a USER_TASK. Older stores and a model that ignores the prompt can
still produce those types; `normalize_structured_steps` rewrites them before anything renders.
"""

from __future__ import annotations

from ai_discovery.ai.flow_analyzer import ScenarioFlow
from ai_discovery.ai.flow_steps import (
    OTHERWISE, flatten_structured_steps, normalize_flat_steps, normalize_structured_steps,
)
from ai_discovery.generators.flow_diagrams import FlowDiagrams

# What the scan writes now: phases, a guarded step, a loop body.
STRUCTURED = [
    {"name": "Validate & Price", "type": "PHASE", "children": [
        {"name": "Validate order items", "type": "PROCESS", "description": "check inventory",
         "source_ref": "svc/order.py:42"},
        {"name": "Apply premium discount", "type": "PROCESS", "condition": "order.total > 1000"},
    ]},
    {"name": "Persist & Notify", "type": "PHASE", "children": [
        {"name": "Save order", "type": "DB", "description": "INSERT orders"},
        {"name": "line items", "type": "LOOP", "children": [{"name": "Reserve stock", "type": "PROCESS"}]},
    ]},
]

# What a store from before M3.11 can hold: a gateway with arms and an invented manual step.
LEGACY = [
    {"name": "Validate & Price", "type": "PHASE", "children": [
        {"name": "Apply pricing", "type": "GATEWAY", "description": "tier-based pricing", "branches": [
            {"condition": "order.total > 1000", "steps": [{"name": "Apply premium discount", "type": "PROCESS"}]},
            {"condition": "else", "steps": [{"name": "Apply standard pricing", "type": "PROCESS"}]},
        ]},
        {"name": "Manager approval", "type": "USER_TASK"},
    ]},
]


def _flow(structured=None, steps=None, process=None) -> ScenarioFlow:
    return ScenarioFlow(scenario_id="s1", domain="orders", steps=steps or [], structured_steps=structured or [],
                        input=["order_id"], process=process or [], output=["confirmation"], data_flow=[])


def _types(nodes) -> set[str]:
    return {n["type"] for n in nodes} | {t for n in nodes for t in _types(n.get("children", []))}


# ── normalization ────────────────────────────────────────────────────────────

def test_normalize_inlines_gateway_arms_with_their_condition_and_drops_manual_steps() -> None:
    out = normalize_structured_steps(LEGACY)
    children = out[0]["children"]
    assert [(c["name"], c.get("condition")) for c in children] == [
        ("Apply premium discount", "order.total > 1000"), ("Apply standard pricing", OTHERWISE)]
    assert _types(out) <= {"PHASE", "PROCESS"}
    assert "branches" not in str(out)


def test_normalize_is_pure_and_keeps_current_steps() -> None:
    before = repr(LEGACY)
    normalize_structured_steps(LEGACY)
    assert repr(LEGACY) == before
    assert normalize_structured_steps(STRUCTURED) == STRUCTURED
    assert normalize_structured_steps(None) == [] and normalize_structured_steps("x") == []


def test_normalize_unknown_type_becomes_process() -> None:
    assert normalize_structured_steps([{"name": "A", "type": "WHATEVER"}]) == [{"name": "A", "type": "PROCESS"}]


def test_normalize_flat_steps_drops_manual_and_renumbers() -> None:
    flat = normalize_flat_steps([{"step": 1, "name": "Check", "type": "GATEWAY"},
                                 {"step": 2, "name": "Approve", "type": "USER_TASK"},
                                 {"step": 3, "name": "Save", "type": "DB"}])
    assert [(s["step"], s["name"], s["type"]) for s in flat] == [(1, "Check", "PROCESS"), (2, "Save", "DB")]


# ── flatten ──────────────────────────────────────────────────────────────────

def test_flatten_emits_leaf_steps_in_order_with_conditions() -> None:
    flat = flatten_structured_steps(STRUCTURED)
    assert [s["name"] for s in flat] == ["Validate order items", "Apply premium discount", "Save order",
                                         "Reserve stock"]
    assert flat[1]["condition"] == "order.total > 1000"
    assert [s["step"] for s in flat] == [1, 2, 3, 4]
    assert {s["type"] for s in flat} == {"PROCESS", "DB"}       # wrappers are never emitted


def test_flatten_normalizes_legacy_input() -> None:
    assert [s["name"] for s in flatten_structured_steps(LEGACY)] == ["Apply premium discount", "Apply standard pricing"]
    assert flatten_structured_steps([]) == [] and flatten_structured_steps(None) == []


# ── rendering ────────────────────────────────────────────────────────────────

def test_ipo_process_renders_nested_bullets_with_guards() -> None:
    md = FlowDiagrams().generate_ipo_markdown(_flow(structured=STRUCTURED))
    assert "- Validate & Price" in md and "  - Validate order items" in md
    assert "when order.total > 1000: Apply premium discount" in md
    assert "for each: line items" in md
    assert "**Input**" in md and "**Output**" in md


def test_ipo_process_falls_back_to_flat_when_no_hierarchy() -> None:
    md = FlowDiagrams().generate_ipo_markdown(_flow(process=["validate", "persist"]))
    assert "- validate" in md and "- persist" in md


def test_flowchart_guard_labels_edge_and_loop_repeats() -> None:
    chart = FlowDiagrams().generate_mermaid_flowchart(_flow(structured=STRUCTURED))
    assert chart.startswith("flowchart TD")
    assert "|when order.total > 1000|" in chart
    assert "repeat" in chart and "for each: line items" in chart
    assert "{" not in chart                                   # no decision diamonds


def test_flowchart_of_legacy_flow_has_no_gateway_or_manual_step() -> None:
    chart = FlowDiagrams().generate_mermaid_flowchart(_flow(structured=LEGACY))
    assert "{" not in chart and "Manager approval" not in chart and "Apply pricing" not in chart
    assert "|otherwise|" in chart


def test_flowchart_falls_back_to_flat_when_no_hierarchy() -> None:
    chart = FlowDiagrams().generate_mermaid_flowchart(_flow(steps=[{"step": 1, "name": "Do Thing", "type": "PROCESS"}]))
    assert chart.startswith("flowchart TD") and "Do Thing" in chart


def test_sequence_uses_opt_for_guards_and_loop_blocks() -> None:
    seq = FlowDiagrams().generate_mermaid_sequence(_flow(structured=STRUCTURED))
    assert seq.startswith("sequenceDiagram")
    assert "opt when order.total > 1000" in seq and "loop line items" in seq
    assert "alt " not in seq and "else" not in seq


def test_sequence_falls_back_to_flat() -> None:
    seq = FlowDiagrams().generate_mermaid_sequence(_flow(steps=[{"step": 1, "name": "Validate", "type": "PROCESS"}]))
    assert "->>" in seq
