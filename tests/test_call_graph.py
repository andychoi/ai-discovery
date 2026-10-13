"""Tests for call graph builder."""

from ai_discovery.graph.call_graph import build_call_graph, resolve_calls
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
    res = resolve_calls(nodes)
    # ADR-0004: an unbindable call is a row, never an edge to a bare name
    assert res.edges == []
    assert [(u.caller, u.callee_name, u.reason) for u in res.unresolved] == [
        ("svc.process", "external_api_call", "not_found")]
    assert res.unresolved[0].file == "svc.py" and res.unresolved[0].line == 1


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
    assert edges[0].confidence == 0.95
    assert edges[0].metadata["resolved_by"] == "import_exact"
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
    assert (edges[0].confidence, edges[0].metadata["resolved_by"]) == (0.95, "same_file")


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
    assert (edges[0].confidence, edges[0].metadata["resolved_by"]) == (0.95, "same_file")


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
        CodeNode(file_path="billing.py", language="python", node_type="class", name="PaymentService",
                 qualified_name="billing.PaymentService", source_code="", line_start=6, line_end=12),
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
    # the type is known only by its unique repo-wide name (no import, other module)
    assert (edges[0].confidence, edges[0].metadata["resolved_by"]) == (0.75, "receiver_global")


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
    assert ("com.x.OrderService.process", 0.93, "receiver_typed") in process_edges
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
    res = resolve_calls([repo, svc, svc_cls])
    # findAll isn't defined on OrderRepository in source -> no edge, an unresolved row.
    assert not [e for e in res.edges if e.caller == "com.x.OrderService.getOrders"]
    assert [(u.callee_name, u.reason) for u in res.unresolved] == [("findAll", "not_found")]


def test_receiver_type_with_framework_base_is_external():
    """`repo.findAll()` where the repository extends a framework type outside the repo."""
    repo = CodeNode(file_path="x.java", language="java", node_type="class", name="OrderRepository",
                    qualified_name="com.x.OrderRepository", source_code="", line_start=1, line_end=2,
                    bases=["JpaRepository"])
    svc_cls = CodeNode(file_path="x.java", language="java", node_type="class", name="OrderService",
                       qualified_name="com.x.OrderService", source_code="", line_start=1, line_end=9,
                       framework_hints={"field_types": {"orderRepository": "OrderRepository"}})
    svc = CodeNode(file_path="x.java", language="java", node_type="method", name="getOrders",
                   qualified_name="com.x.OrderService.getOrders", source_code="", line_start=1, line_end=3,
                   call_sites=[{"name": "findAll", "receiver": "orderRepository"}])
    res = resolve_calls([repo, svc_cls, svc])
    assert res.edges == [] or all(e.caller != svc.qualified_name for e in res.edges)
    assert [(u.callee_name, u.reason) for u in res.unresolved] == [("findAll", "external")]


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
    assert ("com.x.OrderServiceImpl.process", 0.9, "framework") in resolved
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
    # ADR-0004 `dispatch`: one dispatches_to edge per implementation, never a single guess
    assert sorted((e.callee, e.confidence, e.metadata["resolved_by"], e.metadata["edge_kind"])
                  for e in view_edges) == [
        ("com.x.FastOrderService.process", 0.75, "dispatch", "dispatches_to"),
        ("com.x.StandardOrderService.process", 0.75, "dispatch", "dispatches_to"),
    ]


def test_ambiguous_name_gets_no_edge():
    """ADR-0004: a name colliding across unrelated classes is never fanned out
    (old P0-3 behaviour) nor collapsed to a bare-name edge — no edge, one
    `ambiguous` row with the candidate count."""
    caller = CodeNode(file_path="z/caller.py", language="python", node_type="function",
                      name="run", qualified_name="z.caller.run",
                      source_code="", line_start=1, line_end=3, call_sites=[{"name": "persist", "receiver": "repo"}])
    candidates = [
        CodeNode(file_path=f"a/m{i}.py", language="python", node_type="method",
                 name="persist", qualified_name=f"a.pkg{i}.C{i}.persist",
                 source_code="", line_start=1, line_end=2)
        for i in range(3)
    ]
    res = resolve_calls([caller, *candidates])
    assert res.edges == []
    assert [(u.callee_name, u.reason, u.candidate_count) for u in res.unresolved] == [("persist", "ambiguous", 3)]


def test_global_unique_guards():
    """Unique-name binding needs the same language, a callable target and a
    non-builtin name; Python bare calls never reach class methods."""
    caller = CodeNode(file_path="app/a.py", language="python", node_type="function", name="go",
                      qualified_name="a.go", source_code="", line_start=1, line_end=3,
                      call_sites=[{"name": "persist", "receiver": "repo"},      # unique → global_unique
                                  {"name": "append", "receiver": "items"},      # builtin member
                                  {"name": "render_js", "receiver": "view"},    # only in JS
                                  {"name": "helper", "receiver": None}])        # only a class method
    nodes = [
        caller,
        CodeNode(file_path="app/r.py", language="python", node_type="method", name="persist",
                 qualified_name="r.Repo.persist", source_code="", line_start=1, line_end=2),
        CodeNode(file_path="app/l.py", language="python", node_type="method", name="append",
                 qualified_name="l.MyList.append", source_code="", line_start=1, line_end=2),
        CodeNode(file_path="web/v.js", language="javascript", node_type="function", name="render_js",
                 qualified_name="v.render_js", source_code="", line_start=1, line_end=2),
        CodeNode(file_path="app/h.py", language="python", node_type="class", name="H",
                 qualified_name="h.H", source_code="", line_start=1, line_end=5),
        CodeNode(file_path="app/h.py", language="python", node_type="method", name="helper",
                 qualified_name="h.H.helper", source_code="", line_start=2, line_end=3),
    ]
    res = resolve_calls(nodes)
    assert [(e.callee, e.confidence, e.metadata["resolved_by"]) for e in res.edges] == [
        ("r.Repo.persist", 0.5, "global_unique")]
    assert sorted((u.callee_name, u.reason) for u in res.unresolved) == [
        ("append", "builtin"), ("helper", "not_found"), ("render_js", "not_found")]


def test_self_scope_and_inherited():
    base = CodeNode(file_path="b.py", language="python", node_type="class", name="Base",
                    qualified_name="b.Base", source_code="", line_start=1, line_end=9)
    base_m = CodeNode(file_path="b.py", language="python", node_type="method", name="audit",
                      qualified_name="b.Base.audit", source_code="", line_start=2, line_end=3)
    cls = CodeNode(file_path="s.py", language="python", node_type="class", name="Svc",
                   qualified_name="s.Svc", source_code="", line_start=1, line_end=9, bases=["Base"])
    own = CodeNode(file_path="s.py", language="python", node_type="method", name="check",
                   qualified_name="s.Svc.check", source_code="", line_start=2, line_end=3)
    run = CodeNode(file_path="s.py", language="python", node_type="method", name="run",
                   qualified_name="s.Svc.run", source_code="", line_start=4, line_end=8,
                   call_sites=[{"name": "check", "receiver": "self"}, {"name": "audit", "receiver": "self"},
                               {"name": "missing", "receiver": "self"}])
    res = resolve_calls([base, base_m, cls, own, run])
    got = {(e.caller, e.callee, e.metadata["resolved_by"], e.confidence) for e in res.edges}
    assert ("s.Svc.run", "s.Svc.check", "self_scope", 0.95) in got
    assert ("s.Svc.run", "b.Base.audit", "inherited", 0.9) in got
    assert [(u.callee_name, u.reason) for u in res.unresolved] == [("missing", "not_found")]


def test_dispatch_pass_links_base_method_to_overrides():
    nodes = [
        CodeNode(file_path="x.java", language="java", node_type="class", name="Shape",
                 qualified_name="com.x.Shape", source_code="", line_start=1, line_end=2),
        CodeNode(file_path="x.java", language="java", node_type="method", name="area",
                 qualified_name="com.x.Shape.area", source_code="", line_start=1, line_end=2),
        *[n for k in ("Circle", "Square") for n in (
            CodeNode(file_path="x.java", language="java", node_type="class", name=k,
                     qualified_name=f"com.x.{k}", source_code="", line_start=1, line_end=2, bases=["Shape"]),
            CodeNode(file_path="x.java", language="java", node_type="method", name="area",
                     qualified_name=f"com.x.{k}.area", source_code="", line_start=1, line_end=2))],
    ]
    edges = build_call_graph(nodes)
    assert sorted((e.caller, e.callee, e.metadata["edge_kind"], e.confidence) for e in edges) == [
        ("com.x.Shape.area", "com.x.Circle.area", "dispatches_to", 0.75),
        ("com.x.Shape.area", "com.x.Square.area", "dispatches_to", 0.75),
    ]


def test_every_edge_uses_the_closed_origin_vocabulary():
    from ai_discovery.graph.origins import ORIGINS

    for fixture in (_community_fixture_nodes(),):
        for e in build_call_graph(fixture):
            o = ORIGINS[e.metadata["resolved_by"]]
            assert e.confidence == o.confidence
            assert e.metadata["edge_kind"] == o.edge_kind and e.metadata["evidence_kind"] == o.evidence_kind


# ---------------------------------------------------------------------------
# A-3 community narrowing is retired (ADR-0004): a collision is ambiguous even
# when one candidate sits in the caller's call-graph community.
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


def test_import_scoped_calls_resolve_and_collision_stays_unresolved():
    res = resolve_calls(_community_fixture_nodes())
    got = {(e.caller, e.callee, e.metadata["resolved_by"]) for e in res.edges}
    assert ("handler.CheckoutView.total", "invoice.Invoice.build", "import_exact") in got
    assert ("invoice.Invoice.build", "pricing.PriceCalc.base_rate", "import_exact") in got
    assert not any(c.endswith(".calculate") for _, c, _ in got)
    assert [(u.callee_name, u.reason, u.candidate_count) for u in res.unresolved] == [
        ("calculate", "ambiguous", 2)]


def test_relative_js_import_resolves_bare_call():
    """`import authorMapper from './author.mapper'` + `authorMapper(x)` → import_exact."""
    caller = CodeNode(file_path="src/app/article.mapper.ts", language="typescript", node_type="function",
                      name="articleMapper", qualified_name="article.mapper.articleMapper", source_code="",
                      line_start=1, line_end=5, call_sites=[{"name": "authorMapper", "receiver": None}],
                      imports=[{"module": "./author.mapper", "name": "authorMapper", "alias": None}])
    target = CodeNode(file_path="src/app/author.mapper.ts", language="typescript", node_type="function",
                      name="authorMapper", qualified_name="author.mapper.authorMapper", source_code="",
                      line_start=1, line_end=5)
    decoy = CodeNode(file_path="src/other/author.mapper.ts", language="typescript", node_type="function",
                     name="authorMapper", qualified_name="other.author.mapper.authorMapper", source_code="",
                     line_start=1, line_end=5)
    edges = build_call_graph([caller, target, decoy])
    assert [(e.callee, e.metadata["resolved_by"]) for e in edges] == [
        ("author.mapper.authorMapper", "import_exact")]


def test_interface_without_node_resolves_through_implementations():
    """The C# parser emits no interface nodes: `_svc.Run()` on `IService` still
    reaches the one implementation (framework) instead of falling to name-only tiers."""
    nodes = [
        CodeNode(file_path="S.cs", language="csharp", node_type="class", name="Service",
                 qualified_name="App.Service", source_code="", line_start=1, line_end=9, bases=["IService"]),
        CodeNode(file_path="S.cs", language="csharp", node_type="method", name="Run",
                 qualified_name="App.Service.Run", source_code="", line_start=2, line_end=3),
        CodeNode(file_path="C.cs", language="csharp", node_type="class", name="Ctl",
                 qualified_name="App.Ctl", source_code="", line_start=1, line_end=9,
                 framework_hints={"field_types": {"_svc": "IService"}}),
        CodeNode(file_path="C.cs", language="csharp", node_type="method", name="Go",
                 qualified_name="App.Ctl.Go", source_code="", line_start=2, line_end=3,
                 call_sites=[{"name": "Run", "receiver": "_svc"}]),
    ]
    edges = build_call_graph(nodes)
    assert [(e.caller, e.callee, e.metadata["resolved_by"]) for e in edges] == [
        ("App.Ctl.Go", "App.Service.Run", "framework")]
