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


# ---------------------------------------------------------------------------
# End-to-end tests (Task 8)
# ---------------------------------------------------------------------------

def test_detect_and_build_screens_vue_endtoend(tmp_path):
    router = (tmp_path / "src" / "router"); router.mkdir(parents=True)
    (router / "index.ts").write_text((FIX / "vue-routes.ts").read_text())
    menu_items, screens = md.detect_and_build_screens(tmp_path)
    assert menu_items is not None
    labels = {s.label for s in screens}
    assert "Customers" in labels
    assert "Not Found" not in labels            # catch-all dropped
    cust = next(s for s in screens if s.label == "Customers")
    assert "Root" in cust.menu_path             # wrapper contributes breadcrumb
    assert cust.fe_component == "./pages/CustomerList.vue"


def test_jsonyaml_detection_unchanged(tmp_path):
    (tmp_path / "menu.json").write_text(
        '[{"label":"A","path":"/a","children":[{"label":"B","path":"/a/b"}]}]'
    )
    menu_items, screens = md.detect_and_build_screens(tmp_path)
    assert {s.label for s in screens} == {"B"}   # leaf rule preserved


# ---------------------------------------------------------------------------
# End-to-end: React JSX
# ---------------------------------------------------------------------------

def test_detect_and_build_screens_react_endtoend(tmp_path):
    # React detector looks for src/App.tsx
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text((FIX / "react-jsx.tsx").read_text())
    menu_items, screens = md.detect_and_build_screens(tmp_path)
    assert menu_items is not None
    labels = {s.label for s in screens}
    assert "Customer List" in labels
    assert "Not Found" not in labels            # catch-all dropped
    cust = next(s for s in screens if s.label == "Customer List")
    assert cust.path == "/customers"


# ---------------------------------------------------------------------------
# End-to-end: Angular routing
# ---------------------------------------------------------------------------

def test_detect_and_build_screens_angular_endtoend(tmp_path):
    # Angular detector looks for src/app/app-routing.module.ts
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "app-routing.module.ts").write_text(
        (FIX / "angular-routing.module.ts").read_text()
    )
    menu_items, screens = md.detect_and_build_screens(tmp_path)
    assert menu_items is not None
    labels = {s.label for s in screens}
    assert "Customers" in labels
    cust = next(s for s in screens if s.label == "Customers")
    assert cust.path == "/customers"


# ---------------------------------------------------------------------------
# End-to-end: TypeScript constant (Dashboard + Admin leaf screens)
# ---------------------------------------------------------------------------

def test_detect_and_build_screens_tsconst_endtoend(tmp_path):
    # TS-const detector scans src/**/*.ts
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "menu.ts").write_text((FIX / "ts-const-menu.ts").read_text())
    menu_items, screens = md.detect_and_build_screens(tmp_path)
    assert menu_items is not None
    labels = {s.label for s in screens}
    assert "Dashboard" in labels
    assert "Users" in labels    # Admin leaf screens
    assert "Roles" in labels


# ---------------------------------------------------------------------------
# _resolve_component_hint: prefer route-resolved hint over path-segment glob
# ---------------------------------------------------------------------------

def test_resolve_component_hint_prefers_exact_file(tmp_path):
    """CustomerList.vue at a path glob would MISS (no 'Customers.vue'), but the
    hint './pages/CustomerList.vue' resolves it correctly."""
    from ai_discovery.screen_mapper import ScreenMapper
    from ai_discovery.menu_detector import Screen

    # Create the real file at src/pages/CustomerList.vue
    pages = tmp_path / "src" / "pages"
    pages.mkdir(parents=True)
    (pages / "CustomerList.vue").write_text("<template><div>Customers</div></template>")

    mapper = ScreenMapper(tmp_path)
    screen = Screen(
        screen_id="customers",
        menu_path=["Customers"],
        label="Customers",
        path="/customers",
        fe_component="./pages/CustomerList.vue",
    )

    # _resolve_component_hint must find the file via the hint
    resolved = mapper._resolve_component_hint(screen)
    assert resolved is not None
    assert "CustomerList.vue" in resolved

    # _find_fe_component would NOT find it because the path /customers maps to
    # 'Customers.vue', not 'CustomerList.vue'
    glob_result = mapper._find_fe_component(screen)
    assert glob_result is None or "CustomerList.vue" not in (glob_result or "")

    # map_screen must use the resolved hint
    mapping = mapper.map_screen(screen)
    assert mapping.fe_component is not None
    assert "CustomerList.vue" in mapping.fe_component
