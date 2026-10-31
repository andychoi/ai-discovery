"""Contract every tool must meet (M5.3): budget + shed order, schema, honest wording."""

from __future__ import annotations

import re

import pytest
from jsonschema import Draft202012Validator

from ai_discovery.mcp import registry

TOOLS = list(registry.discover().values())
CERTAINTY = re.compile(r"\bwill (break|fail)\b|\bguarantee", re.IGNORECASE)


@pytest.mark.parametrize("spec", TOOLS, ids=lambda s: s.name)
def test_every_tool_declares_a_budget(spec) -> None:
    assert spec.budget is not None and 500 <= spec.budget.max_tokens <= 20_000
    assert isinstance(spec.budget.shed, tuple)
    assert spec.budget.shed or spec.name == "get_symbol_source", \
        f"{spec.name}: list-returning tools must declare a shed order"


@pytest.mark.parametrize("spec", TOOLS, ids=lambda s: s.name)
def test_schema_and_description(spec) -> None:
    Draft202012Validator.check_schema(spec.input_schema)
    assert spec.input_schema["type"] == "object" and spec.input_schema.get("additionalProperties") is False
    assert len(spec.description) >= 40 and not CERTAINTY.search(spec.description)
    assert re.fullmatch(r"[a-z_]{3,40}", spec.name)
