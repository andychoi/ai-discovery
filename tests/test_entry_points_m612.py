"""Evidence-based entry-point scoring (roadmap item M6.12)."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.graph.call_graph import ExecutionSliceBuilder, identify_scenarios, make_edge, resolve_calls
from ai_discovery.graph.entry_points import THRESHOLD, is_test_path, score_entry_points
from ai_discovery.graph.models import CodeNode
from ai_discovery.parsers.csharp import CSharpParser
from ai_discovery.parsers.java import JavaParser
from ai_discovery.parsers.python_parser import PythonParser


def _parse(root: Path, files: dict[str, str]) -> list[CodeNode]:
    parsers = {".java": JavaParser, ".cs": CSharpParser, ".py": PythonParser}
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    nodes: list[CodeNode] = []
    for p in sorted(root.rglob("*")):
        if p.suffix in parsers:
            nodes += parsers[p.suffix]().parse_file(p)
    return nodes


def _entries(nodes) -> dict[str, object]:
    edges = resolve_calls(nodes).edges
    return {ep.node.qualified_name: ep for ep in score_entry_points(nodes, edges)}


def _short(entries) -> dict[str, str]:
    return {qn.rsplit(".", 2)[-2] + "." + qn.rsplit(".", 1)[-1]: ep.kind for qn, ep in entries.items()}


def _n(name, qn=None, *, node_type="method", language="python", path="/repo/app/svc.py", source="", **kw):
    return CodeNode(file_path=path, language=language, node_type=node_type, name=name,
                    qualified_name=qn or f"svc.{name}", source_code=source, line_start=1, line_end=2, **kw)


# ── the substring hints are gone ───────────────────────────────────────────────

def test_entry_point_name_substrings_no_longer_trigger():
    # each of these matched a pre-M6.12 substring hint (`main`, `cli`, `cmd`, `handler`, `consumer`)
    names = ["load_domain", "petclinicCacheCustomizer", "GetClient", "send_cmd_ack", "OnHandlerChanged",
             "event_consumer_count", "remaining_items"]
    nodes = [_n(n, f"svc.Svc.{n}") for n in names]
    assert identify_scenarios(nodes, []) == []


# ── Java ───────────────────────────────────────────────────────────────────────

JAVA = {
    "src/main/java/shop/App.java":
        "package shop;\npublic class App {\n  public static void main(String[] args) { new Jobs().nightly(); }\n}\n",
    "src/main/java/shop/Jobs.java":
        "package shop;\nimport org.springframework.scheduling.annotation.Scheduled;\n"
        "public class Jobs {\n  @Scheduled(cron = \"0 0 * * * *\")\n  public void nightly() {}\n"
        "  @KafkaListener(topics = \"orders\")\n  public void onOrder(String m) {}\n"
        "  public void main() {}\n}\n",
    "src/main/java/shop/OrderController.java":
        "package shop;\n@Controller\npublic class OrderController {\n"
        "  @GetMapping(\"/orders\")\n  public String list() { return \"orders\"; }\n"
        "  @InitBinder\n  public void setAllowedFields(Object b) {}\n"
        "  @ModelAttribute(\"types\")\n  public Object types() { return null; }\n}\n",
    "src/main/java/shop/CacheConfig.java":
        "package shop;\npublic class CacheConfig {\n  @Bean\n  public Object cacheCustomizer() { return null; }\n}\n",
    "src/main/java/shop/Legacy.java":
        "package shop;\npublic class Legacy extends HttpServlet {\n"
        "  protected void doGet(Object req, Object res) {}\n  private void render() {}\n}\n",
    "src/test/java/shop/AppTests.java":
        "package shop;\npublic class AppTests {\n  public static void main(String[] args) {}\n"
        "  @Test\n  void works() {}\n}\n",
}


def test_entry_point_java_evidence(tmp_path):
    got = _short(_entries(_parse(tmp_path, JAVA)))
    assert got == {"App.main": "cli", "Jobs.nightly": "scheduled", "Jobs.onOrder": "message",
                   "OrderController.list": "http", "Legacy.doGet": "http"}
    # instance `main`, @InitBinder/@ModelAttribute/@Bean wiring, test-path mains and @Test are not entries


def test_entry_point_checkout_under_a_tests_directory_is_not_excluded(tmp_path):
    root = tmp_path / "tests" / "corpus" / "shop"     # the *checkout* lives under tests/: rules are relative
    got = _short(_entries(_parse(root, JAVA)))
    assert "App.main" in got and "AppTests.main" not in got


# ── C# ─────────────────────────────────────────────────────────────────────────

CS = {
    "Ordering/Ordering.csproj": "<Project />",
    "Ordering/OrdersApi.cs":
        "namespace Ordering;\npublic static class OrdersApi {\n"
        "  public static void MapOrdersApi(this IEndpointRouteBuilder app) {\n"
        "    var api = app.MapGroup(\"api/orders\");\n"
        "    api.MapGet(\"/\", GetOrdersAsync);\n    api.MapPost(\"/cancel\", CancelOrderAsync);\n  }\n"
        "  public static Task GetOrdersAsync() { return null; }\n"
        "  public static Task CancelOrderAsync() { return null; }\n"
        "  public static Task Unmapped() { return null; }\n}\n",
    "Ordering/Startup.cs":
        "namespace Ordering;\npublic static class Startup {\n"
        "  public static void Add(IHostApplicationBuilder b) {\n"
        "    b.Services.AddHostedService<GracePeriodService>();\n"
        "    b.AddEventBus().AddSubscription<OrderPaid, OrderPaidHandler>();\n  }\n}\n",
    "Ordering/Handlers.cs":
        "namespace Ordering;\n"
        "public class OrderPaidHandler : IIntegrationEventHandler<OrderPaid> {\n"
        "  public Task Handle(OrderPaid e) { return null; }\n}\n"
        "public class StockHandler : IIntegrationEventHandler<StockLow> {\n"
        "  public Task Handle(StockLow e) { return null; }\n}\n"
        "public class CreateOrderCommandHandler : IRequestHandler<CreateOrder, bool> {\n"
        "  public Task<bool> Handle(CreateOrder c, CancellationToken t) { return null; }\n}\n"
        "public class GracePeriodService : BackgroundService {\n"
        "  protected override Task ExecuteAsync(CancellationToken t) { return null; }\n}\n",
    "Ordering/BasketService.cs":
        "namespace Ordering;\npublic class BasketService : Basket.BasketBase {\n"
        "  public override Task<Reply> GetBasket(Request r, ServerCallContext context) { return null; }\n}\n"
        "public static class CallContextExtensions {\n"
        "  public static string GetUser(this ServerCallContext context) { return null; }\n}\n",
    "Ordering/HomeController.cs":
        "namespace Ordering;\npublic class HomeController : Controller {\n"
        "  public IActionResult Index() { return null; }\n"
        "  [NonAction]\n  public void Helper() {}\n  private void Hidden() {}\n"
        "  public static void Util() {}\n}\n",
    "Ordering/MapView.xaml.cs":
        "namespace Ordering;\npublic partial class MapView : ContentPage {\n"
        "  private async void Pin_MarkerClicked(object sender, PinClickedEventArgs e) {}\n"
        "  private void Refresh(object sender) {}\n}\n",
}


def test_entry_point_csharp_evidence(tmp_path):
    entries = _entries(_parse(tmp_path, CS))
    got = _short(entries)
    assert got == {
        "OrdersApi.GetOrdersAsync": "http", "OrdersApi.CancelOrderAsync": "http",      # MapGet/MapPost
        "OrderPaidHandler.Handle": "message",                                          # AddSubscription
        "StockHandler.Handle": "message",                                              # *IntegrationEventHandler
        "GracePeriodService.ExecuteAsync": "background",                               # AddHostedService/base
        "BasketService.GetBasket": "http",                                             # gRPC override
        "HomeController.Index": "http",                                                # MVC conventional
        "MapView.Pin_MarkerClicked": "ui",                                             # (sender, EventArgs)
    }
    # MediatR handlers continue a flow, extension methods taking ServerCallContext are not gRPC services,
    # the `Map*` registration method itself is startup wiring
    assert "registration:map_route" in entries["Ordering.OrdersApi.GetOrdersAsync"].evidence


# ── Python ─────────────────────────────────────────────────────────────────────

PY = {
    "app/cli.py": "import click\n\n@bp.cli.group()\ndef translate():\n    pass\n\n"
                  "@translate.command()\ndef compile():\n    pass\n",
    "app/hooks.py": "@bp.before_app_request\ndef before_request():\n    pass\n\n"
                    "@login.user_loader\ndef load_user(id):\n    return id\n\n"
                    "@login_required\ndef helper():\n    pass\n",
    "app/routes.py": "@bp.route('/explore')\ndef explore():\n    return helper2()\n\ndef helper2():\n    return 1\n",
    "manage.py": "def main():\n    pass\n",
    "tests/test_app.py": "def main():\n    pass\n",
}


def test_entry_point_python_evidence(tmp_path):
    got = {qn: ep.kind for qn, ep in _entries(_parse(tmp_path, PY)).items()}
    assert got == {"cli.translate": "cli", "cli.compile": "cli", "hooks.before_request": "lifecycle",
                   "hooks.load_user": "lifecycle", "routes.explore": "http", "manage.main": "cli"}


# ── graph signals ──────────────────────────────────────────────────────────────

def test_entry_point_weak_evidence_needs_three_signals():
    ctrl = _n("OrderController", "shop.OrderController", node_type="class", language="java",
              path="/r/src/OrderController.java")
    action = _n("list", "shop.OrderController.list", language="java", path="/r/src/OrderController.java",
                source="public String list() { return x(); }")
    internal = _n("audit", "shop.OrderController.audit", language="java", path="/r/src/OrderController.java",
                  source="public void audit() {}")
    caller = _n("run", "shop.Job.run", language="java", path="/r/src/Job.java", source="void run() {}")
    test_caller = _n("t", "shop.OrderControllerTests.t", language="java", path="/r/src/test/OrderControllerTests.java")
    edges = [make_edge(caller.qualified_name, internal.qualified_name, "receiver_typed"),
             make_edge(test_caller.qualified_name, action.qualified_name, "receiver_typed")]
    eps = {e.node.qualified_name: e for e in score_entry_points([ctrl, action, internal, caller, test_caller], edges)}
    # public + `*Controller` + in-degree 0 from production code (a test caller does not count)
    assert set(eps) == {"shop.OrderController.list"}
    assert eps["shop.OrderController.list"].score == THRESHOLD
    assert eps["shop.OrderController.list"].evidence == ("in_degree_0", "name:controller", "public")


def test_entry_point_markup_binding_makes_a_ui_entry():
    page = _n("Orders.aspx", "Orders.aspx", node_type="ui_component", language="csharp", path="/r/Orders.aspx")
    handler = _n("Save_Click", "Shop.Orders.Save_Click", language="csharp", path="/r/Orders.aspx.cs",
                 source="protected void Save_Click(object s, EventArgs e) {}")
    edge = make_edge(page.qualified_name, handler.qualified_name, "framework", binding="markup_event")
    (ep,) = score_entry_points([page, handler], [edge])
    assert (ep.node, ep.kind, ep.evidence[0]) == (handler, "ui", "binding:markup")
    assert ExecutionSliceBuilder([page, handler], [edge]).build_all_scenarios()[0].trigger_type == "UI"


def test_entry_point_spec_only_endpoints_are_contracts_not_entries():
    spec = _n("GET /items", "Catalog.API.json#GET /items", node_type="endpoint", language="json",
              path="/r/Catalog.API.json")
    descriptor = _n("/orders", "web.xml#servlet:/orders", node_type="endpoint", language="java", path="/r/web.xml",
                    call_sites=[{"name": "shop.Orders.doGet", "receiver": None, "binding": "xml_mapping"}])
    assert [e.node for e in score_entry_points([spec, descriptor], [])] == [descriptor]


def test_entry_point_ranking_and_slice_trigger_types(tmp_path):
    nodes = _parse(tmp_path, JAVA)
    edges = resolve_calls(nodes).edges
    eps = score_entry_points(nodes, edges)
    assert [e.score for e in eps] == sorted((e.score for e in eps), reverse=True)
    scenarios = {s.entry_point.rsplit(".", 1)[-1]: s.trigger_type
                 for s in ExecutionSliceBuilder(nodes, edges).build_all_scenarios()}
    assert scenarios == {"main": "CLI", "nightly": "SCHEDULED", "onOrder": "EVENT", "list": "HTTP", "doGet": "HTTP"}


def test_entry_point_test_path_rules_are_relative():
    assert not is_test_path("/home/u/tests/corpus/app/src/Order.java", "/home/u/tests/corpus/app/")
    assert is_test_path("/home/u/tests/corpus/app/src/test/OrderTest.java", "/home/u/tests/corpus/app/")
    is_test = [p for p in ["src/test/java/a/AppTests.java", "tests/test_x.py", "web/src/app.spec.ts",
                           "Ordering.UnitTests/OrderTest.cs", "examples/demo.py", "pkg/conftest.py"]
               if is_test_path(p)]
    assert len(is_test) == 6
    assert not any(is_test_path(p) for p in ["src/main/java/a/Testimonial.java", "app/contest.py",
                                             "src/latest/run.py",
                                             "src/main/java/com/example/web/OrderController.java"])
