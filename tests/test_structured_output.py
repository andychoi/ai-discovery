"""Tests for the durable structured-output path (LLMClient.invoke_structured)
and the shared tolerant-JSON fallback.

Background: screen-spec generation originally did a bare ``json.loads`` on the
model's free text, which failed on Claude's ```json fences (char-0 error) and on
mid-object truncation. The durable fix forces Bedrock tool use so the model
returns schema-validated JSON, with a fence-tolerant text fallback only for
providers without tool support. These tests are fully mocked — no network.
"""

import pytest

import ai_discovery.ai.llm_client as llm_client_mod
from ai_discovery.ai.llm_client import LLMClient, LLMResponse
from ai_discovery.config import DiscoveryConfig
from ai_discovery.shared.json_extract import extract_json_object


# ---------------------------------------------------------------------------
# Shared tolerant-JSON extractor (the fallback for non-tool providers).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"purpose": "x"}', {"purpose": "x"}),                      # bare JSON
        ('```json\n{"purpose": "x"}\n```', {"purpose": "x"}),        # ```json fence
        ('```\n{"b": 3}\n```', {"b": 3}),                            # plain fence
        ('Here is the spec:\n{"a": 2}\nDone.', {"a": 2}),            # prose-wrapped
    ],
)
def test_extract_json_object_tolerates_fences_and_prose(raw, expected):
    assert extract_json_object(raw) == expected


def test_extract_json_object_raises_on_unparseable():
    with pytest.raises(Exception):
        extract_json_object("no json here at all")


# ---------------------------------------------------------------------------
# LLMClient.invoke_structured — Bedrock forced tool use + Ollama fallback.
# ---------------------------------------------------------------------------

_SCHEMA = {"type": "object", "properties": {"purpose": {"type": "string"}}}


def test_invoke_structured_bedrock_uses_forced_tool(monkeypatch):
    cfg = DiscoveryConfig()
    cfg.provider = "bedrock"
    captured = {}

    def fake_converse(model, messages, *, max_tokens, tools, tool_choice, region):
        captured["tools"] = tools
        captured["tool_choice"] = tool_choice
        return {
            "content": [
                {"toolUse": {"name": "emit_screen_spec", "input": {"purpose": "x"}}}
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

    monkeypatch.setattr(llm_client_mod, "converse_bedrock", fake_converse)
    client = LLMClient(cfg)
    result = client.invoke_structured(
        "screen", "prompt", _SCHEMA, tool_name="emit_screen_spec", max_tokens=512,
    )

    assert result.data == {"purpose": "x"}
    assert result.via_tool is True
    # The tool choice must FORCE the named tool — that is what removes the fence/
    # truncation failure modes.
    assert captured["tool_choice"] == {"tool": {"name": "emit_screen_spec"}}
    assert captured["tools"][0]["toolSpec"]["inputSchema"]["json"] == _SCHEMA
    # Cost is tracked under the screen tier.
    assert client.get_costs()["screen"]["tokens_out"] == 20


def test_invoke_structured_bedrock_falls_back_when_model_emits_text(monkeypatch):
    cfg = DiscoveryConfig()
    cfg.provider = "bedrock"

    def fake_converse(model, messages, *, max_tokens, tools, tool_choice, region):
        return {
            "content": [{"text": '```json\n{"purpose": "y"}\n```'}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 5},
        }

    monkeypatch.setattr(llm_client_mod, "converse_bedrock", fake_converse)
    result = LLMClient(cfg).invoke_structured("screen", "p", _SCHEMA, tool_name="t")
    assert result.data == {"purpose": "y"}
    assert result.via_tool is False


def test_invoke_structured_anthropic_uses_forced_tool(monkeypatch):
    cfg = DiscoveryConfig()
    cfg.provider = "anthropic"
    captured = {}

    def fake_structured(model, prompt, schema, *, tool_name, tool_description,
                        max_tokens, api_key, base_url, effort=None, system=""):
        captured.update(model=model, schema=schema, tool_name=tool_name,
                        effort=effort)
        return {"purpose": "x"}, "", 10, 20

    monkeypatch.setattr(llm_client_mod, "invoke_anthropic_structured", fake_structured)
    client = LLMClient(cfg)
    result = client.invoke_structured(
        "screen", "prompt", _SCHEMA, tool_name="emit_screen_spec", max_tokens=512,
    )

    assert result.data == {"purpose": "x"}
    assert result.via_tool is True
    assert captured["model"] == cfg.get_model("screen")
    assert captured["schema"] == _SCHEMA
    assert captured["tool_name"] == "emit_screen_spec"
    # screen tier maps to tier2 → default effort "high" on the anthropic provider
    assert captured["effort"] == "high"
    assert client.get_costs()["screen"]["tokens_out"] == 20


def test_invoke_structured_anthropic_falls_back_when_model_emits_text(monkeypatch):
    cfg = DiscoveryConfig()
    cfg.provider = "anthropic"

    def fake_structured(model, prompt, schema, *, tool_name, tool_description,
                        max_tokens, api_key, base_url, effort=None, system=""):
        return None, '```json\n{"purpose": "y"}\n```', 5, 5

    monkeypatch.setattr(llm_client_mod, "invoke_anthropic_structured", fake_structured)
    result = LLMClient(cfg).invoke_structured("screen", "p", _SCHEMA, tool_name="t")
    assert result.data == {"purpose": "y"}
    assert result.via_tool is False


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_invoke_structured_openai_compat_uses_response_format(monkeypatch, provider):
    cfg = DiscoveryConfig()
    cfg.provider = provider
    captured = {}

    def fake_structured(model, prompt, schema, *, schema_name, schema_description,
                        max_tokens, base_url, api_key, max_tokens_field):
        captured.update(model=model, schema=schema, schema_name=schema_name,
                        max_tokens_field=max_tokens_field)
        return {"purpose": "x"}, '{"purpose": "x"}', 10, 20

    monkeypatch.setattr(llm_client_mod, "invoke_openai_compat_structured",
                        fake_structured)
    client = LLMClient(cfg)
    result = client.invoke_structured(
        "screen", "prompt", _SCHEMA, tool_name="emit_screen_spec", max_tokens=512,
    )

    assert result.data == {"purpose": "x"}
    assert result.via_tool is True
    assert captured["model"] == cfg.get_model("screen")
    assert captured["schema"] == _SCHEMA
    assert captured["schema_name"] == "emit_screen_spec"
    expected_field = "max_completion_tokens" if provider == "openai" else "max_tokens"
    assert captured["max_tokens_field"] == expected_field
    assert client.get_costs()["screen"]["tokens_out"] == 20


def test_invoke_structured_openai_compat_text_fallback(monkeypatch):
    cfg = DiscoveryConfig()
    cfg.provider = "openai"

    def fake_structured(model, prompt, schema, *, schema_name, schema_description,
                        max_tokens, base_url, api_key, max_tokens_field):
        return None, '```json\n{"purpose": "y"}\n```', 5, 5

    monkeypatch.setattr(llm_client_mod, "invoke_openai_compat_structured",
                        fake_structured)
    result = LLMClient(cfg).invoke_structured("screen", "p", _SCHEMA, tool_name="t")
    assert result.data == {"purpose": "y"}
    assert result.via_tool is False


def test_invoke_structured_openai_compat_falls_back_on_request_error(monkeypatch):
    """If the structured request itself errors (e.g. the endpoint rejects
    response_format), invoke_structured degrades to the plain-invoke path
    instead of failing the caller."""
    cfg = DiscoveryConfig()
    cfg.provider = "gemini"

    def fake_structured(*args, **kwargs):
        raise RuntimeError("response_format not supported")

    monkeypatch.setattr(llm_client_mod, "invoke_openai_compat_structured",
                        fake_structured)
    monkeypatch.setattr(
        LLMClient, "invoke",
        lambda self, tier, prompt, max_tokens=4096: LLMResponse(
            text='```json\n{"purpose": "fallback"}\n```', tokens_in=1,
            tokens_out=2, model="gemini-3.5-flash", tier=tier,
        ),
    )
    result = LLMClient(cfg).invoke_structured("screen", "p", _SCHEMA, tool_name="t")
    assert result.data == {"purpose": "fallback"}
    assert result.via_tool is False


def test_invoke_structured_ollama_fallback_parses_fenced_text(monkeypatch):
    cfg = DiscoveryConfig()
    cfg.provider = "ollama"

    monkeypatch.setattr(
        LLMClient, "invoke",
        lambda self, tier, prompt, max_tokens=4096: LLMResponse(
            text='Sure:\n```json\n{"purpose": "z"}\n```', tokens_in=1,
            tokens_out=2, model="gemma", tier=tier,
        ),
    )
    result = LLMClient(cfg).invoke_structured("screen", "p", _SCHEMA, tool_name="t")
    assert result.data == {"purpose": "z"}
    assert result.via_tool is False
