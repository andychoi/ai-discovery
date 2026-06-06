"""LLM Router — provider/model abstraction for the Discovery CLI.

Resolution order: runtime ``save_config`` override → ``configure(DiscoveryConfig)``
→ env var → hardcoded default (``model_defaults.MODELS``). The DiscoveryConfig
layer itself already embodies YAML > env > defaults, so callers normally just
``configure(DiscoveryConfig.load(...))`` once at startup.

Vendored from DocHub, where overrides lived in an ``llm_config`` DB table;
this version is standalone — no DB dependencies. Usage accounting is exposed
via ``set_usage_logger`` so the host app can hook in its own cost tracking.

Supports AWS Bedrock and Ollama-compatible providers (ollama, mlx-gemma,
mlx-qwen). For local Apple Silicon inference, Ollama uses the MLX backend
automatically; for advanced use (LoRA, custom embeddings), point OLLAMA_URL
at ai-mlx-server.
"""
from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:  # avoid an upward dependency from shared/ at runtime
    from ai_discovery.config import DiscoveryConfig

from ai_discovery.shared.model_defaults import MODELS, DEFAULT_PROVIDER
from ai_discovery.shared.llm_invoke import (  # re-exported
    invoke_bedrock, invoke_ollama,
    embed_bedrock, embed_ollama,
    converse_bedrock,
    invoke_openai_compat, embed_openai_compat,
    invoke_anthropic,
)

log = logging.getLogger(__name__)

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


# ── Defaults derived from ai_discovery.shared model_defaults ──────────────────────────────


def _build_defaults() -> dict[str, str]:
    """Build the flat key→value defaults dict from MODELS."""
    d: dict[str, str] = {"provider": DEFAULT_PROVIDER}
    for provider, models in MODELS.items():
        for key, value in models.items():
            if key == "region":
                d[f"{provider}.{key}"] = value
            elif key == "url":
                d[f"{provider}.{key}"] = value
            else:
                d[f"tier.{key}.{provider}_model"] = value
    return d


_DEFAULTS: dict[str, str] = _build_defaults()

# Providers that speak the Ollama HTTP protocol (native Ollama + ai-mlx-server
# variants). All share the invoke_ollama / embed_ollama code path but resolve
# different URL and model keys. Adding a new ollama-compatible provider is as
# simple as adding it to MODELS and listing it here.
_OLLAMA_LIKE_PROVIDERS: frozenset[str] = frozenset({"ollama", "mlx-gemma", "mlx-qwen"})


def _ollama_like_keys(provider: str) -> tuple[str, str, str, str]:
    """Return (url_key, model_suffix, api_key_env, default_url) for an
    ollama-compatible provider. Raises for providers that don't speak the
    Ollama protocol — callers should check with ``_is_ollama_like`` first.
    """
    if provider == "mlx-gemma":
        return ("mlx-gemma.url", "mlx-gemma_model", "MLX_API_KEY",
                _DEFAULTS.get("mlx-gemma.url", "http://localhost:11435"))
    if provider == "mlx-qwen":
        return ("mlx-qwen.url", "mlx-qwen_model", "MLX_API_KEY",
                _DEFAULTS.get("mlx-qwen.url", "http://localhost:11435"))
    return ("ollama.url", "ollama_model", "", _DEFAULTS["ollama.url"])


def _is_ollama_like(provider: str) -> bool:
    return provider in _OLLAMA_LIKE_PROVIDERS


# OpenAI-compatible cloud providers — same chat-completions wire shape,
# different base URL / auth / output-cap field. Adding another compatible
# provider is one new entry here plus a MODELS entry.
# Tuple: (url_key, model_suffix, api_key_env, default_url, max_tokens_field)
_OPENAI_COMPAT_PROVIDERS: dict[str, tuple[str, str, str, str, str]] = {
    "openai": ("openai.url", "openai_model", "OPENAI_API_KEY",
               "https://api.openai.com/v1", "max_completion_tokens"),
    "gemini": ("gemini.url", "gemini_model", "GEMINI_API_KEY",
               "https://generativelanguage.googleapis.com/v1beta/openai",
               "max_tokens"),
}


def _is_openai_compat(provider: str) -> bool:
    return provider in _OPENAI_COMPAT_PROVIDERS


def max_tokens_field_for(provider: str) -> str:
    """Output-cap parameter name for an OpenAI-compatible provider
    (gpt-5 family requires max_completion_tokens; Gemini uses max_tokens)."""
    return _OPENAI_COMPAT_PROVIDERS[provider][4]


def _resolve_api_key(config: dict, provider: str, env_var: str) -> str:
    """API key for a provider: config-supplied (via configure(), e.g. from
    discovery.yaml) takes precedence over the environment variable."""
    key = config.get(f"{provider}.api_key", "")
    if key:
        return key
    return (os.getenv(env_var) or "") if env_var else ""


def _model_suffix(provider: str) -> str:
    """Tier-model config-key suffix for any provider (e.g. 'openai_model')."""
    if provider == "bedrock":
        return "bedrock_model"
    if provider == "anthropic":
        return "anthropic_model"
    if _is_openai_compat(provider):
        return _OPENAI_COMPAT_PROVIDERS[provider][1]
    return _ollama_like_keys(provider)[1]


# Env var overrides (middle priority)
_ENV_MAP: dict[str, str] = {
    "provider":               "LLM_PROVIDER",
    "bedrock.region":         "AWS_REGION_NAME",
    "ollama.url":             "OLLAMA_URL",
    "mlx-gemma.url":          "MLX_SERVER_URL",
    "mlx-qwen.url":           "MLX_SERVER_URL",  # both variants share the server
    "tier.embedding.bedrock_model": "LLM_EMBEDDING_BEDROCK_MODEL",
    "tier.fast.bedrock_model":      "LLM_FAST_BEDROCK_MODEL",
    "tier.standard.bedrock_model":  "LLM_STANDARD_BEDROCK_MODEL",
    "tier.expert.bedrock_model":    "LLM_EXPERT_BEDROCK_MODEL",
    "tier.heavy.bedrock_model":     "LLM_HEAVY_BEDROCK_MODEL",
    "tier.deep.bedrock_model":      "LLM_DEEP_BEDROCK_MODEL",  # legacy alias
    "tier.embedding.ollama_model":  "LLM_EMBEDDING_OLLAMA_MODEL",
    "tier.fast.ollama_model":       "LLM_FAST_OLLAMA_MODEL",
    "tier.standard.ollama_model":   "LLM_STANDARD_OLLAMA_MODEL",
    "tier.expert.ollama_model":     "LLM_EXPERT_OLLAMA_MODEL",
    "tier.heavy.ollama_model":      "LLM_HEAVY_OLLAMA_MODEL",
    "tier.deep.ollama_model":       "LLM_DEEP_OLLAMA_MODEL",  # legacy alias
    "tier.embedding.mlx-gemma_model": "LLM_EMBEDDING_MLX_GEMMA_MODEL",
    "tier.fast.mlx-gemma_model":      "LLM_FAST_MLX_GEMMA_MODEL",
    "tier.standard.mlx-gemma_model":  "LLM_STANDARD_MLX_GEMMA_MODEL",
    "tier.expert.mlx-gemma_model":    "LLM_EXPERT_MLX_GEMMA_MODEL",
    "tier.heavy.mlx-gemma_model":     "LLM_HEAVY_MLX_GEMMA_MODEL",
    "tier.embedding.mlx-qwen_model":  "LLM_EMBEDDING_MLX_QWEN_MODEL",
    "tier.fast.mlx-qwen_model":       "LLM_FAST_MLX_QWEN_MODEL",
    "tier.standard.mlx-qwen_model":   "LLM_STANDARD_MLX_QWEN_MODEL",
    "tier.expert.mlx-qwen_model":     "LLM_EXPERT_MLX_QWEN_MODEL",
    "tier.heavy.mlx-qwen_model":      "LLM_HEAVY_MLX_QWEN_MODEL",
    "openai.url":                     "OPENAI_BASE_URL",
    "tier.embedding.openai_model":    "LLM_EMBEDDING_OPENAI_MODEL",
    "tier.fast.openai_model":         "LLM_FAST_OPENAI_MODEL",
    "tier.standard.openai_model":     "LLM_STANDARD_OPENAI_MODEL",
    "tier.expert.openai_model":       "LLM_EXPERT_OPENAI_MODEL",
    "tier.heavy.openai_model":        "LLM_HEAVY_OPENAI_MODEL",
    "gemini.url":                     "GEMINI_BASE_URL",
    "tier.embedding.gemini_model":    "LLM_EMBEDDING_GEMINI_MODEL",
    "tier.fast.gemini_model":         "LLM_FAST_GEMINI_MODEL",
    "tier.standard.gemini_model":     "LLM_STANDARD_GEMINI_MODEL",
    "tier.expert.gemini_model":       "LLM_EXPERT_GEMINI_MODEL",
    "tier.heavy.gemini_model":        "LLM_HEAVY_GEMINI_MODEL",
    "anthropic.url":                  "ANTHROPIC_BASE_URL",
    "tier.fast.anthropic_model":      "LLM_FAST_ANTHROPIC_MODEL",
    "tier.standard.anthropic_model":  "LLM_STANDARD_ANTHROPIC_MODEL",
    "tier.expert.anthropic_model":    "LLM_EXPERT_ANTHROPIC_MODEL",
    "tier.heavy.anthropic_model":     "LLM_HEAVY_ANTHROPIC_MODEL",
}

# Doc-type → tier mapping
DOC_TYPE_TIER: dict[str, str] = {
    "brd":             "standard",
    "design":          "standard",
    "security-design": "standard",
    "adr":             "standard",
    "data-model":      "standard",
    "spec":            "standard",
    "sla-nfr":         "fast",
    "interface":       "fast",
    "integration":     "fast",
    "test-plan":       "fast",
    "data-migration":  "fast",
    "deployment":      "fast",
    "runbook":         "fast",
    "pcr":             "fast",
    "agent-config":    "fast",
    "ux-design":    "standard",
    "gap-analysis": "standard",
    "dev-log":         "fast",
    "meeting-minutes": "fast",
}


# ── Override layers (replaces the DocHub llm_config DB table) ─────────────────
#
# _discovery_overrides — flattened from a DiscoveryConfig via configure()
# _runtime_overrides   — explicit save_config() calls (highest priority)

_discovery_overrides: dict[str, str] = {}
_runtime_overrides: dict[str, str] = {}


def _flatten_discovery_config(cfg: "DiscoveryConfig") -> dict[str, str]:
    """Map DiscoveryConfig fields onto the router's flat key space.

    Discovery tier → router tier (see config.py "Discovery tier mapping"):
      tier1 → fast, tier2 → standard,
      tier3 (active) → expert (tier3p if cfg.prod else tier3d),
      tier3p → heavy, expert value also aliased to legacy "deep".
    """
    o: dict[str, str] = {"provider": cfg.provider}
    provider_cfgs = {
        "bedrock": cfg.bedrock,
        "ollama": cfg.ollama,
        "mlx-gemma": cfg.mlx_gemma,
        "mlx-qwen": cfg.mlx_qwen,
        "openai": cfg.openai,
        "gemini": cfg.gemini,
        "anthropic": cfg.anthropic,
    }
    for name, pc in provider_cfgs.items():
        suffix = f"{name}_model"
        o[f"tier.fast.{suffix}"] = pc.tier1
        o[f"tier.standard.{suffix}"] = pc.tier2
        expert = pc.tier3p if cfg.prod else pc.tier3d
        o[f"tier.expert.{suffix}"] = expert
        o[f"tier.deep.{suffix}"] = expert  # legacy alias
        o[f"tier.heavy.{suffix}"] = pc.tier3p
        if name == "bedrock":
            o["bedrock.region"] = pc.region
        else:
            o[f"{name}.url"] = pc.base_url
            api_key = getattr(pc, "api_key", "")
            if api_key:
                o[f"{name}.api_key"] = api_key
    o["tier.embedding.bedrock_model"] = cfg.rag.bedrock_model
    o["tier.embedding.ollama_model"] = cfg.rag.ollama_model
    return o


def configure(cfg: "Optional[DiscoveryConfig]") -> None:
    """Register a DiscoveryConfig as the router's config source.

    Call once at startup with ``DiscoveryConfig.load(...)``. Pass ``None``
    to unregister (env/defaults resolution only). Replaces the DocHub
    DB-override layer.
    """
    global _discovery_overrides
    _discovery_overrides = _flatten_discovery_config(cfg) if cfg is not None else {}


def get_config() -> dict[str, str]:
    """Return resolved config: save_config > DiscoveryConfig > env > default."""
    config = dict(_DEFAULTS)
    # Apply env overrides
    for key, env_var in _ENV_MAP.items():
        val = os.getenv(env_var)
        if val:
            config[key] = val
    # Apply DiscoveryConfig overrides (registered via configure())
    config.update(_discovery_overrides)
    # Apply runtime overrides (highest priority)
    config.update(_runtime_overrides)
    return config


def reset_config(key: str | None = None) -> int:
    """Drop runtime overrides so configure()/env/defaults take effect.

    With ``key=None`` wipes every runtime override. With a specific key,
    wipes just that one. Unknown keys are a no-op (return 0). Returns the
    number of overrides removed.
    """
    if key is None:
        n = len(_runtime_overrides)
        _runtime_overrides.clear()
        return n
    return 1 if _runtime_overrides.pop(key, None) is not None else 0


def save_config(updates: dict[str, str]) -> None:
    """Set in-memory runtime overrides (highest-priority config layer).

    Keys are validated against the known key space. Persistent configuration
    belongs in discovery.yaml (loaded via DiscoveryConfig + configure()).
    """
    _ALLOWED_KEYS = set(_DEFAULTS.keys())
    bad = set(updates) - _ALLOWED_KEYS
    if bad:
        raise ValueError(f"Unknown LLM config key(s): {sorted(bad)}")
    _runtime_overrides.update(updates)


def _tier_for_doc_type(doc_type: str) -> str:
    return DOC_TYPE_TIER.get(doc_type, "standard")


def resolve_model_id(tier: str, *, doc_type: str | None = None) -> str:
    """Return the actual model ID that would be used for the given tier/doc_type."""
    if doc_type:
        tier = _tier_for_doc_type(doc_type)
    config = get_config()
    suffix = _model_suffix(config["provider"])
    return config.get(f"tier.{tier}.{suffix}", config[f"tier.standard.{suffix}"])


def resolve_tier_target(tier: str) -> tuple[str, str, str]:
    """Return (provider, model_id, base_url_or_region) for a tier.

    For Bedrock the third element is the AWS region; for all URL-based
    providers (Ollama-like, OpenAI-compatible, Anthropic) it is the base URL.
    Used by startup-time warming to look up the model and URL without
    duplicating config logic.
    """
    config = get_config()
    provider = config["provider"]
    suffix = _model_suffix(provider)
    model = config.get(f"tier.{tier}.{suffix}", config[f"tier.standard.{suffix}"])
    if provider == "bedrock":
        return provider, model, config.get("bedrock.region", "us-west-2")
    if provider == "anthropic":
        return provider, model, config.get("anthropic.url",
                                           _DEFAULTS.get("anthropic.url", "https://api.anthropic.com"))
    if _is_openai_compat(provider):
        url_key, _, _, default_url, _ = _OPENAI_COMPAT_PROVIDERS[provider]
        return provider, model, config.get(url_key, default_url)
    url_key, _, _, default_url = _ollama_like_keys(provider)
    return provider, model, config.get(url_key, default_url)


def invoke_llm(
    tier: str,
    prompt: str,
    max_tokens: int = 1000,
    *,
    doc_type: str | None = None,
    project_slug: str = "",
) -> str:
    """Invoke LLM for the given tier. If doc_type is provided, tier is derived from it."""
    if doc_type:
        tier = _tier_for_doc_type(doc_type)
    config = get_config()
    text = _invoke_text(config, tier, prompt, max_tokens, project_slug=project_slug)
    return strip_thinking(text)


def _invoke_text(config: dict, tier: str, prompt: str, max_tokens: int, *,
                 project_slug: str = "") -> str:
    """Provider dispatch for simple text-in/text-out invocation."""
    provider = config["provider"]
    if provider == "bedrock":
        return _invoke_bedrock(config, tier, prompt, max_tokens, project_slug=project_slug)
    if provider == "anthropic":
        return _invoke_anthropic_direct(config, tier, prompt, max_tokens,
                                        project_slug=project_slug)
    if _is_openai_compat(provider):
        return _invoke_openai_compat_like(config, tier, prompt, max_tokens,
                                          project_slug=project_slug)
    return _invoke_ollama_like(config, tier, prompt, max_tokens, project_slug=project_slug)


def invoke_llm_with_meta(
    tier: str,
    prompt: str,
    max_tokens: int = 1000,
    *,
    doc_type: str | None = None,
    project_slug: str = "",
    enable_thinking: bool = False,
    num_ctx: int | None = None,
) -> dict:
    """Like invoke_llm but returns metadata for UX (model, provider, truncation).

    Returns {"text": str, "model_id": str, "provider": str, "truncated": bool}.
    Thinking is disabled by default — some models spend their entire token
    budget on chain-of-thought reasoning, leaving no room for the actual answer.
    ``num_ctx`` caps the context window on Ollama-like providers (KV-cache
    VRAM control for small-context workloads); ignored elsewhere.
    """
    if doc_type:
        tier = _tier_for_doc_type(doc_type)
    config = get_config()
    provider = config["provider"]
    suffix = _model_suffix(provider)
    model_id = config.get(f"tier.{tier}.{suffix}", config[f"tier.standard.{suffix}"])

    if provider == "bedrock":
        region = config.get("bedrock.region", "us-west-2")
        text, tok_in, tok_out = invoke_bedrock(model_id, prompt, max_tokens, region)
    elif provider == "anthropic":
        text, tok_in, tok_out = invoke_anthropic(
            model_id, prompt, max_tokens,
            api_key=_resolve_api_key(config, "anthropic", "ANTHROPIC_API_KEY"),
            base_url=config.get("anthropic.url", ""))
    elif _is_openai_compat(provider):
        url_key, _, api_key_env, default_url, mt_field = _OPENAI_COMPAT_PROVIDERS[provider]
        text, tok_in, tok_out = invoke_openai_compat(
            model_id, prompt, max_tokens,
            base_url=config.get(url_key, default_url),
            api_key=_resolve_api_key(config, provider, api_key_env),
            max_tokens_field=mt_field)
    else:
        url_key, _, api_key_env, default_url = _ollama_like_keys(provider)
        base_url = config.get(url_key, default_url)
        api_key = _resolve_api_key(config, provider, api_key_env)
        text, tok_in, tok_out = invoke_ollama(model_id, prompt, max_tokens, base_url,
                                              enable_thinking=enable_thinking,
                                              num_ctx=num_ctx,
                                              api_key=api_key)

    _log_usage(project_slug, model_id, tier, tok_in, tok_out, provider=provider)
    truncated = tok_out >= int(max_tokens * 0.95)
    text = strip_thinking(text)
    return {
        "text": text,
        "model_id": model_id,
        "provider": provider,
        "truncated": truncated,
        "input_tokens": tok_in,
        "output_tokens": tok_out,
    }


def converse_llm(
    tier: str,
    messages: list[dict],
    *,
    system: str = "",
    max_tokens: int = 4096,
    tools: list[dict] | None = None,
    doc_type: str | None = None,
    project_slug: str = "",
) -> dict:
    """Multi-turn + tool-use invocation via Bedrock Converse API.

    Same tier resolution as invoke_llm. Returns structured response with
    content blocks, stop_reason, and usage.
    """
    if doc_type:
        tier = _tier_for_doc_type(doc_type)
    config = get_config()
    provider = config["provider"]
    if provider == "bedrock":
        return _converse_bedrock(config, tier, messages, system=system, max_tokens=max_tokens,
                                tools=tools, project_slug=project_slug)
    if tools:
        log.warning("%s provider does not support tool use here — tools will be ignored",
                    provider)
    return _converse_text_fallback(config, tier, messages, system, max_tokens, project_slug)


def _converse_bedrock(
    config: dict, tier: str, messages: list[dict], *,
    system: str = "", max_tokens: int = 4096,
    tools: list[dict] | None = None, project_slug: str = "",
) -> dict:
    region = config.get("bedrock.region", "us-west-2")
    model_id = config.get(f"tier.{tier}.bedrock_model", config["tier.standard.bedrock_model"])
    result = converse_bedrock(model_id, messages, system=system, max_tokens=max_tokens,
                              tools=tools, region=region)
    _log_usage(project_slug, model_id, tier,
               result["usage"]["input_tokens"], result["usage"]["output_tokens"],
               provider="bedrock")
    return result


def _converse_text_fallback(
    config: dict, tier: str, messages: list[dict],
    system: str, max_tokens: int, project_slug: str,
) -> dict:
    """Non-Bedrock providers don't speak Converse here — flatten messages to a
    single prompt and dispatch through the provider's plain text path."""
    parts = []
    if system:
        parts.append(f"[system]: {system}")
    for m in messages:
        role = m.get("role", "user")
        content_blocks = m.get("content", [])
        for block in content_blocks:
            if "text" in block:
                parts.append(f"[{role}]: {block['text']}" if role != "user" else block["text"])
    prompt = "\n\n".join(parts)
    text = _invoke_text(config, tier, prompt, max_tokens, project_slug=project_slug)
    return {
        "content": [{"text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": len(prompt) // 4, "output_tokens": len(text) // 4},
    }


def get_embedding(text: str, *, project_slug: str = "",
                  provider: str | None = None) -> list[float]:
    """Get an embedding vector for the given text.

    ``provider`` overrides the active chat provider — embeddings can run on a
    different backend (required when chatting via Anthropic, which has no
    embeddings API).
    """
    config = get_config()
    provider = provider or config["provider"]
    if provider == "bedrock":
        return _embed_bedrock(config, text, project_slug=project_slug)
    if provider == "anthropic":
        raise RuntimeError(
            "Anthropic has no embedding API — set a different embedding provider "
            "(e.g. rag.embedding_provider: bedrock | ollama | openai | gemini)"
        )
    if _is_openai_compat(provider):
        return _embed_openai_compat_like(config, text, provider=provider,
                                         project_slug=project_slug)
    return _embed_ollama_like(config, text, provider=provider,
                              project_slug=project_slug)


def get_embeddings_batch(texts: list[str], *, project_slug: str = "",
                         batch_size: int = 20,
                         provider: str | None = None) -> list[list[float]]:
    """Batch-embed multiple texts. Falls back to sequential for providers without batch support.

    Batches texts into groups of batch_size and processes each group.
    Currently all providers are called sequentially per-text (Bedrock Titan
    doesn't support multi-text in a single invoke), but this centralizes the
    loop and enables future provider-level batching.
    """
    config = get_config()
    provider = provider or config["provider"]
    if provider == "anthropic":
        raise RuntimeError(
            "Anthropic has no embedding API — set a different embedding provider "
            "(e.g. rag.embedding_provider: bedrock | ollama | openai | gemini)"
        )
    results: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]

        if provider != "bedrock":
            # OpenAI-shape /embeddings endpoints accept {"input": [...]} for batch.
            if _is_openai_compat(provider):
                url_key, suffix, api_key_env, default_url, _ = _OPENAI_COMPAT_PROVIDERS[provider]
                embed_url = f"{config.get(url_key, default_url).rstrip('/')}/embeddings"
            else:
                url_key, suffix, api_key_env, default_url = _ollama_like_keys(provider)
                embed_url = f"{config.get(url_key, default_url)}/v1/embeddings"
            model = config.get(f"tier.embedding.{suffix}", _DEFAULTS.get(f"tier.embedding.{suffix}",
                _DEFAULTS["tier.embedding.ollama_model"]))
            try:
                import httpx
                headers = {}
                api_key = _resolve_api_key(config, provider, api_key_env)
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                payload = {"model": model, "input": batch}
                resp = httpx.post(embed_url, json=payload, timeout=120,
                                  headers=headers or None)
                resp.raise_for_status()
                data = resp.json()["data"]
                batch_vecs = [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
                results.extend(batch_vecs)
                tok_in = sum(len(t) // 4 for t in batch)
                _log_usage(project_slug, model, "embedding", tok_in, 0, provider=provider)
                continue
            except Exception as e:
                log.debug("%s batch embedding failed, falling back to sequential: %s", provider, e)

        # Sequential fallback (Bedrock or batch failure)
        for text in batch:
            vec = get_embedding(text, project_slug=project_slug, provider=provider)
            results.append(vec)

    return results


# ── Usage logging (fire-and-forget, pluggable) ───────────────────────────────
#
# DocHub wrote usage rows to an llm_usage_log table; Discovery tracks costs
# in its own llm_costs ledger. Rather than hard-wire either, the host app
# registers a callback. Default: debug log only.

UsageLogger = Callable[..., None]

_usage_logger: UsageLogger | None = None


def set_usage_logger(fn: UsageLogger | None) -> None:
    """Register a usage callback, called per LLM invocation with keyword args:
    project_slug, model_id, tier, tokens_in, tokens_out, provider.
    Pass ``None`` to reset to the default (debug log only). Exceptions raised
    by the callback are swallowed — usage accounting must never break a call.
    """
    global _usage_logger
    _usage_logger = fn


def _log_usage(project_slug: str, model_id: str, tier: str, tok_in: int, tok_out: int,
               *, provider: str = "bedrock") -> None:
    # Coerce to safe types — some providers return numpy ints or None
    tok_in = int(tok_in or 0)
    tok_out = int(tok_out or 0)
    if _usage_logger is None:
        log.debug("LLM usage: provider=%s model=%s tier=%s in=%d out=%d",
                  provider, model_id, tier, tok_in, tok_out)
        return
    try:
        _usage_logger(project_slug=project_slug, model_id=model_id, tier=tier,
                      tokens_in=tok_in, tokens_out=tok_out, provider=provider)
    except Exception:
        log.debug("LLM usage logger raised; usage entry skipped", exc_info=True)


# ── Bedrock (config-aware private wrappers) ───────────────────────────────────

def _invoke_bedrock(config: dict, tier: str, prompt: str, max_tokens: int, *, project_slug: str = "") -> str:
    region = config.get("bedrock.region", "us-west-2")
    model_id = config.get(f"tier.{tier}.bedrock_model", config["tier.standard.bedrock_model"])
    text, tok_in, tok_out = invoke_bedrock(model_id, prompt, max_tokens, region)
    _log_usage(project_slug, model_id, tier, tok_in, tok_out, provider="bedrock")
    return text


def _embed_bedrock(config: dict, text: str, *, project_slug: str = "") -> list[float]:
    region = config.get("bedrock.region", "us-west-2")
    model_id = config.get("tier.embedding.bedrock_model", _DEFAULTS["tier.embedding.bedrock_model"])
    result = embed_bedrock(model_id, text, region)
    tok_in = len(text) // 4
    _log_usage(project_slug, model_id, "embedding", tok_in, 0, provider="bedrock")
    return result


# ── Ollama-compatible providers (config-aware private wrappers) ──────────────
# Handles native Ollama + ai-mlx-server variants. Picks the right URL, model
# suffix, and optional Bearer auth based on the active provider.

def _invoke_ollama_like(config: dict, tier: str, prompt: str, max_tokens: int, *,
                        project_slug: str = "", enable_thinking: bool = False) -> str:
    provider = config["provider"]
    url_key, suffix, api_key_env, default_url = _ollama_like_keys(provider)
    base_url = config.get(url_key, default_url)
    model = config.get(f"tier.{tier}.{suffix}", config[f"tier.standard.{suffix}"])
    api_key = _resolve_api_key(config, provider, api_key_env)
    text, tok_in, tok_out = invoke_ollama(model, prompt, max_tokens, base_url,
                                          enable_thinking=enable_thinking,
                                          api_key=api_key)
    _log_usage(project_slug, model_id=model, tier=tier, tok_in=tok_in, tok_out=tok_out,
               provider=provider)
    return text


# ── OpenAI-compatible cloud + Anthropic (config-aware private wrappers) ──────


def _invoke_openai_compat_like(config: dict, tier: str, prompt: str, max_tokens: int, *,
                               project_slug: str = "") -> str:
    provider = config["provider"]
    url_key, suffix, api_key_env, default_url, mt_field = _OPENAI_COMPAT_PROVIDERS[provider]
    model = config.get(f"tier.{tier}.{suffix}", config[f"tier.standard.{suffix}"])
    text, tok_in, tok_out = invoke_openai_compat(
        model, prompt, max_tokens,
        base_url=config.get(url_key, default_url),
        api_key=_resolve_api_key(config, provider, api_key_env),
        max_tokens_field=mt_field)
    _log_usage(project_slug, model_id=model, tier=tier, tok_in=tok_in, tok_out=tok_out,
               provider=provider)
    return text


def _embed_openai_compat_like(config: dict, text: str, *, provider: str | None = None,
                              project_slug: str = "") -> list[float]:
    provider = provider or config["provider"]
    url_key, suffix, api_key_env, default_url, _ = _OPENAI_COMPAT_PROVIDERS[provider]
    model = config.get(f"tier.embedding.{suffix}", _DEFAULTS[f"tier.embedding.{suffix}"])
    result = embed_openai_compat(
        model, text,
        base_url=config.get(url_key, default_url),
        api_key=_resolve_api_key(config, provider, api_key_env))
    _log_usage(project_slug, model_id=model, tier="embedding", tok_in=len(text) // 4,
               tok_out=0, provider=provider)
    return result


def _invoke_anthropic_direct(config: dict, tier: str, prompt: str, max_tokens: int, *,
                             project_slug: str = "") -> str:
    model = config.get(f"tier.{tier}.anthropic_model", config["tier.standard.anthropic_model"])
    text, tok_in, tok_out = invoke_anthropic(
        model, prompt, max_tokens,
        api_key=_resolve_api_key(config, "anthropic", "ANTHROPIC_API_KEY"),
        base_url=config.get("anthropic.url", ""))
    _log_usage(project_slug, model_id=model, tier=tier, tok_in=tok_in, tok_out=tok_out,
               provider="anthropic")
    return text


def _embed_ollama_like(config: dict, text: str, *, provider: str | None = None,
                       project_slug: str = "") -> list[float]:
    provider = provider or config["provider"]
    url_key, suffix, api_key_env, default_url = _ollama_like_keys(provider)
    base_url = config.get(url_key, default_url)
    model = config.get(f"tier.embedding.{suffix}",
                       _DEFAULTS.get(f"tier.embedding.{suffix}",
                                     _DEFAULTS["tier.embedding.ollama_model"]))
    api_key = _resolve_api_key(config, provider, api_key_env)
    result = embed_ollama(model, text, base_url, api_key=api_key)
    tok_in = len(text) // 4
    _log_usage(project_slug, model_id=model, tier="embedding", tok_in=tok_in, tok_out=0,
               provider=provider)
    return result
