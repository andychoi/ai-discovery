"""Tests for GraphQL + proto contract ingestion (HIGH-7)."""

from ai_discovery.extractors import extract_graphql, extract_proto

_SDL = """
type Customer {
  id: ID!
  name: String
}
type Query {
  customer(id: ID!): Customer
}
type Mutation {
  createOrder(x: ID!): Order
}
"""

_PROTO = """
syntax = "proto3";
message ChargeRequest {
  string order_id = 1;
  int64 amount = 2;
}
service PaymentService {
  rpc Charge(ChargeRequest) returns (ChargeResponse);
  rpc Refund(ChargeRequest) returns (ChargeResponse);
}
"""


def test_graphql_types_become_entities_and_ops_become_endpoints():
    nodes = extract_graphql(_SDL)
    entities = {n.name: n.fields for n in nodes if n.node_type == "db_model"}
    eps = {(n.framework_hints["method"], n.framework_hints["route"])
           for n in nodes if n.node_type == "endpoint"}
    assert entities["Customer"] == ["id", "name"]
    assert ("QUERY", "/customer") in eps
    assert ("MUTATION", "/createOrder") in eps


def test_proto_messages_become_entities_and_rpcs_become_endpoints():
    nodes = extract_proto(_PROTO)
    entities = {n.name: n.fields for n in nodes if n.node_type == "db_model"}
    eps = {(n.framework_hints["method"], n.framework_hints["route"])
           for n in nodes if n.node_type == "endpoint"}
    assert entities["ChargeRequest"] == ["order_id", "amount"]
    assert ("RPC", "/PaymentService/Charge") in eps
    assert ("RPC", "/PaymentService/Refund") in eps


def test_empty_or_irrelevant_yields_nothing():
    assert extract_graphql("scalar DateTime") == []
    assert extract_proto('syntax = "proto3";') == []
