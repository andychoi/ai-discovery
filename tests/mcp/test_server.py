"""MCP server over the real SDK (in-process client): listing, calls, golden responses (M5.3)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.mcp.conftest import call, list_tools

GOLDEN = Path(__file__).resolve().parent / "golden"
SVC = "com.example.service.ProductService.getAllProducts"
VOLATILE_META = {"timing_ms", "indexed_commit", "head_commit", "scan_id"}

CASES = {
    "get_overview": {},
    "search": {"query": "getAllProducts", "limit": 5},
    "get_context": {"target": "ProductController.list"},
    "get_impact": {"target": SVC},
    "get_change_impact": {"working_tree": True},
    "get_screen": {"id_or_route": "products-list"},
    "get_entity": {"name": "Order"},
    "get_flow": {"id": "checkout"},
    "get_table": {"name": "orders"},
    "get_symbol_source": {"target": SVC},
    "get_path": {"source": "ProductController.list", "target": SVC},
}


def _normalise(answer: dict) -> dict:
    answer = json.loads(json.dumps(answer))
    answer["meta"] = {k: v for k, v in answer["meta"].items() if k not in VOLATILE_META}
    if isinstance(answer["data"].get("source"), dict) and "base" in answer["data"]["source"]:
        answer["data"]["source"]["base"] = "<indexed>"
    return answer


@pytest.mark.parametrize("tool", sorted(CASES))
def test_golden_responses(scanned, tool) -> None:
    got = _normalise(call(scanned, tool, CASES[tool]))
    path = GOLDEN / f"{tool}.json"
    if os.environ.get("UPDATE_MCP_GOLDEN") or not path.exists():
        path.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
        if not os.environ.get("UPDATE_MCP_GOLDEN"):
            pytest.fail(f"golden {path.name} was missing and has been written; re-run")
    assert got == json.loads(path.read_text()), f"{tool} response changed; UPDATE_MCP_GOLDEN=1 to accept"


def test_list_tools_profiles(scanned) -> None:
    default = list_tools(scanned)
    assert [t.name for t in default][:3] == ["get_overview", "search", "get_context"] and len(default) == 8
    for t in default:
        assert t.description and t.input_schema["type"] == "object"
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
    assert len(list_tools(scanned, "lean")) <= 5


def test_answers_carry_meta_and_status(scanned) -> None:
    ans = call(scanned, "get_impact", {"target": SVC})
    assert ans["status"] == "ok" and ans["data"]["entry_points_reached"] == ["endpoint:GET-/api/catalog/products"]
    meta = ans["meta"]
    assert meta["tool"] == "get_impact" and meta["behind_by"] == 0 and meta["response_budget"]["max_tokens"] == 8000
    assert "content_trust" in meta and meta["timing_ms"] >= 0


def test_errors_are_success_shaped(scanned) -> None:
    bad = call(scanned, "get_context", {"target": 5})
    assert bad["status"] == "unknown" and bad["data"]["reason"] == "bad_arguments" and bad["data"]["remedy"]
    missing = call(scanned, "get_context", {"target": "NoSuchThing"})
    assert missing["status"] == "not_found" and missing["data"]["remedy"]
    unknown = call(scanned, "get_nothing", {})
    assert unknown["data"]["reason"] == "unknown_tool" and "search" in unknown["data"]["available"]
    gated = call(scanned, "get_table", {"name": "orders"}, tools="default")     # opt-in tool not enabled
    assert gated["data"]["reason"] == "unknown_tool"


def test_not_indexed_remedy(tmp_path) -> None:
    from ai_discovery.query import Project

    (tmp_path / ".git").mkdir()
    ans = call(Project.open(tmp_path), "get_overview")
    assert ans["status"] == "not_indexed" and "discover scan" in ans["data"]["remedy"]


def test_server_budget_override_sheds(scanned) -> None:
    ans = call(scanned, "search", {"query": "com", "limit": 50}, max_tokens=500)
    assert ans["truncated"] is True and ans["meta"]["response_budget"]["used_tokens"] <= 500
    assert any(o["field"] == "hits" for o in ans["omitted"])
