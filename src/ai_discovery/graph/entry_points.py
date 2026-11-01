"""Evidence-based entry-point scoring (roadmap item M6.12).

An entry point is where control enters application code from *outside* it and starts a unit of work: an
HTTP/RPC request, a scheduled or background job, a message or event, a command line, a UI event, or an
application lifecycle hook. Before M6.12 any method whose name merely *contained* `main`, `cmd`, `handler`,
`listener`, … was one (`domain`, `GetClient`, `OnHandlerChanged`, test classes' `main`); now each callable
gets a score from the evidence it carries and only scores at or above :data:`THRESHOLD` count.

**Strong evidence** — any one is sufficient (:data:`STRONG`):

- a route or schedule the parsers already recognised (`endpoint`, `batch_job`, `cli_command`,
  `event_consumer` node types);
- a framework annotation / decorator from a closed table (`@Scheduled`, `@KafkaListener`, `[Function]`,
  `@celery.task`, `@bp.before_app_request`, `@cli.command()`, …);
- a framework base-type contract: the method a runtime calls on a subclass (`BackgroundService.ExecuteAsync`,
  `HttpServlet.doGet`, `HttpApplication.Application_Start`, `CommandLineRunner.run`,
  `IIntegrationEventHandler<T>.Handle`, a public action of an ASP.NET `Controller`, …) or a gRPC service
  method (an `override` taking `ServerCallContext`);
- a .NET event-handler signature `(object sender, XxxEventArgs e)` (WinForms/WebForms/MAUI handlers wired in
  markup or with `+=`);
- a registration in source naming the method or class (`app.MapGet("/x", Handler)`,
  `AddSubscription<TEvent, THandler>()`, `AddHostedService<T>()`);
- a markup / template binding (`OnClick="Save_Click"`, AutoEventWireup `Page_Load`, Vue `@submit="save"`):
  a `framework` edge from a `ui_component`;
- `main`: a `static` `main`/`Main` method, or a module-level function named `main`.

**Weak evidence** adds to the score but three signals are needed to pass on weak evidence alone:
in-degree 0 from production code (:data:`W_IN0`), an *anchored* name pattern (`*Controller.<public>`,
`*Resource.<public>`,
`*Handler.Handle`, `Save_Click`, JS `onSubmit`) (:data:`W_NAME`), public visibility and fan-out
(:data:`W_PUBLIC`, :data:`W_FANOUT`).

**Excluded:** in-process mediator handlers (`IRequestHandler`, `INotificationHandler`, … — application code
reaches them, so they continue a flow), container wiring and binding helpers (`@Bean`, `@InitBinder`,
`@ModelAttribute`, `@PostConstruct`), test, fixture and mock paths, top-level demo/example/sample directories (matched on the path relative to the scanned root, so a
checkout under `/tests/` is not excluded wholesale), test-annotated methods, abstract members, and
spec-only endpoints with no code and no edges (an OpenAPI path or `.proto` rpc declares a contract; the
handler that implements it is the entry point).

Never raises; pure function of (nodes, edges).
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass

from . import origins as O
from .models import CallEdge, CodeNode

THRESHOLD = 0.5
STRONG = 0.8
W_IN0, W_NAME, W_PUBLIC, W_FANOUT = 0.2, 0.2, 0.1, 0.1
FANOUT_MIN = 2

CALLABLE_TYPES = frozenset({"function", "method", "endpoint", "batch_job", "cli_command", "event_consumer"})
_TYPE_KIND = {"endpoint": "http", "batch_job": "scheduled", "cli_command": "cli", "event_consumer": "message"}
TRIGGER = {"http": "HTTP", "scheduled": "SCHEDULED", "background": "SCHEDULED", "message": "EVENT",
           "lifecycle": "EVENT", "cli": "CLI", "ui": "UI"}

# Java annotations / C# attributes (exact simple name) → kind
_ANNOTATION_KIND = {
    "Scheduled": "scheduled", "Schedules": "scheduled",
    "KafkaListener": "message", "KafkaHandler": "message", "RabbitListener": "message", "RabbitHandler": "message",
    "JmsListener": "message", "SqsListener": "message", "StreamListener": "message", "EventListener": "message",
    "TransactionalEventListener": "message", "ServiceActivator": "message", "MessageMapping": "message",
    "Function": "message", "FunctionName": "message",          # Azure Functions (trigger in the binding)
    "ExceptionHandler": "lifecycle",
    "ShellMethod": "cli", "Command": "cli",                     # Spring Shell, picocli sub-commands
    "RelayCommand": "ui",                                       # CommunityToolkit.Mvvm command
}
# Python decorator tail (after the last dot, arguments dropped) → kind
_DECORATOR_KIND = {
    "command": "cli", "group": "cli",
    "task": "message", "shared_task": "message", "periodic_task": "scheduled", "scheduled_job": "scheduled",
    "receiver": "message", "subscriber": "message",
    **{t: "lifecycle" for t in (
        "before_request", "before_app_request", "before_first_request", "after_request", "after_app_request",
        "teardown_request", "teardown_appcontext", "errorhandler", "app_errorhandler", "error_handler",
        "exception_handler", "user_loader", "request_loader", "verify_password", "verify_token", "get_password",
        "context_processor", "app_context_processor", "shell_context_processor", "on_event", "middleware")},
}
_TEST_ANNOTATIONS = frozenset({"Test", "ParameterizedTest", "RepeatedTest", "BeforeEach", "AfterEach", "BeforeAll",
                               "AfterAll", "Before", "After", "BeforeClass", "AfterClass", "Fact", "Theory",
                               "TestMethod", "TestCase", "SetUp", "TearDown", "TestInitialize", "OneTimeSetUp"})
# container wiring and per-request binding helpers: the framework calls them, but they start no unit of work
_WIRING_ANNOTATIONS = frozenset({"Bean", "InitBinder", "ModelAttribute", "PostConstruct", "PreDestroy", "Autowired",
                                 "Inject", "Produces", "Provides", "BeforeMapping", "AfterMapping"})
# base type (simple name, generics dropped) → (method names the runtime calls, kind)
_BASE_CONTRACT = {
    "BackgroundService": ({"ExecuteAsync"}, "background"),
    "IHostedService": ({"StartAsync"}, "background"),
    "HttpServlet": ({"service", "doGet", "doPost", "doPut", "doDelete", "doPatch"}, "http"),
    "GenericServlet": ({"service"}, "http"),
    "IHttpHandler": ({"ProcessRequest"}, "http"),
    "IHttpAsyncHandler": ({"ProcessRequest", "BeginProcessRequest"}, "http"),
    "Action": ({"execute"}, "http"), "ActionSupport": ({"execute"}, "http"),       # Struts 1 / 2
    "DispatchAction": ({"execute"}, "http"), "MappingDispatchAction": ({"execute"}, "http"),
    "CommandLineRunner": ({"run"}, "cli"), "ApplicationRunner": ({"run"}, "cli"),
    "ApplicationListener": ({"onApplicationEvent"}, "message"),
    "MessageListener": ({"onMessage"}, "message"),
    "IConsumer": ({"Consume"}, "message"),                                          # MassTransit
    "IJob": ({"Execute", "execute"}, "scheduled"), "Job": ({"execute"}, "scheduled"),   # Quartz
}
# ASP.NET MVC conventional routing: every public instance method of a Controller subclass is an action
_MVC_BASES = frozenset({"Controller", "ControllerBase", "ApiController", "AbstractController"})
# in-process handlers: application code reaches them through a mediator, so they continue a flow, never start one
_IN_PROCESS_BASES = frozenset({"IRequestHandler", "INotificationHandler", "IPipelineBehavior",
                               "IStreamRequestHandler", "ICommandHandler", "IQueryHandler"})
_BASE_SUFFIX_CONTRACT = (("IntegrationEventHandler", {"Handle", "HandleAsync"}, "message"),)
_HTTP_APPLICATION = re.compile(r"^(Application|Session)_[A-Z]\w*$")
_CODE_BEHIND = (".xaml.cs", ".aspx.cs", ".ascx.cs", ".master.cs", ".Designer.cs", ".razor.cs")
_UI_OWNER = re.compile(r"(View|Page|Form|Window|Control|Dialog|Shell)$")
# .NET event-handler signature `(object sender, XxxEventArgs e)`: the UI/runtime raises the event
_CS_EVENT_SIGNATURE = re.compile(r"\(\s*(?:object|Object|System\.Object)\??\s+\w+\s*,\s*(?:[\w.]+\.)?\w*EventArgs\s+\w+\s*\)")

_CS_MAP_ROUTE = re.compile(
    r"\.Map(?:Get|Post|Put|Delete|Patch|Methods)\s*\(\s*(?:@?\"[^\"]*\"|[^,()]+)\s*,\s*(?:[A-Za-z_]\w*\.)*"
    r"([A-Za-z_]\w*)\s*\)")
_CS_SUBSCRIPTION = re.compile(r"\bAddSubscription\s*<\s*[\w.]+\s*,\s*(?:[\w]+\.)*(\w+)\s*>")
_CS_HOSTED = re.compile(r"\bAddHostedService\s*<\s*(?:[\w]+\.)*(\w+)\s*>")
# (marker, pattern naming a class, kind, the class's methods the runtime calls, evidence)
_CS_CLASS_REGISTRATIONS = (
    ("AddSubscription", _CS_SUBSCRIPTION, "message", ("Handle", "HandleAsync"), "registration:subscription"),
    ("AddHostedService", _CS_HOSTED, "background", ("ExecuteAsync", "StartAsync"), "registration:hosted_service"),
)

_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|__mocks__|spec|specs|testing|fixtures?|mocks?|e2e)(/|$)"
    r"|^(demos?|examples?|samples?)/"          # top level only: `com/example/` is a Java package, not a demo
    r"|(^|/)(test_[^/]+|[^/]+_tests?|conftest)\.py$"
    r"|[^/]*(Tests?|IT|Spec|Specs)\.(java|kt|cs|scala|groovy)$"
    r"|\.(test|spec)\.[jt]sx?$")
_ENTRY_OWNER = re.compile(r"(Controller|Resource)$")
_HANDLER_OWNER = re.compile(r"(Handler|Consumer|Listener|Job|Worker|Command|Processor)$")
_HANDLER_METHOD = frozenset({"handle", "Handle", "HandleAsync", "consume", "Consume", "execute", "Execute",
                             "ExecuteAsync", "run", "Run", "process", "Process", "onMessage", "OnMessage",
                             "invoke", "Invoke", "InvokeAsync"})
_CS_EVENT_NAME = re.compile(r"^[A-Za-z]\w*_(Click|Command|Changed|SelectedIndexChanged|CheckedChanged|"
                            r"TextChanged|ItemCommand|RowCommand|Tick|Elapsed)$")
_JS_EVENT_NAME = re.compile(r"^(on|handle)[A-Z]\w*$")
_UI_BINDINGS = frozenset({"markup_event", "markup_auto", "template_handler"})
_NOT_CALLS = frozenset({"reads_table", "writes_table", "calls_proc", "calls_external"})


@dataclass(frozen=True)
class EntryPoint:
    node: CodeNode
    score: float
    kind: str                                   # http | scheduled | background | message | lifecycle | cli | ui
    evidence: tuple[str, ...] = ()

    @property
    def trigger(self) -> str:
        return TRIGGER.get(self.kind, "CLI")


def _simple(name: str) -> str:
    """`System.Web.HttpApplication` / `IIntegrationEventHandler<OrderStarted>` → `HttpApplication` / …Handler."""
    return re.sub(r"<.*$", "", name or "").strip().rsplit(".", 1)[-1]


def _decorator(text: str) -> tuple[str, str]:
    """`bp.route('/x')` → ('bp', 'route'); `login.user_loader` → ('login', 'user_loader')."""
    head = (text or "").lstrip("@").split("(", 1)[0].strip()
    qual, _, tail = head.rpartition(".")
    return qual, tail


def _relative(path: str, root: str) -> str:
    p = (path or "").replace("\\", "/")
    if root and p.startswith(root):
        p = p[len(root):]
    return p.lstrip("/")


def _root(nodes: list[CodeNode]) -> str:
    """Common directory of all node files: test-path rules apply below it, never to the checkout location."""
    paths = sorted({n.file_path.replace("\\", "/") for n in nodes if n.file_path and "#" not in n.file_path})
    if not paths:
        return ""
    try:
        common = os.path.commonpath(paths) if len(paths) > 1 else paths[0].rpartition("/")[0]
    except ValueError:
        return ""
    return common.rstrip("/") + "/" if common else ""


def is_test_path(path: str, root: str = "") -> bool:
    return bool(_TEST_PATH.search(_relative(path, root)))


def _header(node: CodeNode) -> str:
    src = node.source_code or ""
    cut = src.find("(")
    return src[:cut] if cut >= 0 else src.split("\n", 1)[0]


def is_public(node: CodeNode) -> bool:
    if node.language in ("java", "csharp"):
        return re.search(r"\bpublic\b", _header(node)) is not None
    if node.language == "python":
        return not node.name.startswith("_")
    if node.language in ("javascript", "typescript"):
        return bool((node.framework_hints or {}).get("exported"))
    return False


class _Index:
    def __init__(self, nodes: list[CodeNode], edges: list[CallEdge]):
        self.root = _root(nodes)
        self.by_qn = {n.qualified_name: n for n in nodes}
        self.members: dict[str, list[CodeNode]] = defaultdict(list)
        self.classes_by_name: dict[str, list[CodeNode]] = defaultdict(list)
        self.callables_in_file: dict[tuple[str, str], list[CodeNode]] = defaultdict(list)
        for n in nodes:
            if n.node_type in CALLABLE_TYPES:
                self.callables_in_file[(n.file_path, n.name)].append(n)
            if "." in n.qualified_name:
                self.members[n.qualified_name.rsplit(".", 1)[0]].append(n)
            if n.node_type in ("class", "db_model", "ui_component", "interface"):
                self.classes_by_name[n.name].append(n)
        self.in_prod: dict[str, int] = defaultdict(int)
        self.fan_out: dict[str, int] = defaultdict(int)
        self.ui_bound: set[str] = set()
        for e in edges:
            origin = O.ORIGINS.get((e.metadata or {}).get("resolved_by"))
            if origin is not None and origin.edge_kind in _NOT_CALLS:
                continue
            caller = self.by_qn.get(e.caller)
            if (e.metadata or {}).get("binding") in _UI_BINDINGS and caller is not None \
                    and caller.node_type == "ui_component":
                self.ui_bound.add(e.callee)
                continue
            if caller is None or not is_test_path(caller.file_path, self.root):
                self.in_prod[e.callee] += 1
            if O.followable(e):
                self.fan_out[e.caller] += 1
        self.registered: dict[str, tuple[str, str]] = {}     # method qn → (kind, evidence)
        for n in nodes:
            if n.language == "csharp" and n.source_code:
                self._registrations(n)

    def owner(self, node: CodeNode) -> CodeNode | None:
        return self.by_qn.get(node.qualified_name.rsplit(".", 1)[0]) if "." in node.qualified_name else None

    def _registrations(self, n: CodeNode) -> None:
        src = n.source_code
        if ".Map" in src:   # minimal API: `api.MapGet("/x", Handler)` names a method group of this class/file
            owner_qn = n.qualified_name.rsplit(".", 1)[0]
            for name in _CS_MAP_ROUTE.findall(src):
                hits = [m for m in self.members.get(owner_qn, []) if m.name == name] \
                    or self.callables_in_file.get((n.file_path, name), [])
                for m in hits:
                    self.registered.setdefault(m.qualified_name, ("http", "registration:map_route"))
        for marker, pattern, kind, methods, evidence in _CS_CLASS_REGISTRATIONS:
            if marker not in src:
                continue
            for cls_name in pattern.findall(src):
                for cls in self.classes_by_name.get(cls_name, []):
                    for m in self.members.get(cls.qualified_name, []):
                        if m.name in methods:
                            self.registered.setdefault(m.qualified_name, (kind, evidence))


def _strong(node: CodeNode, idx: _Index) -> tuple[str, str] | None:
    """(kind, evidence) for the first strong signal on *node*, or None."""
    if node.node_type in _TYPE_KIND:
        return _TYPE_KIND[node.node_type], f"node_type:{node.node_type}"
    for ann in node.annotations or []:
        if node.language == "python":
            qual, tail = _decorator(ann)
            if tail in _DECORATOR_KIND and (qual or tail in ("shared_task", "periodic_task", "receiver")):
                return _DECORATOR_KIND[tail], f"decorator:{tail}"
        else:
            simple = _simple(ann.lstrip("@").split("(", 1)[0])
            if simple in _ANNOTATION_KIND:
                return _ANNOTATION_KIND[simple], f"annotation:{simple}"
    if node.qualified_name in idx.registered:
        return idx.registered[node.qualified_name]
    if node.qualified_name in idx.ui_bound:
        return "ui", "binding:markup"
    owner = idx.owner(node)
    bases = [_simple(b) for b in (owner.bases if owner is not None else [])]
    for base in bases:
        contract = _BASE_CONTRACT.get(base)
        if contract and node.name in contract[0]:
            return contract[1], f"base:{base}"
        for suffix, methods, kind in _BASE_SUFFIX_CONTRACT:
            if base.endswith(suffix) and node.name in methods:
                return kind, f"base:*{suffix}"
        if base == "HttpApplication" and _HTTP_APPLICATION.match(node.name):
            return "lifecycle", "base:HttpApplication"
    if node.language == "csharp" and "ServerCallContext" in _signature(node) \
            and re.search(r"\boverride\b", _header(node)):
        return "http", "grpc:service"
    if node.language == "csharp" and set(bases) & _MVC_BASES and re.search(r"\bpublic\b", _header(node)) \
            and not re.search(r"\bstatic\b", _header(node)):
        return "http", "base:Controller"
    if node.language == "csharp" and _CS_EVENT_SIGNATURE.search(_signature(node)):
        ui = node.file_path.endswith(_CODE_BEHIND) or (owner is not None and _UI_OWNER.search(owner.name))
        return ("ui" if ui else "message"), "signature:event_handler"
    if node.name in ("main", "Main"):
        if node.language in ("java", "csharp") and re.search(r"\bstatic\b", _header(node)):
            return "cli", "main"
        if node.node_type == "function" and node.qualified_name.count(".") <= 1:
            return "cli", "main"
    return None


def _signature(node: CodeNode) -> str:
    src = node.source_code or ""
    end = src.find(")")
    return src[:end + 1] if end >= 0 else ""


def _name_pattern(node: CodeNode, idx: _Index, public: bool) -> str | None:
    owner = idx.owner(node)
    owner_name = owner.name if owner is not None else ""
    if owner_name and _ENTRY_OWNER.search(owner_name) and public \
            and not (node.language in ("java", "csharp") and re.search(r"\bstatic\b", _header(node))):
        return "name:controller"
    if owner_name and _HANDLER_OWNER.search(owner_name) and node.name in _HANDLER_METHOD:
        return "name:handler"
    if node.language == "csharp" and _CS_EVENT_NAME.match(node.name):
        return "name:ui_event"
    if node.language in ("javascript", "typescript") and _JS_EVENT_NAME.match(node.name):
        return "name:ui_event"
    return None


def _excluded(node: CodeNode, idx: _Index) -> bool:
    if is_test_path(node.file_path, idx.root):
        return True
    simple = {_simple(a.lstrip("@").split("(", 1)[0]) for a in node.annotations or []}
    if simple & _TEST_ANNOTATIONS or (node.language != "python" and simple & (_WIRING_ANNOTATIONS | {"NonAction"})):
        return True
    if (node.framework_hints or {}).get("abstract"):
        return True
    owner = idx.owner(node)
    if owner is not None and owner.node_type == "interface":
        return True
    if owner is not None and {_simple(b) for b in owner.bases or []} & _IN_PROCESS_BASES:
        return True
    # a spec-declared endpoint (OpenAPI path, `.proto` rpc) with no code and nothing it calls is a contract
    if node.node_type == "endpoint" and not (node.source_code or "").strip() \
            and not idx.fan_out.get(node.qualified_name) and not node.call_sites and not node.calls:
        return True
    return False


def score(node: CodeNode, idx: _Index) -> EntryPoint | None:
    if node.node_type not in CALLABLE_TYPES or _excluded(node, idx):
        return None
    evidence: list[str] = []
    total = 0.0
    strong = _strong(node, idx)
    kind = "cli"
    if strong is not None:
        kind, why = strong
        total += STRONG
        evidence.append(why)
    public = is_public(node)
    named = _name_pattern(node, idx, public)
    if not idx.in_prod.get(node.qualified_name):
        total += W_IN0
        evidence.append("in_degree_0")
    if named:
        total += W_NAME
        evidence.append(named)
        if strong is None:
            kind = {"name:controller": "http", "name:handler": "message", "name:ui_event": "ui"}[named]
    if public:
        total += W_PUBLIC
        evidence.append("public")
    fan = idx.fan_out.get(node.qualified_name, 0)
    if fan >= FANOUT_MIN:
        total += W_FANOUT
        evidence.append(f"fan_out:{fan}")
    total = round(min(total, 1.0), 3)
    return EntryPoint(node, total, kind, tuple(evidence))


def score_entry_points(nodes: list[CodeNode], edges: list[CallEdge] | None = None, *,
                       threshold: float = THRESHOLD) -> list[EntryPoint]:
    """Entry points at or above *threshold*, highest score first (ties by qualified name)."""
    idx = _Index(nodes, edges or [])
    seen: set[str] = set()
    out: list[EntryPoint] = []
    for n in nodes:
        if n.qualified_name in seen:
            continue
        ep = score(n, idx)
        if ep is not None and ep.score >= threshold:
            seen.add(n.qualified_name)
            out.append(ep)
    out.sort(key=lambda e: (-e.score, e.node.qualified_name))
    return out


def node_kind(node: CodeNode) -> str:
    """Kind from *node* alone (no graph): used to label a slice built from an arbitrary node."""
    strong = _strong(node, _Index([node], []))
    return strong[0] if strong else _TYPE_KIND.get(node.node_type, "cli")
