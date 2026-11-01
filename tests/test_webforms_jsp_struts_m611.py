"""WebForms / JSP / Struts extras (roadmap item M6.11)."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.extractors.webconfig import read_webconfig_files
from ai_discovery.graph.call_graph import resolve_calls
from ai_discovery.parsers.csharp import CSharpParser
from ai_discovery.parsers.java import JavaParser
from ai_discovery.parsers.webforms import WebFormsParser


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def _parse(root: Path) -> list:
    nodes = []
    for p in sorted(root.rglob("*")):
        if p.suffix == ".cs":
            nodes += CSharpParser().parse_file(p)
        elif p.suffix == ".java":
            nodes += JavaParser().parse_file(p)
        elif p.suffix in WebFormsParser().extensions:
            nodes += WebFormsParser().parse_file(p)
    return nodes


WEB = {
    "Web/Web.csproj": "<Project />",
    "Web/Shop.master": '<%@ Master Language="C#" CodeBehind="Shop.master.cs" Inherits="Shop.SiteMaster" %>\n',
    "Web/Shop.master.cs": "namespace Shop { public partial class SiteMaster { protected void Page_Load(object s, object e) {} } }\n",
    "Web/Controls/Cart.ascx": '<%@ Control Language="C#" CodeBehind="Cart.ascx.cs" Inherits="Shop.Controls.Cart" %>\n',
    "Web/Controls/Cart.ascx.cs": "namespace Shop.Controls { public partial class Cart { protected void Page_Init(object s, object e) {} } }\n",
    "Web/Orders.aspx": '<%@ Page Language="C#" AutoEventWireup="true" CodeBehind="Orders.aspx.cs" Inherits="Shop.Orders" %>\n'
                       '<%@ Register TagPrefix="uc" TagName="Cart" Src="~/Controls/Cart.ascx" %>\n'
                       '<asp:Button ID="Save" runat="server" OnClick="Save_Click" OnClientClick="return x();" />\n'
                       '<asp:Button ID="Gone" runat="server" OnClick="Missing_Click" />\n'
                       '<asp:SqlDataSource ID="Src" runat="server" SelectCommand="SELECT * FROM Orders" />\n',
    "Web/Orders.aspx.cs": "namespace Shop { public partial class Orders : BasePage {\n"
                          "  protected void Save_Click(object s, object e) {} } }\n",
    "Web/BasePage.cs": "namespace Shop { public class BasePage { protected void Page_Load(object s, object e) {} } }\n",
    "Web/Manual.aspx": '<%@ Page Language="C#" AutoEventWireup="false" Inherits="Shop.Manual" %>\n',
    "Web/Manual.aspx.cs": "namespace Shop { public partial class Manual { protected void Page_Load(object s, object e) {} } }\n",
    "Web/Global.asax": '<%@ Application Codebehind="Global.asax.cs" Inherits="Shop.Global" %>\n',
    "Web/Global.asax.cs": "namespace Shop { public class Global { protected void Application_Start() {} } }\n",
    "Web/Image.ashx": '<%@ WebHandler Language="C#" Class="Shop.ImageHandler" %>\n',
    "Web/ImageHandler.cs": "namespace Shop { public class ImageHandler { public void ProcessRequest(object c) {} } }\n",
}


def test_webforms_bindings(tmp_path) -> None:
    _write(tmp_path, WEB)
    nodes = _parse(tmp_path)
    res = resolve_calls(nodes)
    edges = {(Path(e.caller).name, e.callee): e.metadata.get("binding") for e in res.edges
             if e.metadata.get("resolved_by") == "framework"}
    assert edges[("Orders.aspx", "Shop.Orders.Save_Click")] == "markup_event"
    assert edges[("Orders.aspx", "Shop.BasePage.Page_Load")] == "markup_auto"          # inherited lifecycle method
    assert edges[("Orders.aspx", str(tmp_path / "Web/Controls/Cart.ascx"))] == "markup_register"
    assert edges[("Shop.master", "Shop.SiteMaster.Page_Load")] == "markup_auto"
    assert edges[("Cart.ascx", "Shop.Controls.Cart.Page_Init")] == "markup_auto"
    assert edges[("Global.asax", "Shop.Global.Application_Start")] == "markup_auto"
    assert edges[("Image.ashx", "Shop.ImageHandler.ProcessRequest")] == "markup_auto"
    assert not [k for k in edges if k[0] == "Manual.aspx"]                             # AutoEventWireup="false"
    assert [u.callee_name for u in res.unresolved if u.caller.endswith("Orders.aspx")] == ["Shop.Orders.Missing_Click"]
    page = next(n for n in nodes if n.qualified_name.endswith("Orders.aspx"))
    assert page.framework_hints["sql"] == [{"id": "Src", "op": "select", "sql": "SELECT * FROM Orders"}]
    handler = next(n for n in nodes if n.qualified_name.endswith("Image.ashx"))
    assert handler.node_type == "endpoint" and handler.framework_hints["route"] == "/Image.ashx"


def test_descriptor_endpoints(tmp_path) -> None:
    _write(tmp_path, {
        "src/main/webapp/WEB-INF/web.xml":
            "<web-app><servlet><servlet-name>orders</servlet-name><servlet-class>shop.OrderServlet</servlet-class>"
            "</servlet><servlet-mapping><servlet-name>orders</servlet-name><url-pattern>/orders/*</url-pattern>"
            "</servlet-mapping></web-app>",
        "src/main/webapp/WEB-INF/struts-config.xml":
            '<struts-config><action-mappings><action path="/cart" type="shop.CartAction"/></action-mappings></struts-config>',
        "src/main/resources/struts.xml":
            '<struts><package name="p" namespace="/admin"><action name="users" class="shop.UserAction" method="list"/>'
            "</package></struts>",
        "src/main/java/shop/OrderServlet.java": "package shop;\npublic class OrderServlet {\n"
                                                "  protected void doGet(Object q, Object r) {}\n}\n",
        "src/main/java/shop/CartAction.java": "package shop;\npublic class CartAction { public String execute() { return \"\"; } }\n",
        "src/main/java/shop/UserAction.java": "package shop;\npublic class UserAction { public String list() { return \"\"; } }\n",
        "legacy/Web.config": '<configuration><system.web><httpHandlers><add verb="GET" path="report.axd" '
                            'type="Shop.ReportHandler, Shop"/></httpHandlers></system.web></configuration>',
    })
    eps = read_webconfig_files(tmp_path)
    assert sorted((e.framework_hints["method"], e.framework_hints["route"]) for e in eps) == [
        ("ANY", "/admin/users"), ("ANY", "/cart"), ("ANY", "/orders/*"), ("GET", "/report.axd")]
    edges = {(e.caller.split("#")[-1], e.callee) for e in resolve_calls(_parse(tmp_path) + eps).edges}
    assert ("servlet:/orders/*", "shop.OrderServlet.doGet") in edges
    assert ("struts:/cart", "shop.CartAction.execute") in edges
    assert ("struts:/admin/users", "shop.UserAction.list") in edges
    assert not [x for x in edges if x[1].endswith((".service", ".doPost"))]            # absent conventions dropped
