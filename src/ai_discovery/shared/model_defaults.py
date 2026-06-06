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
        "fast":      "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "standard":  "us.anthropic.claude-sonnet-4-6",
        "expert":    "us.anthropic.claude-opus-4-6",
        "heavy":     "us.anthropic.claude-opus-4-6",
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
        "fast":     "claude-haiku-4-5",
        "standard": "claude-sonnet-4-6",
        "expert":   "claude-opus-4-8",
        "heavy":    "claude-opus-4-8",
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

DEFAULT_PROVIDER = "ollama"
