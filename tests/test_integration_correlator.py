"""Tests for the cross-repo integration correlator (HIGH-8 federation)."""

import json
from pathlib import Path

from ai_discovery.graph.integration_correlator import (
    Endpoint, OutboundCall, RepoInterfaces, build_repo_interfaces, correlate,
    interfaces_from_dict, interfaces_to_dict, normalize_path,
)
from ai_discovery.graph.models import CallEdge, CodeNode


def test_normalize_path_collapses_params_and_host():
    assert normalize_path("https://api.example.com/orders/42") == "/orders/{}"
    assert normalize_path("/orders/{id}") == "/orders/{}"
    assert normalize_path("/orders/:id/items") == "/orders/{}/items"
    assert normalize_path("/orders/") == "/orders"
    assert normalize_path("/a/3f2504e0-4f89-41d3-9a0c-0305e82c3301") == "/a/{}"


def test_correlate_matches_consumer_outbound_to_provider_inbound():
    provider = RepoInterfaces("orders-svc", inbound=[
        Endpoint("GET", "/api/orders/{id}", "getOrder"),
        Endpoint("POST", "/api/orders", "create"),
    ])
    consumer = RepoInterfaces("web-bff", outbound=[
        OutboundCall("GET", "https://orders/api/orders/42", "dashboard"),
        OutboundCall("POST", "/api/orders", "checkout"),
    ])
    edges = correlate([provider, consumer])
    pairs = {(e.provider_repo, e.consumer_repo, e.method, e.path) for e in edges}
    assert ("orders-svc", "web-bff", "GET", "/api/orders/{}") in pairs
    assert ("orders-svc", "web-bff", "POST", "/api/orders") in pairs


def test_correlate_ignores_intra_repo_and_unmatched():
    repo = RepoInterfaces("solo",
        inbound=[Endpoint("GET", "/x", "h")],
        outbound=[OutboundCall("GET", "/x", "c"),          # same repo → ignored
                  OutboundCall("GET", "/nope", "c2")])     # no provider → ignored
    assert correlate([repo]) == []


def test_build_repo_interfaces_from_nodes_and_edges():
    ep = CodeNode(file_path="c.js", language="javascript", node_type="endpoint",
                  name="getOrder", qualified_name="c.getOrder", source_code="",
                  line_start=1, line_end=2, framework_hints={"method": "GET", "route": "/orders/{id}"})
    edge = CallEdge(caller="c.call", callee="external::axios", edge_type="external_call",
                    confidence=0.9, metadata={"operation": "get", "target": "https://x/orders/5"})
    ri = build_repo_interfaces("svc", [ep], [edge])
    assert ri.inbound[0].path == "/orders/{id}"
    assert ri.outbound[0].target == "https://x/orders/5"
    assert ri.outbound[0].method == "GET"


def test_interfaces_roundtrip(tmp_path):
    ri = RepoInterfaces("r", inbound=[Endpoint("GET", "/a", "h")],
                        outbound=[OutboundCall("POST", "/b", "c")])
    d = interfaces_to_dict(ri)
    p = tmp_path / "interfaces.json"; p.write_text(json.dumps(d))
    back = interfaces_from_dict(json.loads(p.read_text()))
    assert back.inbound[0].path == "/a" and back.outbound[0].target == "/b"
