"""C# coverage (roadmap item M6.2): type kinds, namespaces, partials, generic and ?. calls, scope."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.graph.call_graph import resolve_calls
from ai_discovery.parsers.csharp import CSharpParser


def _parse(root: Path) -> list:
    nodes = []
    for f in sorted(root.rglob("*.cs")):
        nodes.extend(CSharpParser().parse_file(f))
    return nodes


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _edges(nodes) -> dict[tuple[str, str], str]:
    return {(e.caller, e.callee): e.metadata.get("resolved_by") for e in resolve_calls(nodes).edges}


def test_type_kinds_and_namespaces(tmp_path) -> None:
    _write(tmp_path, {"A.cs": "namespace Shop.Billing;\n"
                               "public interface IGateway { void Pay(); }\n"
                               "public record Money(decimal Amount) { public decimal Twice() => Amount * 2; }\n"
                               "public struct Point { public int X; public int Len() { return X; } }\n"
                               "public enum Color { Red }\n",
                      "B.cs": "namespace Outer { namespace Inner { class C { void Go() {} } } }\n"
                               "namespace Single { class D { } }\n"})
    by_qn = {n.qualified_name: n.node_type for n in _parse(tmp_path)}
    assert by_qn["Shop.Billing.IGateway"] == "interface" and "Shop.Billing.IGateway.Pay" not in by_qn
    assert by_qn["Shop.Billing.Money"] == "class" and by_qn["Shop.Billing.Money.Twice"] == "method"
    assert by_qn["Shop.Billing.Point.Len"] == "method" and by_qn["Shop.Billing.Color"] == "enum"
    assert by_qn["Outer.Inner.C.Go"] == "method" and by_qn["Single.D"] == "class"


def test_generic_and_conditional_calls_are_captured(tmp_path) -> None:
    _write(tmp_path, {"S.cs": "namespace App;\npublic class S {\n  private Repo _repo;\n"
                               "  public void Run() { Make<int>(); _repo?.Load(); _repo.Find<string>(1); }\n"
                               "  public void Make<T>() {}\n}\n"
                               "public class Repo { public void Load() {} public void Find<T>(int id) {} }\n"})
    nodes = _parse(tmp_path)
    run = next(n for n in nodes if n.qualified_name == "App.S.Run")
    assert {(c["name"], c["receiver"]) for c in run.call_sites} == {("Make", None), ("Load", "_repo"),
                                                                       ("Find", "_repo")}
    edges = _edges(nodes)
    assert edges[("App.S.Run", "App.S.Make")] == "self_scope"
    assert edges[("App.S.Run", "App.Repo.Load")] == "receiver_typed"      # through `?.`
    assert edges[("App.S.Run", "App.Repo.Find")] == "receiver_typed"      # generic method


def test_same_namespace_beats_a_same_named_type_elsewhere(tmp_path) -> None:
    _write(tmp_path, {
        "Billing/Calculator.cs": "namespace Shop.Billing;\npublic class Calculator { public decimal Compute(decimal x) => x; }\n",
        "Shipping/Calculator.cs": "namespace Shop.Shipping;\npublic class Calculator { public decimal Compute(decimal x) => x; }\n",
        "Billing/Invoice.cs": "namespace Shop.Billing;\npublic class Invoice {\n"
                              "  private readonly Calculator _calc = new Calculator();\n"
                              "  public decimal Total(decimal x) { return _calc.Compute(x); }\n}\n"})
    edges = _edges(_parse(tmp_path))
    assert edges[("Shop.Billing.Invoice.Total", "Shop.Billing.Calculator.Compute")] == "receiver_typed"
    assert ("Shop.Billing.Invoice.Total", "Shop.Shipping.Calculator.Compute") not in edges


def test_foreign_declared_receiver_never_guesses_a_repo_method(tmp_path) -> None:
    _write(tmp_path, {
        "AuditLog.cs": "namespace Shop;\npublic class AuditLog { public void LogInformation(string m) {} }\n",
        "Orders.cs": "using Microsoft.Extensions.Logging;\nnamespace Shop;\npublic class Orders {\n"
                     "  private readonly ILogger<Orders> _logger;\n  private readonly AuditLog _audit = new AuditLog();\n"
                     "  public void Place() { _logger.LogInformation(\"x\"); }\n"
                     "  public void Audit() { _audit.LogInformation(\"y\"); }\n}\n"})
    res = resolve_calls(_parse(tmp_path))
    edges = {(e.caller, e.callee) for e in res.edges}
    assert ("Shop.Orders.Audit", "Shop.AuditLog.LogInformation") in edges
    assert ("Shop.Orders.Place", "Shop.AuditLog.LogInformation") not in edges
    miss = [u for u in res.unresolved if u.caller == "Shop.Orders.Place" and u.callee_name == "LogInformation"]
    assert miss and miss[0].reason == "external"


def test_partial_class_fragments_merge(tmp_path) -> None:
    _write(tmp_path, {
        "Page.cs": "namespace Web;\npublic partial class Page { public void OnClick() { _svc.Save(); } }\n",
        "Page.designer.cs": "namespace Web;\npublic partial class Page { private OrderService _svc; }\n",
        "OrderService.cs": "namespace Web;\npublic class OrderService { public void Save() {} }\n"})
    nodes = _parse(tmp_path)
    assert all(n.framework_hints.get("partial") for n in nodes if n.qualified_name == "Web.Page")
    edges = _edges(nodes)
    assert edges[("Web.Page.OnClick", "Web.OrderService.Save")] == "receiver_typed"   # field from the other fragment


def test_global_using_scoped_to_its_project(tmp_path) -> None:
    _write(tmp_path, {
        "App/App.csproj": "<Project />",
        "App/GlobalUsings.cs": "global using Lib.Data;\n",
        "App/Handler.cs": "namespace App;\npublic class Handler {\n  private Store _store;\n"
                          "  public void Go() { _store.Put(); }\n}\n",
        "Lib/Lib.csproj": "<Project />",
        "Lib/Store.cs": "namespace Lib.Data;\npublic class Store { public void Put() {} }\n",
        "Lib/Other/Store.cs": "namespace Lib.Other;\npublic class Store { public void Put() {} }\n"})
    nodes = _parse(tmp_path)
    from ai_discovery.graph.call_graph import _project_usings

    # GlobalUsings.cs declares no type (no node): the resolver reads it per project
    assert [u["module"] for u in _project_usings(tmp_path / "App")] == ["Lib.Data"]
    edges = _edges(nodes)
    # two repo types named Store: only the namespace brought in by the project's `global using` is local
    assert edges[("App.Handler.Go", "Lib.Data.Store.Put")] == "receiver_typed"
    assert ("App.Handler.Go", "Lib.Other.Store.Put") not in edges


def test_interface_nodes_keep_dispatch_to_implementations(tmp_path) -> None:
    _write(tmp_path, {
        "Pay.cs": "namespace Pay;\npublic interface IGateway { void Charge(); }\n"
                  "public class Stripe : IGateway { public void Charge() {} }\n"
                  "public class Paypal : IGateway { public void Charge() {} }\n"
                  "public class Checkout {\n  private readonly IGateway _gw;\n  public void Pay() { _gw.Charge(); }\n}\n"})
    edges = _edges(_parse(tmp_path))
    assert edges[("Pay.Checkout.Pay", "Pay.Stripe.Charge")] == "dispatch"
    assert edges[("Pay.Checkout.Pay", "Pay.Paypal.Charge")] == "dispatch"
