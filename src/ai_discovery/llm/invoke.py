"""The one LLM invoke path (roadmap item M1.10.a).

Every text, converse and embedding call goes through here with an explicit
``LLMSettings`` (no module-global config). Dispatch is by the provider's
protocol in the registry (``providers.py``); the wire calls live in
``transport.py``, referenced through the module so tests can patch
``ai_discovery.llm.transport.<fn>``.

Ported from ``shared/llm_router.py`` (behaviour unchanged): the router's
``configure()``/``get_config()``/``save_config()`` globals are gone, and usage
accounting is a per-call ``usage_logger`` instead of a registered global.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from . import transport
from .providers import get_provider, is_openai_compat
from .settings import DEFAULTS, LLMSettings

log = logging.getLogger(__name__)

UsageLogger = Callable[..., None]

# ── Plain-text thinking cleanup ──────────────────────────────────────────────
# Qwen3.5 sometimes outputs verbose reasoning as plain text (not <think> tags).

_THINKING_HEADER_RE = re.compile(
    r"^(?:Thinking\s+Process|Reasoning|Analysis|Let\s+me\s+(?:think|analyze|reason))[\s:]*\n",
    re.IGNORECASE,
)


def strip_thinking(text: str) -> str:
    """Remove chain-of-thought artefacts from LLM output.

    Handles:
    - ``<think>…</think>`` tags (Qwen3.5 etc.) — closed and truncated/unclosed
    - Claude-flavored tool-use XML (``<function_calls>…</function_calls>``,
      stray ``<invoke name=…>…</invoke>``) emitted by Opus-distilled Qwen
      variants like ``Qwen3.5-27B-Claude-4.6-Opus-Distilled-MLX-4bit`` —
      these bleed through into plain chat responses where no tool use was
      requested, and must not reach the user. Does NOT interfere with
      legitimate tool use because Ollama tool-use is currently unwired
      (see ``agent-engine/providers/llm.py:converse_llm``).
    - Plain-text reasoning headers ("Thinking Process:", "Let me analyze…")
    """
    text = text.strip()
    # 1. Strip <think>…</think> (closed) and <think>… (truncated, unclosed)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
    # 2. Strip Claude-flavored tool-use XML bleed-through from distilled models.
    text = re.sub(r"<function_calls>.*?</function_calls>", "", text, flags=re.DOTALL)
    text = re.sub(r"<function_calls>.*", "", text, flags=re.DOTALL)
    text = re.sub(r"<invoke\b[^>]*>.*?</invoke>", "", text, flags=re.DOTALL)
    text = re.sub(r"<invoke\b[^>]*>.*", "", text, flags=re.DOTALL)
    text = text.strip()
    # 2. Strip plain-text thinking headers and numbered reasoning steps.
    m = _THINKING_HEADER_RE.match(text)
    if m:
        parts = re.split(
            r"\n---+\n|\n\n(?=(?:#{1,3}\s|[A-Z]|\*\*|Based on|The |There |No[, ]))",
            text[m.end():], maxsplit=1,
        )
        if len(parts) > 1:
            text = parts[-1].strip()
    return text


def _log_usage(usage_logger: Optional[UsageLogger], project_slug: str, model_id: str, tier: str,
               tok_in: int, tok_out: int, *, provider: str) -> None:
    tok_in = int(tok_in or 0)
    tok_out = int(tok_out or 0)
    if usage_logger is None:
        log.debug("LLM usage: provider=%s model=%s tier=%s in=%d out=%d",
                  provider, model_id, tier, tok_in, tok_out)
        return
    try:
        usage_logger(project_slug=project_slug, model_id=model_id, tier=tier,
                     tokens_in=tok_in, tokens_out=tok_out, provider=provider)
    except Exception:
        log.debug("LLM usage logger raised; usage entry skipped", exc_info=True)


def _text_call(settings: LLMSettings, provider: str, model_id: str, prompt: str, max_tokens: int, *,
               enable_thinking: bool = False, num_ctx: Optional[int] = None) -> tuple:
    spec = get_provider(provider)
    if spec.protocol == "bedrock":
        return transport.invoke_bedrock(model_id, prompt, max_tokens, settings.region())
    if spec.protocol == "anthropic":
        return transport.invoke_anthropic(model_id, prompt, max_tokens,
                                          api_key=settings.api_key("anthropic"),
                                          base_url=settings.get("anthropic.url", ""))
    if spec.protocol == "openai_compat":
        return transport.invoke_openai_compat(model_id, prompt, max_tokens,
                                              base_url=settings.url(provider),
                                              api_key=settings.api_key(provider),
                                              max_tokens_field=spec.max_tokens_field)
    return transport.invoke_ollama(model_id, prompt, max_tokens, settings.url(provider),
                                   enable_thinking=enable_thinking, num_ctx=num_ctx,
                                   api_key=settings.api_key(provider))


def invoke_text(
    settings: LLMSettings,
    tier: str,
    prompt: str,
    max_tokens: int = 1000,
    *,
    doc_type: Optional[str] = None,
    project_slug: str = "",
    enable_thinking: bool = False,
    num_ctx: Optional[int] = None,
    usage_logger: Optional[UsageLogger] = None,
) -> dict:
    """Text in, text out, with metadata.

    Returns ``{"text", "model_id", "provider", "truncated", "input_tokens",
    "output_tokens"}``. ``truncated`` means the output used ≥95% of
    ``max_tokens``. Thinking is off by default (some models spend the whole
    budget reasoning); ``num_ctx`` caps the context window on Ollama-like
    providers and is ignored elsewhere.
    """
    if doc_type:
        tier = settings.tier_for_doc_type(doc_type)
    provider = settings.provider
    model_id = settings.model(tier)
    result = _text_call(settings, provider, model_id, prompt, max_tokens,
                        enable_thinking=enable_thinking, num_ctx=num_ctx)
    text, tok_in, tok_out = result
    _log_usage(usage_logger, project_slug, model_id, tier, tok_in, tok_out, provider=provider)
    return {
        "text": strip_thinking(text),
        "model_id": model_id,
        "provider": provider,
        "truncated": tok_out >= int(max_tokens * 0.95),
        "input_tokens": tok_in,
        "output_tokens": tok_out,
        # transport.TextResult carries cache counts; plain tuples (test doubles) mean 0
        "cache_read_tokens": getattr(result, "cache_read", 0),
        "cache_write_tokens": getattr(result, "cache_write", 0),
    }


def converse(
    settings: LLMSettings,
    tier: str,
    messages: list[dict],
    *,
    system: str = "",
    max_tokens: int = 4096,
    tools: Optional[list[dict]] = None,
    doc_type: Optional[str] = None,
    project_slug: str = "",
    usage_logger: Optional[UsageLogger] = None,
) -> dict:
    """Multi-turn + tool use via Bedrock Converse; other providers get a flattened text prompt."""
    if doc_type:
        tier = settings.tier_for_doc_type(doc_type)
    provider = settings.provider
    if provider == "bedrock":
        model_id = settings.model(tier, "bedrock")
        result = transport.converse_bedrock(model_id, messages, system=system, max_tokens=max_tokens,
                                            tools=tools, region=settings.region())
        _log_usage(usage_logger, project_slug, model_id, tier,
                   result["usage"]["input_tokens"], result["usage"]["output_tokens"], provider="bedrock")
        return result
    if tools:
        log.warning("%s provider does not support tool use here — tools will be ignored", provider)
    parts = []
    if system:
        parts.append(f"[system]: {system}")
    for m in messages:
        role = m.get("role", "user")
        for block in m.get("content", []):
            if "text" in block:
                parts.append(f"[{role}]: {block['text']}" if role != "user" else block["text"])
    prompt = "\n\n".join(parts)
    model_id = settings.model(tier)
    text, tok_in, tok_out = _text_call(settings, provider, model_id, prompt, max_tokens)
    _log_usage(usage_logger, project_slug, model_id, tier, tok_in, tok_out, provider=provider)
    return {
        "content": [{"text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": len(prompt) // 4, "output_tokens": len(text) // 4},
    }


def _no_embeddings(provider: str) -> RuntimeError:
    return RuntimeError(
        f"{provider.capitalize()} has no embedding API — set a different embedding provider "
        "(e.g. rag.embedding_provider: bedrock | ollama | openai | gemini)"
    )


def embed(settings: LLMSettings, text: str, *, provider: Optional[str] = None, project_slug: str = "",
          usage_logger: Optional[UsageLogger] = None) -> list[float]:
    """One embedding vector. ``provider`` overrides the chat provider (needed for Anthropic)."""
    provider = provider or settings.provider
    spec = get_provider(provider)
    if not spec.embeddings:
        raise _no_embeddings(provider)
    if spec.protocol == "bedrock":
        model_id = settings.get("tier.embedding.bedrock_model", DEFAULTS["tier.embedding.bedrock_model"])
        vec = transport.embed_bedrock(model_id, text, settings.region())
    elif spec.protocol == "openai_compat":
        model_id = settings.get(f"tier.embedding.{spec.model_suffix}",
                                DEFAULTS[f"tier.embedding.{spec.model_suffix}"])
        vec = transport.embed_openai_compat(model_id, text, base_url=settings.url(provider),
                                            api_key=settings.api_key(provider))
    else:
        model_id = settings.embedding_model(provider)
        vec = transport.embed_ollama(model_id, text, settings.url(provider), api_key=settings.api_key(provider))
    _log_usage(usage_logger, project_slug, model_id, "embedding", len(text) // 4, 0, provider=provider)
    return vec


def embed_batch(settings: LLMSettings, texts: list[str], *, provider: Optional[str] = None,
                project_slug: str = "", batch_size: int = 20,
                usage_logger: Optional[UsageLogger] = None) -> list[list[float]]:
    """Batch embeddings via the OpenAI-shape ``/embeddings`` endpoint where available;
    sequential ``embed`` for Bedrock or when a batch request fails."""
    provider = provider or settings.provider
    spec = get_provider(provider)
    if not spec.embeddings:
        raise _no_embeddings(provider)
    results: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        if spec.protocol != "bedrock":
            if is_openai_compat(provider):
                embed_url = f"{settings.url(provider).rstrip('/')}/embeddings"
            else:
                embed_url = f"{settings.url(provider)}/v1/embeddings"
            model = settings.embedding_model(provider)
            try:
                headers = {}
                api_key = settings.api_key(provider)
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                resp = transport.httpx.post(embed_url, json={"model": model, "input": batch}, timeout=120,
                                            headers=headers or None)
                resp.raise_for_status()
                data = resp.json()["data"]
                results.extend(d["embedding"] for d in sorted(data, key=lambda d: d["index"]))
                _log_usage(usage_logger, project_slug, model, "embedding", sum(len(t) // 4 for t in batch), 0,
                           provider=provider)
                continue
            except Exception as e:
                log.debug("%s batch embedding failed, falling back to sequential: %s", provider, e,
                          exc_info=True)
        for text in batch:
            results.append(embed(settings, text, provider=provider, project_slug=project_slug,
                                 usage_logger=usage_logger))
    return results


def resolve_tier_target(settings: LLMSettings, tier: str) -> tuple[str, str, str]:
    """``(provider, model_id, region-or-base-url)`` for a tier (used by model warming)."""
    provider = settings.provider
    model = settings.model(tier)
    if provider == "bedrock":
        return provider, model, settings.region()
    return provider, model, settings.url(provider)
