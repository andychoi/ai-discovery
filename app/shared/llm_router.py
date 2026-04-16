"""LLM Router — provider/model abstraction for DocHub handlers.

Resolution order: llm_config DB → env var → hardcoded default.
Supports AWS Bedrock and Ollama (OpenAI-compatible API).

For local Apple Silicon inference, Ollama uses the MLX backend automatically.
For advanced use (LoRA, custom embeddings), point OLLAMA_URL at ai-mlx-server.
"""
import logging
import os
import re
from datetime import date

from app.shared.sdlc_db import get_read_conn, return_read_conn, now_iso

from app.shared.model_defaults import MODELS, DEFAULT_PROVIDER
from app.shared.llm_invoke import (  # re-exported
    invoke_bedrock, invoke_ollama,
    embed_bedrock, embed_ollama,
    converse_bedrock,
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


# ── Defaults derived from app.shared model_defaults ──────────────────────────────


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


def get_config() -> dict[str, str]:
    """Return resolved config: DB > env > default."""
    config = dict(_DEFAULTS)
    # Apply env overrides
    for key, env_var in _ENV_MAP.items():
        val = os.getenv(env_var)
        if val:
            config[key] = val
    # Apply DB overrides (highest priority)
    conn = get_read_conn()
    try:
        rows = conn.execute("SELECT key, value FROM llm_config").fetchall()
        for row in rows:
            config[row["key"]] = row["value"]
    except Exception as e:
        log.warning("Could not read llm_config from DB: %s", e)
    finally:
        return_read_conn(conn)
    return config


def reset_config(key: str | None = None) -> int:
    """Delete DB overrides so env/defaults take effect. Returns row count.

    With ``key=None`` wipes every row in ``llm_config``. With a specific key,
    wipes just that row. Unknown keys are a no-op (return 0) — the UI passes
    whatever the user clicks, so raising here would just surface spurious
    errors after a schema change.
    """
    from app.shared.db.connection import execute_write
    count = {"n": 0}
    def _do(conn):
        if key is None:
            cur = conn.execute("DELETE FROM llm_config")
        else:
            cur = conn.execute("DELETE FROM llm_config WHERE key = %s", (key,))
        count["n"] = cur.rowcount or 0
    execute_write(_do)
    return count["n"]


def save_config(updates: dict[str, str]) -> None:
    """Upsert config keys into the llm_config table."""
    _ALLOWED_KEYS = set(_DEFAULTS.keys())
    bad = set(updates) - _ALLOWED_KEYS
    if bad:
        raise ValueError(f"Unknown LLM config key(s): {sorted(bad)}")
    from app.shared.db.connection import execute_write
    ts = now_iso()
    def _do(conn):
        for key, value in updates.items():
            conn.execute(
                "INSERT INTO llm_config (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, ts),
            )
    execute_write(_do)


def _tier_for_doc_type(doc_type: str) -> str:
    return DOC_TYPE_TIER.get(doc_type, "standard")


def resolve_model_id(tier: str, *, doc_type: str | None = None) -> str:
    """Return the actual model ID that would be used for the given tier/doc_type."""
    if doc_type:
        tier = _tier_for_doc_type(doc_type)
    config = get_config()
    provider = config["provider"]
    if provider == "bedrock":
        return config.get(f"tier.{tier}.bedrock_model", config["tier.standard.bedrock_model"])
    _, suffix, _, _ = _ollama_like_keys(provider)
    return config.get(f"tier.{tier}.{suffix}", config[f"tier.standard.{suffix}"])


def resolve_tier_target(tier: str) -> tuple[str, str, str]:
    """Return (provider, model_id, base_url_or_region) for a tier.

    For Bedrock the third element is the AWS region; for Ollama-like providers
    it is the base URL. Used by startup-time warming to look up the model and
    URL without duplicating config logic.
    """
    config = get_config()
    provider = config["provider"]
    if provider == "bedrock":
        model = config.get(f"tier.{tier}.bedrock_model", config["tier.standard.bedrock_model"])
        return provider, model, config.get("bedrock.region", "us-west-2")
    url_key, suffix, _, default_url = _ollama_like_keys(provider)
    model = config.get(f"tier.{tier}.{suffix}", config[f"tier.standard.{suffix}"])
    base_url = config.get(url_key, default_url)
    return provider, model, base_url


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
    provider = config["provider"]
    if provider == "bedrock":
        text = _invoke_bedrock(config, tier, prompt, max_tokens, project_slug=project_slug)
    else:
        text = _invoke_ollama_like(config, tier, prompt, max_tokens, project_slug=project_slug)
    return strip_thinking(text)


def invoke_llm_with_meta(
    tier: str,
    prompt: str,
    max_tokens: int = 1000,
    *,
    doc_type: str | None = None,
    project_slug: str = "",
    enable_thinking: bool = False,
) -> dict:
    """Like invoke_llm but returns metadata for UX (model, provider, truncation).

    Returns {"text": str, "model_id": str, "provider": str, "truncated": bool}.
    Thinking is disabled by default — some models spend their entire token
    budget on chain-of-thought reasoning, leaving no room for the actual answer.
    """
    if doc_type:
        tier = _tier_for_doc_type(doc_type)
    config = get_config()
    provider = config["provider"]

    if provider == "bedrock":
        region = config.get("bedrock.region", "us-west-2")
        model_id = config.get(f"tier.{tier}.bedrock_model", config["tier.standard.bedrock_model"])
        text, tok_in, tok_out = invoke_bedrock(model_id, prompt, max_tokens, region)
    else:
        url_key, suffix, api_key_env, default_url = _ollama_like_keys(provider)
        model_id = config.get(f"tier.{tier}.{suffix}", config[f"tier.standard.{suffix}"])
        base_url = config.get(url_key, default_url)
        api_key = os.getenv(api_key_env) if api_key_env else ""
        text, tok_in, tok_out = invoke_ollama(model_id, prompt, max_tokens, base_url,
                                              enable_thinking=enable_thinking,
                                              api_key=api_key or "")

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
        log.warning("%s provider does not support tool use — tools will be ignored", provider)
    return _converse_ollama_fallback(config, tier, messages, system, max_tokens, project_slug)


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


def _converse_ollama_fallback(
    config: dict, tier: str, messages: list[dict],
    system: str, max_tokens: int, project_slug: str,
) -> dict:
    """Ollama doesn't support Converse — flatten messages to text and use chat API."""
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
    text = _invoke_ollama_like(config, tier, prompt, max_tokens, project_slug=project_slug)
    return {
        "content": [{"text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": len(prompt) // 4, "output_tokens": len(text) // 4},
    }


def get_embedding(text: str, *, project_slug: str = "") -> list[float]:
    """Get an embedding vector for the given text."""
    config = get_config()
    provider = config["provider"]
    if provider == "bedrock":
        return _embed_bedrock(config, text, project_slug=project_slug)
    return _embed_ollama_like(config, text, project_slug=project_slug)


def get_embeddings_batch(texts: list[str], *, project_slug: str = "",
                         batch_size: int = 20) -> list[list[float]]:
    """Batch-embed multiple texts. Falls back to sequential for providers without batch support.

    Batches texts into groups of batch_size and processes each group.
    Currently all providers are called sequentially per-text (Bedrock Titan
    doesn't support multi-text in a single invoke), but this centralizes the
    loop and enables future provider-level batching.
    """
    config = get_config()
    provider = config["provider"]
    results: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]

        if provider != "bedrock":
            # Ollama-compatible /v1/embeddings supports {"input": [...]} for batch
            url_key, suffix, api_key_env, default_url = _ollama_like_keys(provider)
            base_url = config.get(url_key, default_url)
            model = config.get(f"tier.embedding.{suffix}", _DEFAULTS.get(f"tier.embedding.{suffix}",
                _DEFAULTS["tier.embedding.ollama_model"]))
            try:
                import httpx
                headers = {}
                api_key = os.getenv(api_key_env) if api_key_env else ""
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                payload = {"model": model, "input": batch}
                resp = httpx.post(f"{base_url}/v1/embeddings", json=payload, timeout=120,
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

        # Sequential fallback (Bedrock or Ollama batch failure)
        for text in batch:
            vec = get_embedding(text, project_slug=project_slug)
            results.append(vec)

    return results


# ── Usage logging (fire-and-forget) ──────────────────────────────────────────

def _log_usage(project_slug: str, model_id: str, tier: str, tok_in: int, tok_out: int,
               *, provider: str = "bedrock") -> None:
    today = date.today().isoformat()
    # Coerce to safe types — some providers return numpy ints or None
    tok_in = int(tok_in or 0)
    tok_out = int(tok_out or 0)
    try:
        from app.shared.db.connection import execute_log_write
        execute_log_write(lambda conn: conn.execute(
            """-- pg-native
            INSERT INTO llm_usage_log
                (project_slug, date, model_id, tier, provider, invocations, input_tokens, output_tokens)
            VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
            ON CONFLICT(project_slug, date, model_id, tier, provider) DO UPDATE SET
                invocations   = llm_usage_log.invocations + 1,
                input_tokens  = llm_usage_log.input_tokens + excluded.input_tokens,
                output_tokens = llm_usage_log.output_tokens + excluded.output_tokens
            """,
            (project_slug, today, model_id, tier, provider, tok_in, tok_out),
        ))
    except Exception:
        log.debug("LLM usage log skipped (pool unavailable)")


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
    api_key = os.getenv(api_key_env) if api_key_env else ""
    text, tok_in, tok_out = invoke_ollama(model, prompt, max_tokens, base_url,
                                          enable_thinking=enable_thinking,
                                          api_key=api_key or "")
    _log_usage(project_slug, model_id=model, tier=tier, tok_in=tok_in, tok_out=tok_out,
               provider=provider)
    return text


def _embed_ollama_like(config: dict, text: str, *, project_slug: str = "") -> list[float]:
    provider = config["provider"]
    url_key, suffix, api_key_env, default_url = _ollama_like_keys(provider)
    base_url = config.get(url_key, default_url)
    model = config.get(f"tier.embedding.{suffix}",
                       _DEFAULTS.get(f"tier.embedding.{suffix}",
                                     _DEFAULTS["tier.embedding.ollama_model"]))
    api_key = os.getenv(api_key_env) if api_key_env else ""
    result = embed_ollama(model, text, base_url, api_key=api_key or "")
    tok_in = len(text) // 4
    _log_usage(project_slug, model_id=model, tier="embedding", tok_in=tok_in, tok_out=0,
               provider=provider)
    return result
