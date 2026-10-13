"""LLM settings: the resolved provider/model/endpoint key space, passed explicitly (M1.10.a).

Replaces the module-global ``llm_router.configure()`` / ``get_config()`` layer.
Resolution is unchanged: catalog defaults < environment variables <
``DiscoveryConfig`` (YAML/env already merged by the config loader). Build one
with ``settings_from_config(cfg)``; ``LLMClient`` owns it and hands it to the
invoke path, so two clients with different configs never interfere.

Keys are the router's flat space: ``provider``, ``<provider>.url``,
``<provider>.api_key``, ``bedrock.region`` and ``tier.<tier>.<provider>_model``
for tiers fast | standard | expert (= deep) | heavy | embedding.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .catalog import DEFAULT_PROVIDER, MODELS
from .providers import PROVIDERS, get_provider

if TYPE_CHECKING:
    from ai_discovery.config import DiscoveryConfig


def _build_defaults() -> dict[str, str]:
    """Build the flat key→value defaults dict from MODELS."""
    d: dict[str, str] = {"provider": DEFAULT_PROVIDER}
    for provider, models in MODELS.items():
        for key, value in models.items():
            if key in ("region", "url"):
                d[f"{provider}.{key}"] = value
            else:
                d[f"tier.{key}.{provider}_model"] = value
    return d


DEFAULTS: dict[str, str] = _build_defaults()

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
    "aigw.url":                       "AIGW_BASE_URL",
    "omlx.url":                       "OMLX_BASE_URL",
    "openrouter.url":                 "OPENROUTER_BASE_URL",
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


def _flatten_discovery_config(cfg: "DiscoveryConfig") -> dict[str, str]:
    """Map DiscoveryConfig fields onto the flat key space.

    Role → router tier: fast → fast, standard → standard, deep → heavy, and the
    router's ``expert`` (documents) = deep when ``quality: final`` else standard
    (also aliased to legacy ``deep``). ``models.<role>`` overrides apply to the
    active provider only.
    """
    o: dict[str, str] = {"provider": cfg.provider}
    for name in PROVIDERS:
        pc = cfg.provider_section(name)
        suffix = f"{name}_model"
        overrides = cfg.models if name == cfg.provider else None
        fast = (overrides and overrides.fast) or pc.fast
        standard = (overrides and overrides.standard) or pc.standard
        deep = (overrides and overrides.deep) or pc.deep
        expert = deep if cfg.is_final else standard
        o[f"tier.fast.{suffix}"] = fast
        o[f"tier.standard.{suffix}"] = standard
        o[f"tier.expert.{suffix}"] = expert
        o[f"tier.deep.{suffix}"] = expert  # legacy alias
        o[f"tier.heavy.{suffix}"] = deep
        if name == "bedrock":
            o["bedrock.region"] = pc.region
        else:
            o[f"{name}.url"] = pc.base_url
            api_key = getattr(pc, "api_key", "")
            if api_key:
                o[f"{name}.api_key"] = api_key
    o["tier.embedding.bedrock_model"] = cfg.rag.bedrock_model
    o["tier.embedding.ollama_model"] = cfg.rag.ollama_model
    if cfg.rag.embedding_model:
        o[f"tier.embedding.{get_provider(cfg.rag.embedding_provider).model_suffix}"] = cfg.rag.embedding_model
    return o


@dataclass(frozen=True)
class LLMSettings:
    """Resolved LLM key space for one client (see module docstring)."""

    values: dict[str, str] = field(default_factory=dict)

    @property
    def provider(self) -> str:
        return self.values["provider"]

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def model(self, tier: str, provider: Optional[str] = None) -> str:
        """Model id for a router tier (fast | standard | expert | heavy | deep); falls back to standard."""
        suffix = get_provider(provider or self.provider).model_suffix
        return self.values.get(f"tier.{tier}.{suffix}", self.values[f"tier.standard.{suffix}"])

    def embedding_model(self, provider: Optional[str] = None) -> str:
        suffix = get_provider(provider or self.provider).model_suffix
        return self.values.get(f"tier.embedding.{suffix}",
                               DEFAULTS.get(f"tier.embedding.{suffix}", DEFAULTS["tier.embedding.ollama_model"]))

    def url(self, provider: Optional[str] = None) -> str:
        spec = get_provider(provider or self.provider)
        return self.values.get(spec.url_key, DEFAULTS.get(spec.url_key, spec.default_url))

    def region(self) -> str:
        return self.values.get("bedrock.region", "us-west-2")

    def api_key(self, provider: Optional[str] = None) -> str:
        """Config-supplied key (e.g. discovery.yaml) wins over the provider's env var."""
        spec = get_provider(provider or self.provider)
        key = self.values.get(f"{spec.name}.api_key", "")
        if key:
            return key
        return (os.getenv(spec.api_key_env) or "") if spec.api_key_env else ""

    def tier_for_doc_type(self, doc_type: str) -> str:
        return DOC_TYPE_TIER.get(doc_type, "standard")


def settings_from_config(cfg: "Optional[DiscoveryConfig]" = None,
                         overrides: Optional[dict[str, str]] = None) -> LLMSettings:
    """defaults < env < ``cfg`` < explicit *overrides* (validated against the key space)."""
    values = dict(DEFAULTS)
    for key, env_var in _ENV_MAP.items():
        val = os.getenv(env_var)
        if val:
            values[key] = val
    if cfg is not None:
        values.update(_flatten_discovery_config(cfg))
    if overrides:
        bad = set(overrides) - set(DEFAULTS)
        if bad:
            raise ValueError(f"Unknown LLM config key(s): {sorted(bad)}")
        values.update(overrides)
    return LLMSettings(values)
