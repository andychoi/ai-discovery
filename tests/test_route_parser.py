from __future__ import annotations
from pathlib import Path
import pytest
from ai_discovery import route_parser as rp
from ai_discovery.route_parser import RouteNode


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
