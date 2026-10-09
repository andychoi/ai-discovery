"""Deterministic recorded-response stand-in for ``ai_discovery.ai.llm_client.LLMClient``.

Used by the golden characterization harness (roadmap item M0.2) so a full
pipeline run needs no provider, no network and no API key.

Responses are looked up by ``sha256(tier + "\\0" + prompt)`` in a recorded
JSON map (``{hash: text_or_object}``). Unknown prompts get a deterministic,
tier-shaped placeholder so every downstream parser succeeds. Every call is
logged (tier, method, prompt hash) so a snapshot can characterise *how many*
LLM calls each stage makes without asserting on prose.

Injection: patch ``ai_discovery.ai.llm_client.LLMClient`` to return a
``StubLLM`` instance — the same seam ``tests/test_integration.py`` uses.
A config-selectable stub provider arrives with roadmap item M1.10.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ai_discovery.ai.llm_client import LLMResponse, StructuredResponse

EMBED_DIM = 256


def prompt_key(tier: str, prompt: str) -> str:
    return hashlib.sha256(f"{tier}\0{prompt}".encode("utf-8")).hexdigest()


def _placeholder_text(tier: str, prompt: str) -> str:
    low = prompt.lower()
    if tier == "tier1":
        if "extract" in low and "claim" in low:
            return json.dumps(["The component performs the documented operation"])
        if "verif" in low:
            return "verified"
        return json.dumps({
            "purpose": "Handles business logic",
            "business_rules": "Validates input",
            "io_summary": "Reads from DB",
            "tech_debt_signals": "None detected",
        })
    if tier == "tier2":
        return json.dumps([{
            "name": "Stub Flow",
            "flow_type": "user_flow",
            "description": "Deterministic placeholder flow",
            "involved_nodes": [],
        }])
    if tier in ("tier3", "tier3p", "tier3d"):
        return "# Current State\n\nDeterministic placeholder document.\n\nConfidence: 0.8"
    return "stub response"


def _default_for_schema(schema: dict[str, Any]) -> Any:
    """Smallest value that satisfies a JSON schema's required shape."""
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    typ = schema.get("type")
    if isinstance(typ, list):
        typ = next((t for t in typ if t != "null"), typ[0] if typ else "string")
    if typ == "object":
        props = schema.get("properties", {})
        return {name: _default_for_schema(sub) for name, sub in props.items()}
    if typ == "array":
        return []
    if typ == "integer":
        return 0
    if typ == "number":
        return 0.0
    if typ == "boolean":
        return False
    return "stub"


class StubLLM:
    """Duck-typed replacement for ``LLMClient`` (only the surface the pipeline uses)."""

    def __init__(self, recorded: dict[str, Any] | None = None) -> None:
        self.recorded = recorded or {}
        self.calls: list[tuple[str, str, str]] = []  # (method, tier, prompt hash)

    @classmethod
    def from_file(cls, path: Path) -> "StubLLM":
        recorded = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls(recorded)

    # -- text invocations -------------------------------------------------
    def _text(self, method: str, tier: str, prompt: str) -> LLMResponse:
        key = prompt_key(tier, prompt)
        self.calls.append((method, tier, key))
        hit = self.recorded.get(key)
        text = hit if isinstance(hit, str) else _placeholder_text(tier, prompt)
        return LLMResponse(text=text, tokens_in=0, tokens_out=0, model=f"stub-{tier}", tier=tier)

    def invoke(self, tier: str, prompt: str, max_tokens: int = 4096, **_: Any) -> LLMResponse:
        return self._text("invoke", tier, prompt)

    def invoke_with_advisor(self, tier: str, prompt: str, max_tokens: int = 4096, **_: Any) -> LLMResponse:
        return self._text("invoke_with_advisor", tier, prompt)

    def invoke_structured(self, tier: str, prompt: str, schema: dict, **_: Any) -> StructuredResponse:
        key = prompt_key(tier, prompt)
        self.calls.append(("invoke_structured", tier, key))
        hit = self.recorded.get(key)
        data = hit if isinstance(hit, dict) else _default_for_schema(schema)
        return StructuredResponse(data=data, tokens_in=0, tokens_out=0, model=f"stub-{tier}",
                                  tier=tier, via_tool=True, raw_text=json.dumps(data))

    # -- embeddings & cost bookkeeping ------------------------------------
    def get_embedding(self, text: str, *_: Any, **__: Any) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(EMBED_DIM)]

    def total_cost_usd(self) -> float:
        return 0.0

    def get_costs(self) -> dict:
        return {}

    def persist_costs(self, *_: Any, **__: Any) -> None:
        return None

    def __getattr__(self, name: str):
        # Fail loudly on any LLMClient surface the stub doesn't model, so a new
        # call path shows up as a harness failure rather than silent drift.
        raise AttributeError(f"StubLLM has no attribute {name!r}; extend tests/golden/stub_llm.py")

    # -- characterisation -------------------------------------------------
    def call_counts(self) -> dict[str, int]:
        counts = Counter(f"{method}:{tier}" for method, tier, _ in self.calls)
        return dict(sorted(counts.items()))
