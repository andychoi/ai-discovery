from __future__ import annotations
from pathlib import Path
import pytest
from ai_discovery import route_parser as rp
from ai_discovery.route_parser import RouteNode

pytestmark = pytest.mark.skipif(not rp._TS_AVAILABLE, reason="tree-sitter grammars not installed")


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
    def find(n, t):
        if n.type == t: return n
        for c in n.children:
            r = find(c, t)
            if r: return r
    s = find(root, "string")
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
