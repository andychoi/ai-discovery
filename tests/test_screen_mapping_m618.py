"""Screen → backend mapping for server-rendered screens (roadmap item M6.18).

WebForms screens map to their code-behind class and the handlers their markup wires. JSP screens map to the
Spring MVC handlers that render them (view name) and that their forms post to (route + HTTP method). Both small
apps are scanned for real, and the wiki then links each screen to the flows it starts (M4.4.b drilldown).
"""

from __future__ import annotations

import json

import pytest

from ai_discovery.pages.registry import BuildContext, run_builders
from ai_discovery.screen_mapper import jsp_view_names, parse_mvc_handlers
from tests.test_builders_flows_m34 import _git_scan

CONTROLLER = """package com.shop.web;

import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.*;

@Controller
@RequestMapping("/customers")
public class CustomerController {
    private final CustomerService service;

    public CustomerController(CustomerService service) {
        this.service = service;
    }

    @GetMapping
    public String list(Model model) {
        model.addAttribute("customers", service.findAll());
        return "customer/list";
    }

    @PostMapping("/search")
    public String search(@RequestParam("q") String q, Model model) {
        model.addAttribute("customers", service.search(q));
        return "customer/list";
    }

    @RequestMapping(value = "/{id}/delete", method = RequestMethod.POST)
    public String delete(@PathVariable("id") long id) {
        service.delete(id);
        return "redirect:/customers";
    }
}
"""

SPRING_APP = {
    "pom.xml": "<project><artifactId>shop</artifactId></project>\n",
    "src/main/java/com/shop/web/CustomerController.java": CONTROLLER,
    "src/main/java/com/shop/web/CustomerService.java": (
        "package com.shop.web;\n\nimport java.util.List;\n\npublic class CustomerService {\n"
        "    public List<String> findAll() { return List.of(); }\n"
        "    public List<String> search(String q) { return List.of(); }\n"
        "    public void delete(long id) { }\n}\n"),
    "src/main/webapp/WEB-INF/views/customer/list.jsp": (
        "<%@ page contentType=\"text/html\" %>\n<title>Customers</title>\n"
        "<form action=\"${pageContext.request.contextPath}/customers/search\" method=\"post\">\n"
        "  <input name=\"q\"/>\n</form>\n"),
    "src/main/webapp/WEB-INF/views/customer/help.jsp": "<title>Help</title>\n<p>static</p>\n",
}

WEBFORMS_APP = {
    "Shop.csproj": "<Project />\n",
    "Orders/List.aspx": (
        '<%@ Page Language="C#" AutoEventWireup="true" CodeBehind="List.aspx.cs" Inherits="Shop.Orders.List" '
        'Title="Orders" %>\n<asp:Button ID="btnRefresh" runat="server" OnClick="btnRefresh_Click" Text="Refresh" />\n'),
    "Orders/List.aspx.cs": (
        "using System;\n\nnamespace Shop.Orders\n{\n    public partial class List : System.Web.UI.Page\n    {\n"
        "        protected void Page_Load(object sender, EventArgs e) { }\n\n"
        "        protected void btnRefresh_Click(object sender, EventArgs e)\n        {\n"
        "            var svc = new OrderService();\n            svc.Refresh();\n        }\n    }\n}\n"),
    "Orders/OrderService.cs": (
        "namespace Shop.Orders\n{\n    public class OrderService\n    {\n        public void Refresh() { }\n    }\n}\n"),
    "About.aspx": '<%@ Page Language="C#" Inherits="Shop.About" Title="About" %>\n<p>static</p>\n',
}


def _scan(tmp_path_factory, name: str, files: dict[str, str]):
    repo = tmp_path_factory.mktemp(name) / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    conn, sid = _git_scan(repo)
    specs = {sid_: json.loads(spec) for sid_, spec in conn.execute(
        "SELECT screen_id, spec_json FROM screen_specs WHERE scan_id = ?", (sid,))}
    pages = {p.page_id: p for p in run_builders(BuildContext(conn=conn, scan_id=sid, repo=repo))}
    return specs, pages


@pytest.fixture(scope="module")
def spring(tmp_path_factory):
    return _scan(tmp_path_factory, "jsp", SPRING_APP)


@pytest.fixture(scope="module")
def webforms(tmp_path_factory):
    return _scan(tmp_path_factory, "webforms", WEBFORMS_APP)


def _spec(specs, suffix: str) -> dict:
    return next(v for k, v in specs.items() if k.endswith(suffix))


def _routes_to(pages, screen_suffix: str) -> set[str]:
    page = next(p for pid, p in pages.items() if pid.startswith("screen:") and pid.endswith(screen_suffix))
    return {lk.dst for lk in page.links if lk.kind == "routes_to"}


# ── parsing units ─────────────────────────────────────────────────────────────

def test_parse_mvc_handlers_reads_routes_methods_and_views() -> None:
    hs = {h.method: h for h in parse_mvc_handlers(CONTROLLER, "C.java")}
    assert set(hs) == {"list", "search", "delete"}                       # the constructor is not a handler
    assert hs["list"].class_name == "com.shop.web.CustomerController"
    assert hs["list"].paths == ("/customers",) and hs["list"].http == frozenset({"GET"})
    assert hs["search"].paths == ("/customers/search",) and hs["search"].views == frozenset({"customer/list"})
    assert hs["delete"].http == frozenset({"POST"}) and hs["delete"].paths == ("/customers/{id}/delete",)
    assert hs["delete"].views == frozenset()                             # redirect: is not a view
    assert parse_mvc_handlers("public class Plain { public String x() { return \"a\"; } }", "P.java") == []


def test_jsp_view_names_use_the_configured_prefix_else_the_conventional_roots() -> None:
    assert jsp_view_names("src/main/webapp/WEB-INF/views/customer/list.jsp", []) == {"customer/list"}
    assert jsp_view_names("src/main/webapp/WEB-INF/jsp/owners/form.jsp", []) == {"owners/form"}
    assert jsp_view_names("src/main/webapp/WEB-INF/tpl/a/b.jsp", ["/WEB-INF/tpl/"]) == {"a/b"}


# ── JSP end to end ────────────────────────────────────────────────────────────

def test_jsp_screen_maps_to_the_handlers_that_render_it_and_its_form_post(spring) -> None:
    specs, _ = spring
    spec = _spec(specs, "listjsp")
    [ctrl] = spec["be_controllers"]
    assert ctrl["class_name"] == "com.shop.web.CustomerController"
    assert ctrl["methods"] == ["list", "search"]                        # not `delete`: it renders no view
    assert ctrl["confidence"] == 0.9
    assert {"method": "POST", "path": "/customers/search"}.items() <= spec["fe_api_calls"][0].items()
    assert _spec(specs, "helpjsp")["be_controllers"] == []              # no handler renders it


def test_jsp_screen_routes_to_its_flows_in_the_wiki(spring) -> None:
    _, pages = spring
    routed = _routes_to(pages, "listjsp")
    assert routed == {"flow:com.shop.web.CustomerController.list", "flow:com.shop.web.CustomerController.search"}
    assert _routes_to(pages, "helpjsp") == set()


# ── WebForms end to end ───────────────────────────────────────────────────────

def test_webforms_screen_maps_to_its_code_behind_and_handlers(webforms) -> None:
    specs, _ = webforms
    [cb] = _spec(specs, "listaspx")["be_controllers"]
    assert cb["component_type"] == "code_behind" and cb["class_name"] == "Shop.Orders.List"
    assert cb["methods"] == ["Page_Load", "btnRefresh_Click"] and cb["confidence"] == 0.95
    assert cb["file_path"] == "Orders/List.aspx.cs"
    [about] = _spec(specs, "aboutaspx")["be_controllers"]                # directive only: no code-behind file
    assert about["class_name"] == "Shop.About" and about["confidence"] == 0.8 and about["methods"] == []


def test_webforms_screen_routes_to_its_handler_flows(webforms) -> None:
    _, pages = webforms
    routed = _routes_to(pages, "listaspx")
    assert "flow:Shop.Orders.List.btnRefresh_Click" in routed
    assert routed <= {"flow:Shop.Orders.List.btnRefresh_Click", "flow:Shop.Orders.List.Page_Load"}
