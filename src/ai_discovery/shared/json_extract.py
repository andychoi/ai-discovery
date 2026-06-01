"""Tolerant JSON extraction from LLM free-text responses.

Bedrock/Claude and most local models wrap JSON in ```json ... ``` fences or
surround it with prose, so a bare ``json.loads(text)`` fails at char 0 on
fenced output. The *durable* fix for structured output is tool use / JSON
schema enforcement (see ``LLMClient.invoke_structured``), which removes the
need to parse free text at all. This helper is the shared fallback for the
remaining cases — providers without tool support, or a model that ignores the
tool — and replaces the three ad-hoc fence strippers that previously lived in
``flow_analyzer``, ``self_review`` and ``screen_spec_generator``.
"""

from __future__ import annotations

import json


def extract_json_object(text: str) -> dict:
    """Parse a JSON object from an LLM response, tolerating code fences and prose.

    Raises ``json.JSONDecodeError`` if no JSON object can be recovered.
    """
    cleaned = (text or "").strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: extract the first balanced {...} block from any prose.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise
