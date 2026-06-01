"""Tests for external-system node synthesis (HIGH-8)."""

from ai_discovery.extractors import extract_external_systems
from ai_discovery.extractors.external_system_extractor import _match_external
from ai_discovery.graph.models import CodeNode


def _caller(call_sites):
    return CodeNode(
        file_path="svc.js", language="javascript", node_type="method",
        name="charge", qualified_name="svc.Pay.charge", source_code="",
        line_start=1, line_end=9, call_sites=call_sites,
    )


def test_match_external_by_receiver_root_and_member_access():
    assert _match_external("axios") == "axios"
    assert _match_external("stripe.charges") == "stripe"   # member access → root token
    assert _match_external("this.redis") == "redis"         # strip this.
    assert _match_external("self.kafka") == "kafka"
    assert _match_external("orderService") is None          # not a known client


def test_promotes_known_clients_to_typed_nodes():
    node = _caller([
        {"name": "post", "receiver": "axios"},
        {"name": "create", "receiver": "stripe.charges"},
        {"name": "set", "receiver": "redis"},
    ])
    systems, edges = extract_external_systems([node])
    by_kind = {n.framework_hints["kind"]: n.name for n in systems}
    assert by_kind == {"http_api": "HTTP API", "payment": "Stripe", "cache": "Redis"}
    assert all(e.edge_type == "external_call" and e.confidence == 0.9 for e in edges)
    assert {e.callee for e in edges} == {"external::axios", "external::stripe", "external::redis"}


def test_ignores_local_calls():
    node = _caller([{"name": "save", "receiver": "orderRepository"},
                    {"name": "process", "receiver": "self.orderService"}])
    systems, edges = extract_external_systems([node])
    assert systems == [] and edges == []
