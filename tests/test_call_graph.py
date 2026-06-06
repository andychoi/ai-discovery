"""Tests for call graph builder."""

from ai_discovery.graph.call_graph import build_call_graph
from ai_discovery.graph.models import CodeNode


def test_build_call_graph_resolves_internal_calls():
    nodes = [
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="process",
            qualified_name="svc.PaymentService.process",
            source_code="",
            line_start=1,
            line_end=10,
            calls=["validate", "charge"],
        ),
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="validate",
            qualified_name="svc.PaymentService.validate",
            source_code="",
            line_start=12,
            line_end=20,
        ),
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="charge",
            qualified_name="svc.PaymentService.charge",
            source_code="",
            line_start=22,
            line_end=30,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 2
    callee_names = {e.callee for e in edges}
    assert "svc.PaymentService.validate" in callee_names
    assert "svc.PaymentService.charge" in callee_names


def test_unresolved_calls_recorded():
    nodes = [
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="process",
            qualified_name="svc.process",
            source_code="",
            line_start=1,
            line_end=10,
            calls=["external_api_call"],
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].callee == "external_api_call"
    assert edges[0].confidence < 1.0


def test_empty_calls_no_edges():
    nodes = [
        CodeNode(
            file_path="x.py",
            language="python",
            node_type="method",
            name="noop",
            qualified_name="x.noop",
            source_code="",
            line_start=1,
            line_end=2,
        )
    ]
    assert build_call_graph(nodes) == []


def test_qualified_name_exact_match():
    """Exact qualified_name match should get confidence=1.0."""
    nodes = [
        CodeNode(
            file_path="a.py",
            language="python",
            node_type="method",
            name="caller",
            qualified_name="mod.caller",
            source_code="",
            line_start=1,
            line_end=5,
            calls=["mod.target"],
        ),
        CodeNode(
            file_path="a.py",
            language="python",
            node_type="method",
            name="target",
            qualified_name="mod.target",
            source_code="",
            line_start=7,
            line_end=10,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].confidence == 1.0
    assert edges[0].callee == "mod.target"


def test_ambiguous_short_name():
    """Multiple nodes with the same short name prefer the same-file candidate."""
    nodes = [
        CodeNode(
            file_path="a.py",
            language="python",
            node_type="method",
            name="caller",
            qualified_name="mod.caller",
            source_code="",
            line_start=1,
            line_end=5,
            calls=["run"],
        ),
        CodeNode(
            file_path="a.py",
            language="python",
            node_type="method",
            name="run",
            qualified_name="mod.A.run",
            source_code="",
            line_start=7,
            line_end=10,
        ),
        CodeNode(
            file_path="b.py",
            language="python",
            node_type="method",
            name="run",
            qualified_name="mod.B.run",
            source_code="",
            line_start=1,
            line_end=5,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].callee == "mod.A.run"
    assert edges[0].confidence == 0.9


def test_same_class_match_preferred_over_global_ambiguity():
    nodes = [
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="process",
            qualified_name="svc.PaymentService.process",
            source_code="",
            line_start=1,
            line_end=5,
            calls=["validate"],
        ),
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="validate",
            qualified_name="svc.PaymentService.validate",
            source_code="",
            line_start=7,
            line_end=10,
        ),
        CodeNode(
            file_path="other.py",
            language="python",
            node_type="method",
            name="validate",
            qualified_name="svc.OtherService.validate",
            source_code="",
            line_start=1,
            line_end=3,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].callee == "svc.PaymentService.validate"
    assert edges[0].confidence == 0.95


def test_same_module_match_preferred_when_no_same_class_match():
    nodes = [
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="function",
            name="process",
            qualified_name="svc.process",
            source_code="",
            line_start=1,
            line_end=5,
            calls=["validate"],
        ),
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="function",
            name="validate",
            qualified_name="svc.validate",
            source_code="",
            line_start=7,
            line_end=10,
        ),
        CodeNode(
            file_path="other.py",
            language="python",
            node_type="function",
            name="validate",
            qualified_name="other.validate",
            source_code="",
            line_start=1,
            line_end=3,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].callee == "svc.validate"
    assert edges[0].confidence == 0.9


def test_suffix_match_resolves_partially_qualified_call():
    nodes = [
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="function",
            name="caller",
            qualified_name="svc.caller",
            source_code="",
            line_start=1,
            line_end=5,
            calls=["PaymentService.charge"],
        ),
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="charge",
            qualified_name="billing.PaymentService.charge",
            source_code="",
            line_start=7,
            line_end=10,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].callee == "billing.PaymentService.charge"
    assert edges[0].confidence == 0.85


def test_receiver_type_resolution_pins_di_call_no_fanout():
    """HIGH-3: a call on an injected field resolves to the field's declared type,
    not every same-named method. orderService.process() -> OrderService.process
    only (NOT PaymentService.process)."""
    def cls(qn, name, field_types=None):
        return CodeNode(file_path="x.java", language="java", node_type="class",
                        name=name, qualified_name=qn, source_code="", line_start=1, line_end=9,
                        framework_hints={"field_types": field_types} if field_types else {})

    def method(qn, name):
        return CodeNode(file_path="x.java", language="java", node_type="method",
                        name=name, qualified_name=qn, source_code="", line_start=1, line_end=3)

    nodes = [
        cls("com.x.OrderService", "OrderService"),
        cls("com.x.PaymentService", "PaymentService"),
        method("com.x.OrderService.process", "process"),
        method("com.x.PaymentService.process", "process"),
        cls("com.x.CheckoutController", "CheckoutController",
            field_types={"orderService": "OrderService"}),
        CodeNode(file_path="x.java", language="java", node_type="method",
                 name="view", qualified_name="com.x.CheckoutController.view",
                 source_code="", line_start=1, line_end=3,
                 call_sites=[{"name": "process", "receiver": "orderService"}]),
    ]
    edges = build_call_graph(nodes)
    process_edges = [(e.callee, e.confidence, e.metadata.get("resolved_by"))
                     for e in edges if e.caller == "com.x.CheckoutController.view"]
    assert ("com.x.OrderService.process", 0.93, "receiver_type") in process_edges
    # The fan-out false edge must be gone.
    assert not any(c == "com.x.PaymentService.process" for c, _, _ in process_edges)


def test_receiver_type_falls_through_for_unknown_method():
    """A method not defined on the receiver's type (e.g. framework-inherited)
    is NOT force-resolved — it falls through to unresolved, no false edge."""
    repo = CodeNode(file_path="x.java", language="java", node_type="class",
                    name="OrderRepository", qualified_name="com.x.OrderRepository",
                    source_code="", line_start=1, line_end=2)
    svc = CodeNode(file_path="x.java", language="java", node_type="method",
                   name="getOrders", qualified_name="com.x.OrderService.getOrders",
                   source_code="", line_start=1, line_end=3,
                   framework_hints={},
                   call_sites=[{"name": "findAll", "receiver": "orderRepository"}])
    svc_cls = CodeNode(file_path="x.java", language="java", node_type="class",
                       name="OrderService", qualified_name="com.x.OrderService",
                       source_code="", line_start=1, line_end=9,
                       framework_hints={"field_types": {"orderRepository": "OrderRepository"}})
    edges = build_call_graph([repo, svc, svc_cls])
    findall = [e for e in edges if e.caller == "com.x.OrderService.getOrders"]
    # findAll isn't defined on OrderRepository in source -> stays unresolved.
    assert all(e.metadata.get("resolved_by") == "unresolved" for e in findall)


def test_interface_to_impl_resolution_pins_di_call_no_fanout():
    """P0-2: a call on an interface-typed injected field resolves to the concrete
    implementation precisely, NOT fanning out across unrelated same-named methods.

    OrderService is an interface; OrderServiceImpl (implements it) defines process().
    PaymentService.process() is an unrelated distractor. orderService.process() must
    resolve to OrderServiceImpl.process only — without interface->impl, short-name
    resolution would also reach PaymentService.process."""
    nodes = [
        # interface node — declares no process() body
        CodeNode(file_path="x.java", language="java", node_type="class",
                 name="OrderService", qualified_name="com.x.OrderService",
                 source_code="", line_start=1, line_end=2),
        # concrete impl — bases includes the interface, defines process()
        CodeNode(file_path="x.java", language="java", node_type="class",
                 name="OrderServiceImpl", qualified_name="com.x.OrderServiceImpl",
                 source_code="", line_start=1, line_end=9, bases=["OrderService"]),
        CodeNode(file_path="x.java", language="java", node_type="method",
                 name="process", qualified_name="com.x.OrderServiceImpl.process",
                 source_code="", line_start=1, line_end=3),
        # unrelated distractor with the same method name
        CodeNode(file_path="x.java", language="java", node_type="class",
                 name="PaymentService", qualified_name="com.x.PaymentService",
                 source_code="", line_start=1, line_end=9),
        CodeNode(file_path="x.java", language="java", node_type="method",
                 name="process", qualified_name="com.x.PaymentService.process",
                 source_code="", line_start=1, line_end=3),
        # controller injects the INTERFACE type
        CodeNode(file_path="x.java", language="java", node_type="class",
                 name="CheckoutController", qualified_name="com.x.CheckoutController",
                 source_code="", line_start=1, line_end=9,
                 framework_hints={"field_types": {"orderService": "OrderService"}}),
        CodeNode(file_path="x.java", language="java", node_type="method",
                 name="view", qualified_name="com.x.CheckoutController.view",
                 source_code="", line_start=1, line_end=3,
                 call_sites=[{"name": "process", "receiver": "orderService"}]),
    ]
    edges = build_call_graph(nodes)
    resolved = [(e.callee, e.confidence, e.metadata.get("resolved_by"))
                for e in edges if e.caller == "com.x.CheckoutController.view"]
    assert ("com.x.OrderServiceImpl.process", 0.9, "interface_impl") in resolved
    # The unrelated same-named method must NOT be reached.
    assert not any(c == "com.x.PaymentService.process" for c, _, _ in resolved)


def test_interface_to_impl_ambiguous_does_not_guess():
    """Two implementations define the method -> genuine polymorphism. Do NOT
    force a single interface->impl edge (no false precision); fall through."""
    nodes = [
        CodeNode(file_path="x.java", language="java", node_type="class",
                 name="OrderService", qualified_name="com.x.OrderService",
                 source_code="", line_start=1, line_end=2),
        CodeNode(file_path="x.java", language="java", node_type="class",
                 name="StandardOrderService", qualified_name="com.x.StandardOrderService",
                 source_code="", line_start=1, line_end=9, bases=["OrderService"]),
        CodeNode(file_path="x.java", language="java", node_type="method",
                 name="process", qualified_name="com.x.StandardOrderService.process",
                 source_code="", line_start=1, line_end=3),
        CodeNode(file_path="x.java", language="java", node_type="class",
                 name="FastOrderService", qualified_name="com.x.FastOrderService",
                 source_code="", line_start=1, line_end=9, bases=["OrderService"]),
        CodeNode(file_path="x.java", language="java", node_type="method",
                 name="process", qualified_name="com.x.FastOrderService.process",
                 source_code="", line_start=1, line_end=3),
        CodeNode(file_path="x.java", language="java", node_type="class",
                 name="CheckoutController", qualified_name="com.x.CheckoutController",
                 source_code="", line_start=1, line_end=9,
                 framework_hints={"field_types": {"orderService": "OrderService"}}),
        CodeNode(file_path="x.java", language="java", node_type="method",
                 name="view", qualified_name="com.x.CheckoutController.view",
                 source_code="", line_start=1, line_end=3,
                 call_sites=[{"name": "process", "receiver": "orderService"}]),
    ]
    edges = build_call_graph(nodes)
    view_edges = [e for e in edges if e.caller == "com.x.CheckoutController.view"]
    assert not any(e.metadata.get("resolved_by") == "interface_impl" for e in view_edges)


def test_short_name_fanout_is_capped_to_single_unresolved_edge():
    """P0-3: a call name colliding across many unrelated classes (no locality,
    no type info) must NOT fan out to every candidate at 0.6 — that is the
    quadratic edge blow-up. Above the cap it collapses to ONE unresolved edge."""
    caller = CodeNode(file_path="z/caller.py", language="python", node_type="method",
                      name="run", qualified_name="z.caller.Caller.run",
                      source_code="", line_start=1, line_end=3, calls=["save"])
    # 12 unrelated classes each defining save() — different files & packages,
    # no prefix overlap with the caller.
    candidates = []
    for i in range(12):
        candidates.append(CodeNode(
            file_path=f"a/m{i}.py", language="python", node_type="method",
            name="save", qualified_name=f"a.pkg{i}.C{i}.save",
            source_code="", line_start=1, line_end=2))
    edges = build_call_graph([caller, *candidates])
    save_edges = [e for e in edges if e.caller == "z.caller.Caller.run"]
    assert len(save_edges) == 1
    assert save_edges[0].metadata.get("resolved_by") == "unresolved"
    assert save_edges[0].callee == "save"


def test_short_name_small_collision_still_resolves():
    """Below the cap, a small ambiguous set still fans out (bounded, useful)."""
    caller = CodeNode(file_path="z/caller.py", language="python", node_type="method",
                      name="run", qualified_name="z.caller.Caller.run",
                      source_code="", line_start=1, line_end=3, calls=["save"])
    candidates = [
        CodeNode(file_path=f"a/m{i}.py", language="python", node_type="method",
                 name="save", qualified_name=f"a.pkg{i}.C{i}.save",
                 source_code="", line_start=1, line_end=2)
        for i in range(2)
    ]
    edges = build_call_graph([caller, *candidates])
    save_edges = [e for e in edges if e.caller == "z.caller.Caller.run"]
    # Two candidates -> still resolved (not collapsed), not "unresolved".
    assert len(save_edges) == 2
    assert all(e.metadata.get("resolved_by") == "short_name" for e in save_edges)


# ---------------------------------------------------------------------------
# Stage-4 community narrowing (assessment 07 A-3)
# ---------------------------------------------------------------------------

def _community_fixture_nodes(with_binder: bool = True):
    """Mirror of tests/fixtures/projects/python-community-collision:
    web caller bound into billing by high-confidence edges; shipping isolated;
    `calculate` collides across billing and shipping."""
    caller = CodeNode(
        file_path="app/web/handler.py", language="python", node_type="method",
        name="total", qualified_name="handler.CheckoutView.total",
        source_code="", line_start=10, line_end=15,
        call_sites=(
            [{"name": "build", "receiver": "Invoice"}] if with_binder else []
        ) + [{"name": "calculate", "receiver": "self.strategy"}],
        imports=[{"module": "app.billing.invoice", "name": "Invoice", "alias": None}]
        if with_binder else [],
    )
    invoice_build = CodeNode(
        file_path="app/billing/invoice.py", language="python", node_type="method",
        name="build", qualified_name="invoice.Invoice.build",
        source_code="", line_start=5, line_end=8,
        call_sites=[{"name": "base_rate", "receiver": "PriceCalc"}],
        imports=[{"module": "app.billing.pricing", "name": "PriceCalc", "alias": None}],
    )
    price_base = CodeNode(
        file_path="app/billing/pricing.py", language="python", node_type="method",
        name="base_rate", qualified_name="pricing.PriceCalc.base_rate",
        source_code="", line_start=4, line_end=6,
    )
    price_calc = CodeNode(
        file_path="app/billing/pricing.py", language="python", node_type="method",
        name="calculate", qualified_name="pricing.PriceCalc.calculate",
        source_code="", line_start=8, line_end=10,
    )
    rate_calc = CodeNode(
        file_path="app/shipping/rates.py", language="python", node_type="method",
        name="calculate", qualified_name="rates.RateCalc.calculate",
        source_code="", line_start=4, line_end=6,
    )
    return [caller, invoice_build, price_base, price_calc, rate_calc]


def test_community_narrows_cross_module_collision():
    """An untyped-receiver collision must resolve within the caller's
    call-graph community (built from stage 0-3 edges), not fan out to an
    unconnected module."""
    edges = build_call_graph(_community_fixture_nodes())
    by_callee = {e.callee: e for e in edges if e.caller == "handler.CheckoutView.total"}
    assert "pricing.PriceCalc.calculate" in by_callee
    winner = by_callee["pricing.PriceCalc.calculate"]
    assert winner.confidence == 0.8
    assert winner.metadata["resolved_by"] == "short_name_community"
    # the forbidden cross-community edge must not exist
    assert "rates.RateCalc.calculate" not in by_callee


def test_community_with_multiple_candidates_fans_out_within_community_only():
    nodes = _community_fixture_nodes()
    # second billing candidate, bound into the community via pricing.py
    tax_calc = CodeNode(
        file_path="app/billing/pricing.py", language="python", node_type="method",
        name="calculate", qualified_name="pricing.TaxCalc.calculate",
        source_code="", line_start=20, line_end=22,
    )
    nodes.append(tax_calc)
    edges = build_call_graph(nodes)
    collision = [e for e in edges
                 if e.caller == "handler.CheckoutView.total" and e.callee.endswith(".calculate")]
    callees = {e.callee for e in collision}
    assert callees == {"pricing.PriceCalc.calculate", "pricing.TaxCalc.calculate"}
    assert all(e.confidence == 0.7 for e in collision)
    assert all(e.metadata["resolved_by"] == "short_name_community" for e in collision)


def test_no_community_signal_preserves_existing_fanout():
    """Without any high-confidence binder edges there is no community to
    narrow by — behavior must be byte-identical to today (both candidates,
    0.6, short_name)."""
    edges = build_call_graph(_community_fixture_nodes(with_binder=False))
    collision = [e for e in edges
                 if e.caller == "handler.CheckoutView.total" and e.callee.endswith(".calculate")]
    callees = {e.callee for e in collision}
    assert callees == {"pricing.PriceCalc.calculate", "rates.RateCalc.calculate"}
    assert all(e.confidence == 0.6 for e in collision)
    assert all(e.metadata["resolved_by"] == "short_name" for e in collision)
