"""Tests for the tour/onboarding generator (assessment 07 A-7).

ONBOARD/{domain}.md: a deterministic BFS learning path (entry points →
callees over high-confidence edges, with Tier-1 purposes) wrapped in one
Tier-2 narrative call per domain. The step table is verified structure; only
the narrative is LLM prose — same facts/prose separation as rollups.
"""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

from ai_discovery.generators.onboarding_generator import (
    TourStep,
    build_tour_steps,
    generate_onboarding_docs,
    render_onboarding_md,
)
from ai_discovery.graph.models import CallEdge, CodeNode, Domain


def _node(qname: str, node_type: str = "function", file_path: str = "src/orders.py") -> CodeNode:
    return CodeNode(
        file_path=file_path, language="python", node_type=node_type,
        name=qname.rsplit(".", 1)[-1], qualified_name=qname,
        source_code="", line_start=10, line_end=20,
    )


def _edge(caller: str, callee: str, confidence: float = 0.95) -> CallEdge:
    return CallEdge(caller=caller, callee=callee, edge_type="direct_call", confidence=confidence)


def _orders_domain():
    """endpoint -> service -> repo chain plus an unconnected helper."""
    api = _node("orders.api.create_order", node_type="endpoint")
    svc = _node("orders.svc.process")
    repo = _node("orders.repo.save")
    helper = _node("orders.util.fmt")
    domain = Domain(name="orders", nodes=[api, svc, repo, helper], entry_points=[api])
    edges = [_edge("orders.api.create_order", "orders.svc.process"),
             _edge("orders.svc.process", "orders.repo.save")]
    summaries = {
        "orders.api.create_order": {"purpose": "POST endpoint creating an order"},
        "orders.svc.process": {"purpose": "Validates and totals the order"},
        "orders.repo.save": {"purpose": "Persists the order row"},
    }
    return domain, edges, summaries


def test_build_tour_steps_bfs_from_entry_point():
    domain, edges, summaries = _orders_domain()
    steps = build_tour_steps(domain, edges, summaries)
    qnames = [s.qualified_name for s in steps]
    # entry point first, then BFS down the call chain
    assert qnames[:3] == ["orders.api.create_order", "orders.svc.process", "orders.repo.save"]
    assert steps[0].order == 1
    assert steps[1].purpose == "Validates and totals the order"
    assert steps[0].file_path == "src/orders.py"


def test_build_tour_steps_caps_and_dedupes():
    api = _node("d.api.go", node_type="endpoint")
    nodes = [api] + [_node(f"d.svc.f{i}") for i in range(30)]
    edges = [_edge("d.api.go", f"d.svc.f{i}") for i in range(30)]
    edges.append(_edge("d.svc.f0", "d.api.go"))  # cycle back — must not loop
    domain = Domain(name="d", nodes=nodes, entry_points=[api])
    steps = build_tour_steps(domain, edges, {}, max_steps=15)
    qnames = [s.qualified_name for s in steps]
    assert len(steps) == 15
    assert len(set(qnames)) == 15  # deduped


def test_build_tour_steps_falls_back_to_fan_in_seed():
    """No entry points: seed with the most-called node (highest fan-in)."""
    hub = _node("d.core.hub")
    a, b = _node("d.a.fa"), _node("d.b.fb")
    domain = Domain(name="d", nodes=[hub, a, b], entry_points=[])
    edges = [_edge("d.a.fa", "d.core.hub"), _edge("d.b.fb", "d.core.hub")]
    steps = build_tour_steps(domain, edges, {})
    assert steps[0].qualified_name == "d.core.hub"


def test_render_onboarding_md_structure():
    steps = [
        TourStep(order=1, qualified_name="orders.api.create_order",
                 file_path="src/orders.py", line_start=10, node_type="endpoint",
                 purpose="POST endpoint", fan_in=0, fan_out=1),
    ]
    md = render_onboarding_md("orders", steps, "Welcome to orders.", "demo", 0.6)
    assert "doc_id: demo-onboard-orders" in md
    assert "discovery_confidence: 0.6" in md
    assert "llm-narrative-unverified" in md  # honest provenance banner
    assert "src/orders.py:10" in md          # deterministic citation
    assert "POST endpoint" in md
    assert "Welcome to orders." in md


def test_generate_onboarding_docs_writes_files_one_call_per_domain(tmp_path):
    domain, edges, summaries = _orders_domain()

    @dataclass
    class _Resp:
        text: str = "Start at the API endpoint, then follow the service layer."
        tokens_in: int = 100
        tokens_out: int = 50
        model: str = "sonnet-test"
        tier: str = "tier2"

    client = MagicMock()
    client.invoke.return_value = _Resp()

    docs = generate_onboarding_docs([domain], edges, summaries, client, tmp_path, "demo")
    assert client.invoke.call_count == 1
    assert client.invoke.call_args[0][0] == "tier2"

    (doc,) = docs
    assert doc["doc_type"] == "onboard"
    assert doc["domain"] == "orders"
    assert doc["verified_row_count"] == 3   # deterministic steps count as facts
    assert doc["confidence"] == 0.6         # UNREVIEWED_WITH_FACTS_CONFIDENCE

    out = tmp_path / "ONBOARD" / "demo-onboard-orders.md"
    assert out.exists()
    content = out.read_text()
    assert "Start at the API endpoint" in content
    assert "orders.svc.process" in content


def test_generate_onboarding_docs_skips_tiny_domains(tmp_path):
    lone = _node("tiny.f")
    domain = Domain(name="tiny", nodes=[lone], entry_points=[])
    client = MagicMock()
    docs = generate_onboarding_docs([domain], [], {}, client, tmp_path, "demo")
    assert docs == []
    assert client.invoke.call_count == 0  # no LLM spend on un-tourable domains


def test_generate_onboarding_docs_survives_llm_failure(tmp_path):
    """The deterministic table must ship even when the narrative call fails —
    facts don't depend on prose."""
    domain, edges, summaries = _orders_domain()
    client = MagicMock()
    client.invoke.side_effect = RuntimeError("provider down")
    docs = generate_onboarding_docs([domain], edges, summaries, client, tmp_path, "demo")
    (doc,) = docs
    content = (tmp_path / "ONBOARD" / "demo-onboard-orders.md").read_text()
    assert "orders.repo.save" in content       # deterministic table present
    assert doc["verified_row_count"] == 3
