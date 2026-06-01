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
