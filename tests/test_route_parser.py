from __future__ import annotations
from pathlib import Path
import pytest
from ai_discovery import route_parser as rp
from ai_discovery.route_parser import RouteNode

pytestmark = pytest.mark.skipif(not rp._TS_AVAILABLE, reason="tree-sitter grammars not installed")


def _find_node(node, node_type):
    if node.type == node_type:
        return node
    for c in node.children:
        r = _find_node(c, node_type)
        if r:
            return r
    return None


def _parse_src(src: bytes, ext: str = ".ts"):
    """Parse a raw source string with the right grammar and return the root node."""
    lang = rp._grammar_for_ext(Path(f"x{ext}"))
    from tree_sitter import Parser
    return Parser(lang).parse(src).root_node


def test_routenode_defaults():
    rn = RouteNode()
    assert rn.path is None and rn.children == [] and rn.roles == [] and rn.is_catch_all is False


def test_grammar_for_ext_selects_grammar():
    assert rp._grammar_for_ext(Path("a.ts")) is not None
    assert rp._grammar_for_ext(Path("a.tsx")) is not None
    assert rp._grammar_for_ext(Path("a.jsx")) is not None
    assert rp._grammar_for_ext(Path("a.js")) is not None
    assert rp._grammar_for_ext(Path("a.txt")) is None


def test_str_value_strips_quotes():
    root = _parse_src(b"const x = '/users'")
    s = _find_node(root, "string")
    assert rp._str_value(s) == "/users"


def test_collect_imports_maps_identifier_to_specifier():
    root = _parse_src(b"import UserList from './pages/UserList.vue'\nimport {A,B} from './ab'")
    imports = rp._collect_imports(root)
    assert imports["UserList"] == "./pages/UserList.vue"
    assert imports["A"] == "./ab"
    assert imports["B"] == "./ab"


def test_collect_imports_uses_local_binding_for_alias():
    root = _parse_src(b"import {A as Alias} from './m'\nimport Def from './d'\nimport * as NS from './n'")
    imports = rp._collect_imports(root)
    assert imports["Alias"] == "./m"     # alias is the local binding
    assert "A" not in imports            # original name is NOT a local binding here
    assert imports["Def"] == "./d"
    assert imports["NS"] == "./n"


def test_array_to_routes_ts_const_menu():
    src = (b"const MENU=[{path:'/users',label:'Users',roles:['admin'],"
           b"children:[{path:'/users/:id',label:'Detail'}]}]")
    root = _parse_src(src)
    arr = _find_node(root, "array")
    routes = rp._array_to_routes(arr, rp.FIELD_MAPS["ts-const"], {})
    assert len(routes) == 1
    top = routes[0]
    assert top.path == "/users"
    assert top.title == "Users"
    assert top.roles == ["admin"]
    assert len(top.children) == 1
    assert top.children[0].path == "/users/:id"
    assert top.children[0].title == "Detail"


def test_object_to_route_detects_catch_all():
    src = b"const R=[{path:'*',label:'NotFound'}]"
    root = _parse_src(src)
    routes = rp._array_to_routes(_find_node(root, "array"), rp.FIELD_MAPS["ts-const"], {})
    assert routes[0].is_catch_all is True


def test_string_list_skips_non_literals():
    src = b"const R=[{path:'/x',label:'X',roles:['a', SOME_CONST, 'b']}]"
    root = _parse_src(src)
    routes = rp._array_to_routes(_find_node(root, 'array'), rp.FIELD_MAPS['ts-const'], {})
    assert routes[0].roles == ['a', 'b']


def test_component_resolves_identifier_to_import_source():
    src = (b"import UserList from './pages/UserList.vue'\n"
           b"const R=[{path:'/u',component:UserList}]")
    root = _parse_src(src)
    imports = rp._collect_imports(root)
    routes = rp._array_to_routes(_find_node(root, "array"), rp.FIELD_MAPS["vue"], imports)
    assert routes[0].component == "UserList"
    assert routes[0].component_source == "./pages/UserList.vue"


def test_component_resolves_lazy_import():
    src = b"const R=[{path:'/u',component:() => import('./pages/UserList.vue')}]"
    root = _parse_src(src)
    routes = rp._array_to_routes(_find_node(root, "array"), rp.FIELD_MAPS["vue"], {})
    assert routes[0].component_source == "./pages/UserList.vue"


def test_component_resolves_react_lazy_import():
    src = b"const R=[{path:'/u',component:React.lazy(() => import('./pages/UserList.vue'))}]"
    root = _parse_src(src)
    routes = rp._array_to_routes(_find_node(root, "array"), rp.FIELD_MAPS["vue"], {})
    assert routes[0].component_source == "./pages/UserList.vue"


FIX = Path(__file__).parent / "fixtures" / "routes"


def test_parse_ts_const_menu():
    root = rp.parse_route_file(FIX / "ts-const-menu.ts", "ts-const")
    assert root is not None
    assert {c.path for c in root.children} == {"/dashboard", "/admin"}
    admin = next(c for c in root.children if c.path == "/admin")
    assert {c.title for c in admin.children} == {"Users", "Roles"}


def test_parse_vue_routes():
    root = rp.parse_route_file(FIX / "vue-routes.ts", "vue")
    assert root is not None
    top = {c.path: c for c in root.children}
    assert top["/"].component == "Layout"
    cust = next(c for c in top["/"].children if c.path == "customers")
    assert cust.title == "Customers" and cust.roles == ["sales"]
    assert cust.component_source == "./pages/CustomerList.vue"
    assert top["/old"].redirect_to == "/customers"
    assert any(c.is_catch_all for c in root.children)


def test_parse_angular_routes():
    root = rp.parse_route_file(FIX / "angular-routing.module.ts", "angular")
    assert root is not None
    paths = {c.path for c in root.children}
    assert "customers" in paths and "orders" in paths
    orders = next(c for c in root.children if c.path == "orders")
    assert orders.component_source == "./orders/orders.module"


def test_parse_react_data_router():
    root = rp.parse_route_file(FIX / "react-data-router.tsx", "react")
    assert root is not None
    cust = next(c for c in root.children if c.path == "/customers")
    assert cust.title == "Customers"
    assert cust.component == "CustomerList"
