"""Tool discovery, profiles and --tools selection (M5.3)."""

from __future__ import annotations

import pytest

from ai_discovery.mcp import registry
from ai_discovery.mcp.registry import ToolSelectionError, schema_tokens, select

DEFAULT = ["get_overview", "search", "get_context", "get_impact", "get_change_impact", "get_screen", "get_entity",
           "get_flow"]
OPT_IN = {"get_table", "get_symbol_source", "get_path"}


def _names(spec):
    return [s.name for s in select(spec)]


def test_default_profile_is_the_design_doc_eight() -> None:
    assert _names(None) == DEFAULT
    assert _names("default") == DEFAULT


def test_lean_and_all() -> None:
    lean = _names("lean")
    assert len(lean) <= 5 and set(lean) <= set(DEFAULT) and {"search", "get_context", "get_impact"} <= set(lean)
    assert set(_names("all")) == set(DEFAULT) | OPT_IN
    assert schema_tokens(select("lean")) < schema_tokens(select("default")) < schema_tokens(select("all"))


def test_selection_edits() -> None:
    assert _names("+get_table") == DEFAULT + ["get_table"]
    assert "get_flow" not in _names("-get_flow") and len(_names("-get_flow")) == 7
    assert _names("search,get_context") == ["search", "get_context"]          # bare names = allowlist
    assert set(_names("lean,+get_table")) == set(_names("lean")) | {"get_table"}
    assert _names("get_path,search") == ["search", "get_path"]               # registry order, not input order


def test_selection_errors() -> None:
    with pytest.raises(ToolSelectionError, match="unknown tool"):
        select("get_nothing")
    with pytest.raises(ToolSelectionError, match="selects no tools"):
        select("search,-search")


def test_discovery_is_automatic() -> None:
    tools = registry.discover()
    assert set(tools) == set(DEFAULT) | OPT_IN
    assert all(tools[n].opt_in for n in OPT_IN) and not any(tools[n].opt_in for n in DEFAULT)
