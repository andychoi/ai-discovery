"""Single source of truth for LLM model IDs across Discovery CLI + DocHub.

Both ``app/config.py`` and ``app/shared/llm_router.py`` import from
here so model defaults are defined exactly once.

Tier names:
  fast      – cheapest / fastest        (Discovery: tier1)
  standard  – balanced                  (Discovery: tier2)
  expert    – high-reasoning quality    (Discovery: tier3p in prod, tier3d in dev)
  heavy     – largest / longest-context (batch synthesis, gap analysis)
  embedding – vector embeddings

Note: ``deep`` is retained as a backward-compatible alias for ``expert`` so legacy
handler call sites like ``invoke_llm("deep", ...)`` keep working. New code should
use ``expert`` or ``heavy`` explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path


def _default_ollama_url() -> str:
    """Pick a sensible Ollama URL default based on where we're running.

    Ollama almost always runs on the host (no compose service for it). When
    DocHub itself runs inside a container, ``localhost`` points at the container
    — we need ``host.docker.internal`` (Docker Desktop) or a Linux extra_hosts
    alias to reach the host. When DocHub runs natively (``./scripts/dev.sh``),
    ``localhost`` is correct.
    """
    in_container = Path("/.dockerenv").exists() or os.environ.get("IN_DOCKER") == "1"
    return "http://host.docker.internal:11434" if in_container else "http://localhost:11434"


MODELS: dict[str, dict[str, str]] = {
    "bedrock": {
        "region":    "us-west-2",
        "embedding": "amazon.titan-embed-text-v2:0",
        # Claude 5.5 family on bedrock-runtime (InvokeModel + Converse) through the
        # Global cross-region inference profile. `global.anthropic.claude-haiku-5-5`
        # is the id in AWS's launch post ("Introducing Claude Haiku 5.5 on AWS",
        # 2026); the Sonnet/Opus ids follow the same pattern and are UNVERIFIED —
        # check with `discover doctor --llm`, or override bedrock.standard/deep
        # (us./eu./au./jp. geo profiles also exist; exact ids not published there).
        "fast":      "global.anthropic.claude-haiku-5-5",
        "standard":  "global.anthropic.claude-sonnet-5-5",
        "expert":    "global.anthropic.claude-opus-5-5",
        "heavy":     "global.anthropic.claude-opus-5-5",
    },
    "ollama": {
        "url":       _default_ollama_url(),
        # 1024-dim — matches the vector(1024) pgvector schema
        # (app/shared/db/pg_schema.sql:1045). The previous nomic-embed-text
        # default produces 768-dim vectors and silently broke every embed
        # write under Ollama mode.
        "embedding": "mxbai-embed-large",
        # Tier sizing rationale:
        #   fast      — bulk classify/summarize/JSON extraction; 2B effective is sufficient
        #   standard  — daily-driver chat & retrieval (Agent Engine Executor uses this)
        #   expert    — careful multi-step reasoning when standard misses
        #   heavy     — batch synthesis with very long contexts; rarely the right choice
        "fast":      "gemma4:e2b",
        "standard":  "gemma4:e4b",
        "expert":    "gemma4:26b",
        "heavy":     "gemma4:31b",
    },
    # ── ai-mlx-server (https://github.com/andychoi/ai-mlx-server) ─────────
    # MLX-native runtime for Apple Silicon. Speaks the Ollama protocol on
    # :11435 so callers can reuse invoke_ollama / embed_ollama with a
    # different base_url. Two provider keys point at the same server with
    # different model tag sets — select via LLM_PROVIDER=mlx-gemma or
    # LLM_PROVIDER=mlx-qwen. Models are pulled by the server itself from
    # the mlx-community Hugging Face org; ai-docs only references them by name.
    "mlx-gemma": {
        "url":       "http://localhost:11435",
        # 1024-dim — matches the existing pgvector(1024) schema, so no
        # re-embedding is required when switching providers (similarity
        # scores will shift in practice; re-embed for best Ask quality).
        "embedding": "mlx-community/snowflake-arctic-embed-l-v2.0-4bit",
        "fast":      "mlx-community/gemma-4-e2b-it-4bit",
        "standard":  "mlx-community/gemma-4-e4b-it-4bit",
        "expert":    "mlx-community/gemma-4-26b-a4b-it-4bit",
        "heavy":     "mlx-community/gemma-4-31b-it-4bit",
    },
    # ── OpenAI (api.openai.com, OpenAI-compatible chat completions) ───────
    # base_url includes the version prefix; invoke_openai_compat appends
    # /chat/completions. Auth via OPENAI_API_KEY. Note: the gpt-5 family
    # requires max_completion_tokens (not max_tokens) — handled by the
    # router's per-provider max_tokens_field.
    "openai": {
        "url":       "https://api.openai.com/v1",
        "embedding": "text-embedding-3-small",
        "fast":      "gpt-5-nano",
        "standard":  "gpt-5-mini",
        "expert":    "gpt-5.1",
        "heavy":     "gpt-5.1",
    },
    # ── Google Gemini (OpenAI-compatible endpoint) ────────────────────────
    # https://ai.google.dev/gemini-api/docs/openai — same chat-completions
    # shape as OpenAI, different base path. Auth via GEMINI_API_KEY.
    "gemini": {
        "url":       "https://generativelanguage.googleapis.com/v1beta/openai",
        "embedding": "gemini-embedding-001",
        "fast":      "gemini-2.5-flash-lite",
        "standard":  "gemini-3.5-flash",
        "expert":    "gemini-2.5-pro",
        "heavy":     "gemini-2.5-pro",
    },
    # ── Anthropic direct (api.anthropic.com via the anthropic SDK) ────────
    # Bare model aliases per Anthropic guidance — no date suffixes. Auth via
    # ANTHROPIC_API_KEY (resolved by the SDK). Anthropic has no embeddings
    # API, so no "embedding" key — pick a different rag.embedding_provider.
    "anthropic": {
        "url":      "https://api.anthropic.com",
        "fast":     "claude-haiku-5-5",
        "standard": "claude-sonnet-5-5",
        "expert":   "claude-opus-5-5",
        "heavy":    "claude-opus-5-5",
    },
    # ── ai-gateway (local, :11433) — OpenAI-compatible front for OMLX ─────
    # base_url includes /v1; invoke_openai_compat appends /chat/completions.
    # Model IDs are *gateway aliases* (see ai-gateway MODEL_GUIDE.md), not
    # upstream names — the virtual key's metadata.models allowlist must name
    # each alias explicitly. Auth via AIGW_API_KEY (Bearer virtual key).
    "aigw": {
        "url":       "http://localhost:11433/v1",
        "embedding": "pplx-embed",
        "fast":      "claude-haiku",
        "standard":  "claude-sonnet",
        "expert":    "claude-opus",
        "heavy":     "claude-opus",
    },
    # ── OMLX (local MLX host, :11434, OpenAI-compatible) ──────────────────
    # Replaces Ollama on this port. base_url includes /v1. Auth via
    # OMLX_API_KEY (Bearer). Model IDs as served by OMLX — the same names
    # ai-gateway's embedder/semantic cache use.
    "omlx": {
        "url":       "http://localhost:11434/v1",
        # 1024-dim — matches the vector(1024) schema.
        "embedding": "Qwen3-Embedding-0.6B-4bit-DWQ",
        "fast":      "Qwen3.5-9B-MTPLX-Optimized-Speed",
        "standard":  "Qwen3.5-9B-MTPLX-Optimized-Speed",
        "expert":    "Qwen3.5-9B-MTPLX-Optimized-Speed",
        "heavy":     "Qwen3.5-9B-MTPLX-Optimized-Speed",
    },
    # ── OpenRouter (OpenAI-compatible chat completions + embeddings) ──────
    # Auth via OPENROUTER_API_KEY. Model IDs are OpenRouter slugs
    # (vendor/model) — verify against https://openrouter.ai/models.
    "openrouter": {
        "url":       "https://openrouter.ai/api/v1",
        # 1024-dim. Alternatives (set rag.embedding_model), one id per line:
        #   openai/text-embedding-3-small
        #   liquid/lfm-2.5-embedding-350m:free
        "embedding": "perplexity/pplx-embed-v1-0.6b",
        "fast":      "anthropic/claude-haiku-5.5",     # slugs checked against
        "standard":  "anthropic/claude-sonnet-5.5",    # openrouter.ai/api/v1/models
        "expert":    "anthropic/claude-opus-5.5",      # (2026-10-13)
        "heavy":     "anthropic/claude-opus-5.5",
    },
    # ── ai-mlx-server (Qwen3.5 variant) ──────────────────────────────────
    # Same server (:11435), different tier → model map. Selectable as a
    # sibling of mlx-gemma for benchmarking vs. gemma4 or when Qwen's
    # tool-calling is preferred.
    "mlx-qwen": {
        "url":       "http://localhost:11435",
        # Reuse the snowflake embedding — 1024-dim, schema-compatible.
        # Qwen does not publish a dedicated mlx-community embedding model.
        "embedding": "mlx-community/snowflake-arctic-embed-l-v2.0-4bit",
        "edge":      "mlx-community/Qwen3.5-2B-MLX-4bit",
        "fast":      "mlx-community/Qwen3.5-4B-MLX-4bit",
        "standard":  "mlx-community/Qwen3.5-9B-MLX-4bit",
        "expert":    "mlx-community/Qwen3.5-27B-Claude-4.6-Opus-Distilled-MLX-4bit",
        # Claude-4.6-Opus distillation of Qwen3.5-27B — keeps the 27B backbone
        # but inherits Opus-style reasoning/tool-use traces. Heavier download
        # than vanilla 27B, same VRAM footprint at 4-bit.
        "heavy":     "mlx-community/Qwen3.5-27B-Claude-4.6-Opus-Distilled-MLX-4bit",
    },
}

# Legacy alias: "deep" → "expert". Kept so existing handlers and Discovery CLI
# that read MODELS[provider]["deep"] continue to resolve to a real model.
for _provider, _models in MODELS.items():
    if "expert" in _models:
        _models.setdefault("deep", _models["expert"])

# Ollama is not used locally: OMLX serves :11434 and ai-gateway (:11433) fronts it.
DEFAULT_PROVIDER = "aigw"


# ── Cost rates ($ per 1M tokens: input, output) ───────────────────────────────
# Estimates for cost reporting and budget guarding — matched by model-ID
# substring, most specific fragment first. Update alongside MODELS when
# provider pricing changes.

_LOCAL_PROVIDERS = frozenset({"ollama", "mlx-gemma", "mlx-qwen", "omlx"})

MODEL_RATES: tuple[tuple[str, float, float], ...] = (
    # OpenRouter free variants (vendor/model:free)
    (":free",  0.0, 0.0),
    # Anthropic Claude — matches direct aliases, Bedrock (anthropic.… / us.anthropic.…)
    # and OpenRouter (anthropic/claude-…-5.5) ids. 5.5 family first (first-party
    # rates, 2026-10): Opus $4/$20, Sonnet $2/$10, Haiku $0.10/$0.50 (prompts
    # ≤100K tokens; $0.50/$2.50 beyond — not modelled). Bedrock and OpenRouter
    # price separately; these first-party rates are used as the estimate there. Older generations: Opus 4.x $5/$25, Sonnet 4.6 $3/$15,
    # Haiku 4.5 $1/$5.
    ("opus-5-5",   4.0, 20.0),
    ("opus-5.5",   4.0, 20.0),
    ("sonnet-5-5", 2.0, 10.0),
    ("sonnet-5.5", 2.0, 10.0),
    ("haiku-5-5",  0.10, 0.50),
    ("haiku-5.5",  0.10, 0.50),
    ("opus",   5.0, 25.0),
    ("sonnet", 3.0, 15.0),
    ("haiku",  1.0, 5.0),
    # OpenAI gpt-5 family (specific variants before the family match).
    ("gpt-5-nano", 0.05, 0.40),
    ("gpt-5-mini", 0.25, 2.0),
    ("gpt-5",      1.25, 10.0),   # gpt-5 / gpt-5.1
    # Google Gemini (embedding and flash-lite before flash; bare "gemini"
    # catches the pro family). gemini-3.x flash rates approximated with the
    # 2.5-flash tier — verify against current pricing when it matters.
    ("gemini-embedding", 0.15, 0.0),
    ("pplx-embed",       0.004, 0.0),
    ("flash-lite", 0.10, 0.40),
    ("flash",      0.30, 2.50),
    ("gemini",     1.25, 10.0),
    # Embeddings (no output tokens)
    ("titan-embed",            0.02, 0.0),
    ("text-embedding-3-small", 0.02, 0.0),
    ("text-embedding-3-large", 0.13, 0.0),
)

_DEFAULT_CLOUD_RATE = (3.0, 15.0)  # unknown cloud model — Sonnet-class, conservative


def rates_for_model(model: str, provider: str | None = None) -> tuple[float, float]:
    """($ per 1M input tokens, $ per 1M output tokens) for *model*.

    Local providers are free regardless of the model name — checked before
    name matching so a local distill named after a Claude family (e.g.
    Qwen…-Claude-4.6-Opus-Distilled) is not billed. Unknown cloud models fall
    back to a conservative Sonnet-class default so budget guards stay safe
    rather than optimistic.
    """
    if provider in _LOCAL_PROVIDERS:
        return 0.0, 0.0
    needle = model.lower()
    for fragment, in_rate, out_rate in MODEL_RATES:
        if fragment in needle:
            return in_rate, out_rate
    return _DEFAULT_CLOUD_RATE
