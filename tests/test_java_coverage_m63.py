"""Java coverage (roadmap item M6.3): type kinds, receiver vs JPA types, local typing, Spring Data, scope."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.graph.call_graph import resolve_calls
from ai_discovery.parsers.java import JavaParser


def _write(root: Path, files: dict[str, str]) -> list:
    nodes = []
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for f in sorted(root.rglob("*.java")):
        nodes.extend(JavaParser().parse_file(f))
    return nodes


def _edges(nodes) -> dict[tuple[str, str], str]:
    return {(e.caller, e.callee): e.metadata.get("resolved_by") for e in resolve_calls(nodes).edges}


def _misses(nodes) -> dict[tuple[str, str], str]:
    return {(u.caller, u.callee_name): u.reason for u in resolve_calls(nodes).unresolved}


def test_type_kinds(tmp_path) -> None:
    nodes = _write(tmp_path, {"a/T.java": "package a;\n"
                                          "public interface Repo extends Base<X, Integer> { X findBy(String n); }\n"
                                          "enum Color { RED; String label() { return \"r\"; } }\n"
                                          "record Point(int x) { int twice() { return x * 2; } }\n"})
    by = {n.qualified_name: n for n in nodes}
    assert by["a.Repo"].node_type == "interface" and by["a.Repo"].bases == ["Base"]
    assert by["a.Repo.findBy"].framework_hints.get("abstract") is True
    assert by["a.Color"].node_type == "enum" and by["a.Color.label"].node_type == "method"
    assert by["a.Point"].node_type == "class" and "a.Point.twice" in by


def test_receiver_keeps_outer_type_but_jpa_view_keeps_inner(tmp_path) -> None:
    nodes = _write(tmp_path, {"shop/Cart.java": "package shop;\nimport java.util.List;\n"
                                                "public class Cart {\n  private List<Item> items;\n"
                                                "  public int count() { return items.size(); }\n}\n",
                              "shop/Item.java": "package shop;\npublic class Item { public int size() { return 1; } }\n"})
    cart = next(n for n in nodes if n.qualified_name == "shop.Cart")
    assert cart.framework_hints["field_types"]["items"] == "Item"        # entity/table view unchanged
    assert cart.framework_hints["receiver_types"]["items"] == "List"
    assert ("shop.Cart.count", "shop.Item.size") not in _edges(nodes)
    assert _misses(nodes)[("shop.Cart.count", "size")] == "external"


def test_local_and_parameter_typing(tmp_path) -> None:
    nodes = _write(tmp_path, {
        "app/Svc.java": "package app;\npublic class Svc {\n"
                        "  public void run(Owner owner) {\n    owner.rename();\n    var pet = new Pet();\n    pet.feed();\n"
                        "    Owner other = owner;\n    other.rename();\n  }\n}\n",
        "app/Owner.java": "package app;\npublic class Owner { public void rename() {} }\n",
        "app/Pet.java": "package app;\npublic class Pet { public void feed() {} }\n",
        "app/Decoy.java": "package app;\npublic class Decoy { public void rename() {} public void feed() {} }\n"})
    edges = _edges(nodes)
    assert edges[("app.Svc.run", "app.Owner.rename")] == "receiver_typed"
    assert edges[("app.Svc.run", "app.Pet.feed")] == "receiver_typed"
    assert ("app.Svc.run", "app.Decoy.rename") not in edges and ("app.Svc.run", "app.Decoy.feed") not in edges


def test_spring_data_repository_methods(tmp_path) -> None:
    nodes = _write(tmp_path, {
        "pc/OwnerRepository.java": "package pc;\nimport org.springframework.data.jpa.repository.JpaRepository;\n"
                                   "public interface OwnerRepository extends JpaRepository<Owner, Integer> {\n"
                                   "  Page<Owner> findByLastName(String n);\n}\n",
        "pc/OwnerController.java": "package pc;\npublic class OwnerController {\n"
                                   "  private final OwnerRepository owners;\n"
                                   "  OwnerController(OwnerRepository owners) { this.owners = owners; }\n"
                                   "  public void find() { this.owners.findByLastName(\"x\"); }\n"
                                   "  public void store(Owner o) { this.owners.save(o); }\n}\n",
        "pc/Owner.java": "package pc;\npublic class Owner { public void save() {} }\n"})
    edges = _edges(nodes)
    assert edges[("pc.OwnerController.find", "pc.OwnerRepository.findByLastName")] == "receiver_typed"
    assert ("pc.OwnerController.store", "pc.Owner.save") not in edges       # never the same-named entity method
    assert _misses(nodes)[("pc.OwnerController.store", "save")] == "external"   # inherited from JpaRepository


def test_interface_with_implementations_still_dispatches(tmp_path) -> None:
    nodes = _write(tmp_path, {
        "p/Gateway.java": "package p;\npublic interface Gateway { void charge(long c); }\n",
        "p/Stripe.java": "package p;\npublic class Stripe implements Gateway { public void charge(long c) {} }\n",
        "p/Paypal.java": "package p;\npublic class Paypal implements Gateway { public void charge(long c) {} }\n",
        "p/Checkout.java": "package p;\npublic class Checkout {\n  private final Gateway gateway;\n"
                           "  Checkout(Gateway g) { this.gateway = g; }\n  public void pay() { gateway.charge(1); }\n}\n"})
    edges = _edges(nodes)
    assert edges[("p.Checkout.pay", "p.Stripe.charge")] == "dispatch"
    assert edges[("p.Checkout.pay", "p.Paypal.charge")] == "dispatch"
    assert ("p.Checkout.pay", "p.Gateway.charge") not in edges


def test_same_package_type_wins(tmp_path) -> None:
    nodes = _write(tmp_path, {
        "billing/Calc.java": "package billing;\npublic class Calc { public int run() { return 1; } }\n",
        "shipping/Calc.java": "package shipping;\npublic class Calc { public int run() { return 2; } }\n",
        "billing/Invoice.java": "package billing;\npublic class Invoice {\n  private final Calc calc = new Calc();\n"
                                "  public int total() { return calc.run(); }\n}\n"})
    edges = _edges(nodes)
    assert edges[("billing.Invoice.total", "billing.Calc.run")] == "receiver_typed"
    assert ("billing.Invoice.total", "shipping.Calc.run") not in edges
