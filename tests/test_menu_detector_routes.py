from __future__ import annotations
from pathlib import Path
from ai_discovery import menu_detector as md
from ai_discovery.route_parser import RouteNode

FIX = Path(__file__).parent / "fixtures" / "routes"


def test_routenode_to_menuitem_sets_route_metadata():
    rn = RouteNode(path="/c", component="CustomerList", component_source="./C.vue", title="Customers")
    item = md._routenode_to_menuitem(rn, is_route_format=True)
    assert item.label == "Customers"
    assert item.path == "/c"
    assert item.metadata["is_screen"] is True
    assert item.metadata["component_source"] == "./C.vue"


def test_routenode_to_menuitem_wrapper_is_not_screen():
    rn = RouteNode(path="/", component=None, children=[RouteNode(path="x", component="X")])
    item = md._routenode_to_menuitem(rn, is_route_format=True)
    assert item.metadata["is_screen"] is False


def test_ts_const_detector_parses_fixture(tmp_path):
    src = (tmp_path / "src"); src.mkdir()
    (src / "menu.ts").write_text((FIX / "ts-const-menu.ts").read_text())
    items = md.TypeScriptConstantDetector().detect(tmp_path)
    assert items is not None
    assert {i.label for i in items} == {"Dashboard", "Admin"}


def test_vue_detector_parses_fixture(tmp_path):
    router = (tmp_path / "src" / "router"); router.mkdir(parents=True)
    (router / "index.ts").write_text((FIX / "vue-routes.ts").read_text())
    items = md.FrameworkRoutingDetector().detect(tmp_path)
    assert items is not None
    assert any(i.path == "/" for i in items)


def test_humanize_cases():
    assert md._humanize("CustomerListPage") == "Customer List"
    assert md._humanize("OrderScreen") == "Order"
    assert md._humanize("customer-detail") == "Customer Detail"
    assert md._humanize("Page") == "Page"  # bare suffix preserved


# ---------------------------------------------------------------------------
# build_screen_map tests (Task 7)
# ---------------------------------------------------------------------------

def _mi(path, label, children=None, is_screen=None, **meta):
    m = {"component_source": None, "redirect_to": None, "is_catch_all": False, **meta}
    if is_screen is not None:
        m["is_screen"] = is_screen
    return md.MenuItem(id=label.lower(), label=label, path=path, children=children or [], metadata=m)


def test_build_screen_map_route_rules():
    tree = [_mi("/", "Root", is_screen=False, children=[
        _mi("customers", "Customers", is_screen=True, component_source="./C.vue"),
        _mi("orders", "Orders", is_screen=True),
    ])]
    screens = md.build_screen_map(tree, Path("."))
    labels = {s.label for s in screens}
    assert labels == {"Customers", "Orders"}
    cust = next(s for s in screens if s.label == "Customers")
    assert cust.path == "/customers"
    assert cust.menu_path == ["Root", "Customers"]
    assert cust.fe_component == "./C.vue"


def test_build_screen_map_leaf_rule_backward_compat():
    tree = [md.MenuItem(id="a", label="A", path="/a", children=[
        md.MenuItem(id="b", label="B", path="/a/b"),
    ])]
    screens = md.build_screen_map(tree, Path("."))
    assert {s.label for s in screens} == {"B"}


def test_build_screen_map_redirect_alias():
    tree = [
        _mi("/customers", "Customers", is_screen=True),
        _mi("/old", "Old", is_screen=False, redirect_to="/customers"),
    ]
    screens = md.build_screen_map(tree, Path("."))
    assert {s.label for s in screens} == {"Customers"}
    cust = screens[0]
    assert "/old" in cust.metadata.get("redirect_aliases", [])


def test_build_screen_map_relative_redirect_alias():
    tree = [_mi("/app", "App", is_screen=False, children=[
        _mi("home", "Home", is_screen=True),
        _mi("start", "Start", is_screen=False, redirect_to="home"),  # relative target
    ])]
    screens = md.build_screen_map(tree, Path("."))
    home = next(s for s in screens if s.label == "Home")
    assert "/app/start" in home.metadata.get("redirect_aliases", [])


def test_build_screen_map_unique_ids_no_collision():
    # two screens whose ids would collide -> second gets a -2 suffix, not "-2"
    tree = [
        md.MenuItem(id="dup", label="A", path="/a", metadata={"is_screen": True}),
        md.MenuItem(id="dup", label="B", path="/b", metadata={"is_screen": True}),
    ]
    screens = md.build_screen_map(tree, Path("."))
    ids = {s.screen_id for s in screens}
    assert ids == {"dup", "dup-2"}
