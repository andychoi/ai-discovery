"""Chained-call typing (roadmap item M6.16): `a.b().c()` typed from b's declared return type."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.graph.call_graph import resolve_calls
from ai_discovery.parsers.csharp import CSharpParser
from ai_discovery.parsers.java import JavaParser


def _parse(root: Path, files: dict[str, str]) -> list:
    nodes = []
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        nodes.extend((JavaParser() if p.suffix == ".java" else CSharpParser()).parse_file(p))
    return nodes


def _res(nodes):
    res = resolve_calls(nodes)
    return {(e.caller, e.callee): e.metadata.get("resolved_by") for e in res.edges}, res


JAVA = {
    "shop/Order.java": "package shop;\npublic class Order {\n  public long total() { return 1; }\n"
                       "  public Customer buyer() { return null; }\n}\n",
    "shop/Customer.java": "package shop;\npublic class Customer { public String email() { return \"\"; } }\n",
    "shop/Repo.java": "package shop;\nimport java.util.Optional;\npublic class Repo {\n"
                      "  public Order find(long id) { return null; }\n"
                      "  public Optional<Order> findById(long id) { return null; }\n"
                      "  public <T> T any() { return null; }\n}\n",
    "billing/Invoice.java": "package billing;\npublic class Invoice {\n  public long total() { return 0; }\n"
                            "  public Order orElseThrow() { return null; }\n  public String email() { return \"\"; }\n}\n",
    "shop/Checkout.java": "package shop;\npublic class Checkout {\n  private final Repo repo = new Repo();\n"
                          "  public long due(long id) { return repo.find(id).total(); }\n"
                          "  public String mail(long id) { return repo.find(id).buyer().email(); }\n"
                          "  public Order strict(long id) { return repo.findById(id).orElseThrow(); }\n"
                          "  public long self() { return make().total() + this.make().total(); }\n"
                          "  public long generic() { return repo.any().total(); }\n"
                          "  Order make() { return new Order(); }\n}\n",
}


def test_java_chain_typed_from_declared_return(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, JAVA))
    assert edges[("shop.Checkout.due", "shop.Order.total")] == "receiver_typed"
    assert ("shop.Checkout.due", "billing.Invoice.total") not in edges


def test_java_three_hop_chain(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, JAVA))
    assert edges[("shop.Checkout.mail", "shop.Order.buyer")] == "receiver_typed"
    assert edges[("shop.Checkout.mail", "shop.Customer.email")] == "receiver_typed"
    assert ("shop.Checkout.mail", "billing.Invoice.email") not in edges


def test_java_self_chains(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, JAVA))
    assert edges[("shop.Checkout.self", "shop.Order.total")] == "receiver_typed"


def test_library_return_type_is_external_never_guessed(tmp_path) -> None:
    edges, res = _res(_parse(tmp_path, JAVA))
    assert ("shop.Checkout.strict", "billing.Invoice.orElseThrow") not in edges
    assert [u.reason for u in res.unresolved
            if u.caller == "shop.Checkout.strict" and u.callee_name == "orElseThrow"] == ["external"]


def test_type_parameter_return_is_not_typed(tmp_path) -> None:
    edges, res = _res(_parse(tmp_path, JAVA))
    # `T any()` says nothing about the receiver: no typed edge, and not claimed external
    assert not [k for k, v in edges.items() if k[0] == "shop.Checkout.generic" and v == "receiver_typed"
                and k[1].endswith(".total")]
    assert "external" not in [u.reason for u in res.unresolved if u.caller == "shop.Checkout.generic"
                              and u.callee_name == "total"]


def test_csharp_chain_and_conditional_access_receiver(tmp_path) -> None:
    nodes = _parse(tmp_path, {
        "Order.cs": "namespace Shop;\npublic class Order { public long Total() { return 1; } }\n",
        "Invoice.cs": "namespace Billing;\npublic class Invoice { public long Total() { return 0; } }\n",
        "Repo.cs": "namespace Shop;\npublic class Repo { public Order Find(long id) { return null; } }\n",
        "Svc.cs": "namespace Shop;\npublic class Svc {\n  private Repo _repo;\n"
                  "  public long Due(long id) { return 1 + _repo?.Find(id)?.Total() ?? 0; }\n}\n"})
    due = next(n for n in nodes if n.qualified_name == "Shop.Svc.Due")
    assert {(c["name"], c["receiver"]) for c in due.call_sites} == {("Find", "_repo"), ("Total", "_repo.Find(id)")}
    edges, _ = _res(nodes)
    assert edges[("Shop.Svc.Due", "Shop.Repo.Find")] == "receiver_typed"
    assert edges[("Shop.Svc.Due", "Shop.Order.Total")] == "receiver_typed"
    assert ("Shop.Svc.Due", "Billing.Invoice.Total") not in edges


def test_csharp_extension_methods_on_foreign_types(tmp_path) -> None:
    nodes = _parse(tmp_path, {
        "Setup.cs": "using Microsoft.Extensions.DependencyInjection;\nnamespace App;\npublic static class Setup {\n"
                    "  public static IServiceCollection AddOrders(this IServiceCollection services) { return services; }\n"
                    "  public static IServiceCollection AddBilling(this IServiceCollection services) { return services; }\n}\n",
        "Host.cs": "using Microsoft.Extensions.DependencyInjection;\nnamespace App;\npublic class Host {\n"
                   "  private readonly IServiceCollection _services;\n"
                   "  public IServiceCollection Build() { return _services.AddOrders().AddBilling(); }\n}\n"})
    setup = next(n for n in nodes if n.qualified_name == "App.Setup.AddOrders")
    assert setup.framework_hints["extends_type"] == "IServiceCollection"
    edges, _ = _res(nodes)
    assert edges[("App.Host.Build", "App.Setup.AddOrders")] == "receiver_typed"   # foreign field type
    assert edges[("App.Host.Build", "App.Setup.AddBilling")] == "receiver_typed"  # chained: returns IServiceCollection


def test_csharp_fluent_chain_across_lines_and_directives(tmp_path) -> None:
    nodes = _parse(tmp_path, {"P.cs": "namespace App;\npublic static class P {\n  public static B Make() {\n"
                                      "    return B\n      .Create()\n#if DEBUG\n      .Debug()\n#endif\n"
                                      "      .Finish();\n  }\n}\n"
                                      "public class B { public static B Create() { return null; } public B Debug() { return this; }"
                                      " public B Finish() { return this; } }\n"})
    make = next(n for n in nodes if n.qualified_name == "App.P.Make")
    receivers = {c["name"]: c["receiver"] for c in make.call_sites}
    assert receivers["Create"] == "B"
    assert not any(r and ("\n" in r or "#" in r) for r in receivers.values())


def test_receiver_tail_operand_keeps_alias_qualifiers() -> None:
    from ai_discovery.parsers.csharp import _tail_operand

    assert _tail_operand("global::Google.Protobuf.MessageExtensions") == "global::Google.Protobuf.MessageExtensions"
    assert _tail_operand("1 + _repo.Find(id)") == "_repo.Find(id)"
    assert _tail_operand("(await _repo.FindAsync(id))") == "(await _repo.FindAsync(id))"
