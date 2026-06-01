"""Low-level LLM invocation — no DB or config dependencies.

Provides raw Bedrock and Ollama invoke/embed functions used by both
the DocHub llm_router (config-aware) and the Discovery CLI (standalone).

Two Bedrock APIs are supported:
- invoke_bedrock()   — Messages API (text-in text-out, simple prompts)
- converse_bedrock() — Converse API (multi-turn, tool use, structured I/O)

For local Apple Silicon inference, Ollama uses the MLX backend automatically.
For advanced use (LoRA, custom embeddings), see ai-mlx-server which exposes
the same OpenAI-compatible API — just point OLLAMA_URL at it.
"""

from __future__ import annotations

import json
import logging
import random
import time

import httpx

_log = logging.getLogger(__name__)

# ── Retry config for transient Bedrock errors ──
_MAX_RETRIES = 3
_BASE_DELAY = 0.5  # seconds; exponential backoff: 0.5, 1.0, 2.0 + jitter

# Error codes / substrings considered transient (safe to retry)
_TRANSIENT_PATTERNS = ("ThrottlingException", "TooManyRequestsException",
                       "ServiceUnavailableException", "ReadTimeoutError",
                       "ConnectTimeoutError", "RequestTimeout")


def _is_transient(error: Exception) -> bool:
    """Return True if the error is transient and worth retrying."""
    err_str = str(error)
    return any(p in err_str for p in _TRANSIENT_PATTERNS)


def _log_llm_error(provider: str, model_id: str, error: str) -> None:
    """Best-effort: log LLM failure to activity_log for health tracking."""
    try:
        from ai_discovery.shared.log_writer import log_activity
        log_activity(
            None, None, "llm_error",
            f"{provider} invocation failed ({model_id}): {error[:200]}",
            username="system",
            metadata={"provider": provider, "model": model_id},
        )
    except Exception:
        pass  # Best-effort only


def invoke_bedrock(
    model_id: str, prompt: str, max_tokens: int = 4096,
    region: str = "us-west-2",
) -> tuple[str, int, int]:
    """Invoke a Bedrock model directly. Returns (text, tokens_in, tokens_out)."""
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime", region_name=region,
        config=Config(read_timeout=300, connect_timeout=10),
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    })
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = client.invoke_model(modelId=model_id, body=body)
            result = json.loads(response["body"].read())
            text = result["content"][0]["text"]
            tok_in = result.get("usage", {}).get("input_tokens", 0)
            tok_out = result.get("usage", {}).get("output_tokens", 0)
            return text, tok_in, tok_out
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES and _is_transient(e):
                delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.3)
                _log.warning("Bedrock invoke attempt %d/%d failed: %s, retrying in %.1fs",
                             attempt + 1, _MAX_RETRIES, e, delay)
                time.sleep(delay)
                continue
            _log_llm_error("bedrock", model_id, str(e))
            raise RuntimeError(f"Bedrock invocation failed (model={model_id}): {e}") from e
    # Should not reach here, but safety net
    _log_llm_error("bedrock", model_id, str(last_err))
    raise RuntimeError(f"Bedrock invocation failed after {_MAX_RETRIES} retries (model={model_id}): {last_err}") from last_err


def converse_bedrock(
    model_id: str,
    messages: list[dict],
    *,
    system: str = "",
    max_tokens: int = 4096,
    tools: list[dict] | None = None,
    tool_choice: dict | None = None,
    region: str = "us-west-2",
) -> dict:
    """Invoke Bedrock Converse API — supports multi-turn and tool use.

    Args:
        model_id: Bedrock model ID (e.g. "us.anthropic.claude-sonnet-4-20250514-v1:0")
        messages: Converse-format messages, each with role + content blocks.
                  Content blocks: [{"text": "..."}, {"toolUse": {...}}, {"toolResult": {...}}]
        system: Optional system prompt.
        max_tokens: Max output tokens.
        tools: Optional list of toolSpec definitions for tool use.
        tool_choice: Optional Converse toolChoice, e.g. {"tool": {"name": "..."}}
                     to force the model to emit that tool's input (schema-valid
                     JSON), {"any": {}}, or {"auto": {}}. Ignored unless tools set.
        region: AWS region.

    Returns:
        {
            "content": [{"text": "..."}, {"toolUse": {"toolUseId", "name", "input"}}],
            "stop_reason": "end_turn" | "tool_use" | "max_tokens",
            "usage": {"input_tokens": N, "output_tokens": N},
        }
    """
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime", region_name=region,
        config=Config(read_timeout=300, connect_timeout=10),
    )

    kwargs: dict = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    if tools:
        tool_config: dict = {"tools": tools}
        if tool_choice:
            tool_config["toolChoice"] = tool_choice
        kwargs["toolConfig"] = tool_config

    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = client.converse(**kwargs)
            output = response.get("output", {}).get("message", {})
            return {
                "content": output.get("content", []),
                "stop_reason": response.get("stopReason", "end_turn"),
                "usage": {
                    "input_tokens": response.get("usage", {}).get("inputTokens", 0),
                    "output_tokens": response.get("usage", {}).get("outputTokens", 0),
                },
            }
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES and _is_transient(e):
                delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.3)
                _log.warning("Bedrock converse attempt %d/%d failed: %s, retrying in %.1fs",
                             attempt + 1, _MAX_RETRIES, e, delay)
                time.sleep(delay)
                continue
            _log_llm_error("bedrock", model_id, str(e))
            raise RuntimeError(f"Bedrock converse failed (model={model_id}): {e}") from e
    _log_llm_error("bedrock", model_id, str(last_err))
    raise RuntimeError(f"Bedrock converse failed after {_MAX_RETRIES} retries (model={model_id}): {last_err}") from last_err


def invoke_ollama(
    model: str, prompt: str, max_tokens: int = 4096,
    base_url: str = "http://localhost:11434",
    enable_thinking: bool = True,
    num_ctx: int | None = None,
    api_key: str = "",
) -> tuple[str, int, int]:
    """Invoke an Ollama model directly. Returns (text, tokens_in, tokens_out).

    Per-request timeout defaults to 600s (10 min) to accommodate large local
    models (e.g. gemma4:31b generating 4k tokens on Apple Silicon). Override
    with env var OLLAMA_INVOKE_TIMEOUT (seconds).

    If ``num_ctx`` is set, the request caps the model's context window at that
    token count. This dramatically reduces KV-cache VRAM for small-context
    tasks (e.g. Discovery tier1 summarizes chunks ≤2K tokens — forcing
    num_ctx=4096 cuts memory vs. the model's default 32K).

    Routing: when ``num_ctx`` is provided we use Ollama's native ``/api/chat``
    endpoint because the OpenAI-compatible ``/v1/chat/completions`` endpoint
    silently drops ``options.num_ctx`` (same class of issue as ``keep_alive``
    — see the warm_ollama comment below). Without num_ctx we stay on the
    OpenAI path to preserve existing behavior for all other callers."""
    try:
        import os
        import re
        timeout = float(os.environ.get("OLLAMA_INVOKE_TIMEOUT", "600"))

        # Build the options dict once — both endpoints accept it under the
        # same key, we just route it differently.
        options: dict = {}
        if not enable_thinking and "qwen" in model.lower():
            options["think"] = False
        if num_ctx is not None:
            options["num_ctx"] = int(num_ctx)

        # Optional Bearer auth — used by ai-mlx-server when MLX_API_KEY is set.
        # Native Ollama ignores the header, so it's safe to pass unconditionally.
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None

        if num_ctx is not None:
            # Native /api/chat path: reliably honors options.num_ctx.
            # Response shape: {"message": {"content": "..."},
            #                  "prompt_eval_count": N, "eval_count": N}
            payload: dict = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": max_tokens, **options},
            }
            resp = httpx.post(f"{base_url}/api/chat", json=payload, timeout=timeout, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("message", {}).get("content", "")
            tok_in = int(data.get("prompt_eval_count", len(prompt) // 4))
            tok_out = int(data.get("eval_count", len(raw) // 4))
        else:
            # OpenAI-compatible path (default — unchanged behavior).
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "max_tokens": max_tokens,
            }
            if options:
                payload["options"] = options
            resp = httpx.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
            tok_in = data.get("usage", {}).get("prompt_tokens", len(prompt) // 4)
            tok_out = data.get("usage", {}).get("completion_tokens", len(raw) // 4)

        # Strip <think>…</think> reasoning blocks (Qwen3.5 etc.)
        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
        text = text.strip()
        return text, tok_in, tok_out
    except Exception as e:
        _log_llm_error("ollama", model, str(e))
        raise RuntimeError(f"Ollama invocation failed (model={model} at {base_url}): {e}") from e


def embed_bedrock(
    model_id: str, text: str, region: str = "us-west-2",
) -> list[float]:
    """Get embedding vector via Bedrock. Returns list of floats."""
    import boto3

    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        body = json.dumps({"inputText": text})
        response = client.invoke_model(
            modelId=model_id, body=body,
            accept="application/json", contentType="application/json",
        )
        return json.loads(response["body"].read())["embedding"]
    except Exception as e:
        raise RuntimeError(f"Bedrock embedding failed (model={model_id}): {e}") from e


def embed_ollama(
    model: str, text: str, base_url: str = "http://localhost:11434",
    api_key: str = "",
) -> list[float]:
    """Get embedding vector via Ollama. Returns list of floats.

    Timeout defaults to 120s (to tolerate cold-load of the embedding model).
    Override with env var OLLAMA_EMBED_TIMEOUT (seconds).

    Optional ``api_key`` adds an ``Authorization: Bearer …`` header — used by
    ai-mlx-server when MLX_API_KEY is set. Native Ollama ignores it."""
    try:
        import os
        timeout = float(os.environ.get("OLLAMA_EMBED_TIMEOUT", "120"))
        payload = {"model": model, "input": text}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        resp = httpx.post(f"{base_url}/v1/embeddings", json=payload, timeout=timeout, headers=headers)
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception as e:
        raise RuntimeError(f"Ollama embedding failed (model={model}): {e}") from e


def embed_ollama_batch(
    model: str, texts: list[str], base_url: str = "http://localhost:11434",
    api_key: str = "",
) -> list[list[float]]:
    """Batch variant of :func:`embed_ollama`. Ollama's OpenAI-compatible
    /v1/embeddings endpoint accepts an array under ``input`` and returns one
    embedding per element, ordered. Cuts per-request overhead ~10–30× on
    large chunk sets.

    Returns embeddings in the same order as *texts*. Raises on any failure —
    caller decides whether to fall back to per-text calls."""
    if not texts:
        return []
    try:
        import os
        timeout = float(os.environ.get("OLLAMA_EMBED_TIMEOUT", "120"))
        # Scale timeout by batch size — large batches take longer end-to-end.
        timeout = max(timeout, 30 + 0.5 * len(texts))
        payload = {"model": model, "input": texts}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        resp = httpx.post(f"{base_url}/v1/embeddings", json=payload, timeout=timeout, headers=headers)
        resp.raise_for_status()
        data = resp.json()["data"]
        # Ollama preserves input order and returns ``index`` — use it defensively
        # in case a future version ever reorders.
        sorted_data = sorted(data, key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in sorted_data]
    except Exception as e:
        raise RuntimeError(f"Ollama batch embedding failed (model={model}, n={len(texts)}): {e}") from e


# ── Ollama keep-alive helpers ──────────────────────────────────────────────
# The OpenAI-compat endpoints (/v1/chat/completions, /v1/embeddings) silently
# ignore Ollama's `keep_alive` parameter. To pin or evict a model we have to
# hit the native /api/generate endpoint with an empty prompt.

def warm_ollama(model: str, base_url: str, keep_alive: str = "30m",
                api_key: str = "") -> bool:
    """Load `model` into Ollama and pin it in memory for `keep_alive`.
    Returns True on success, False on failure (non-fatal — caller can proceed)."""
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        resp = httpx.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": keep_alive, "stream": False},
            timeout=300,  # cold-loading a 31b model can take a while
            headers=headers,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        _log_llm_error("ollama", model, f"warm failed: {e}")
        return False


def unload_ollama(model: str, base_url: str) -> bool:
    """Evict `model` from Ollama memory immediately (keep_alive=0).
    Returns True on success, False on failure (non-fatal)."""
    try:
        resp = httpx.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": 0, "stream": False},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        _log_llm_error("ollama", model, f"unload failed: {e}")
        return False
