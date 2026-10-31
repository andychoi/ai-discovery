"""Shield and budget (M5.3)."""

from __future__ import annotations

from ai_discovery.mcp.middleware import call_tool, estimate_tokens
from ai_discovery.mcp.tool import Budget, ToolSpec, schema
from ai_discovery.query.answer import Answer
from ai_discovery.query.session import Project


def _spec(handler, *, shed=("items",), max_tokens=600, props=None) -> ToolSpec:
    return ToolSpec(name="fake_tool", description="A fake tool used to exercise the middleware pipeline.",
                    input_schema=schema(props or {"n": {"type": "integer"}}), handler=handler,
                    budget=Budget(max_tokens=max_tokens, shed=shed))


def _big(project, args):
    ans = Answer()
    ans.data = {"items": ans.cap("items", [{"id": i, "pad": "x" * 40} for i in range(args.get("n", 200))], 150),
                "notes": ["keep me"]}
    return ans.to_dict()


def test_shield_turns_exceptions_into_answers(tmp_path) -> None:
    def boom(project, args):
        raise RuntimeError("database is locked")

    out = call_tool(_spec(boom), Project.open(tmp_path), {})
    assert out["status"] == "unknown" and out["data"]["reason"] == "internal_error"
    assert out["data"]["error"] == "RuntimeError" and out["data"]["remedy"] and out["meta"]["tool"] == "fake_tool"


def test_bad_arguments_are_success_shaped(tmp_path) -> None:
    out = call_tool(_spec(_big), Project.open(tmp_path), {"n": "many", "extra": 1})
    assert out["status"] == "unknown" and out["data"]["reason"] == "bad_arguments"
    assert any("n:" in e for e in out["data"]["errors"]) and out["data"]["remedy"]


def test_budget_sheds_ranked_tail_and_records_true_total(tmp_path) -> None:
    out = call_tool(_spec(_big), Project.open(tmp_path), {"n": 200})
    items = out["data"]["items"]
    assert 0 < len(items) < 150 and [i["id"] for i in items] == list(range(len(items)))   # head kept, in order
    assert out["data"]["notes"] == ["keep me"]
    omitted = {o["field"]: o for o in out["omitted"]}
    assert omitted["items"]["total"] == 200 and omitted["items"]["dropped"] == 200 - len(items)
    assert out["truncated"] is True and out["meta"]["truncated"] is True
    budget = out["meta"]["response_budget"]
    assert budget["max_tokens"] == 600 and budget["used_tokens"] <= 600
    assert estimate_tokens(out) <= 600 + 20      # tool/timing meta added after measuring


def test_under_budget_untouched_and_override(tmp_path) -> None:
    small = call_tool(_spec(_big), Project.open(tmp_path), {"n": 3})
    assert len(small["data"]["items"]) == 3 and small["omitted"] == []
    wide = call_tool(_spec(_big), Project.open(tmp_path), {"n": 200}, max_tokens=20_000)
    assert len(wide["data"]["items"]) == 150 and wide["omitted"][0]["dropped"] == 50     # only the tool's own cap


def test_strings_clipped_when_lists_cannot_help(tmp_path) -> None:
    def blob(project, args):
        return Answer(data={"source": "y" * 50_000}).to_dict()

    out = call_tool(_spec(blob, shed=()), Project.open(tmp_path), {})
    assert len(out["data"]["source"]) <= 4000 and "strings_clipped" in out["meta"]["degraded"]
