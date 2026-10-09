"""The one config template (roadmap item M1.3).

`discover init`, `src/ai_discovery/discovery.yaml.example` and the repo-root
`discovery.yaml` are all rendered from here; a test keeps the checked-in files
in sync and checks every rendered template loads without errors.
"""

from __future__ import annotations

from ai_discovery.config import (
    BedrockConfig, OllamaConfig, MLXGemmaConfig, MLXQwenConfig,
    OpenAIConfig, GeminiConfig, AnthropicConfig,
    RagConfig, ProcessMiningConfig, AdvisorConfig
    )


def render_template(provider: str = "ollama") -> str:
    """YAML config template for *provider*, with comments, from the dataclass defaults."""
    # Build template as list of (key, value) tuples to preserve ordering
    # and allow duplicate blank-line entries.
    template: list[tuple[str, object]] = [
        ("# AI-Discovery Configuration", None),
        ("# Customize discovery behavior, model selection, and optional features", None),
        ("", None),
        ("# Provider: bedrock | ollama | mlx-gemma | mlx-qwen | openai | gemini | anthropic", None),
        ("provider", provider),
        ("", None),
        ("# LLM cost limit (USD) — stops pipeline if exceeded", None),
        ("budget_limit_usd", 50.0),
        ("", None),
        ("# Max concurrent workers for parallel processing", None),
        ("max_concurrent", 10),
        ("", None),
        ("# Production mode: use tier3p (deep) instead of tier3d (standard) for doc generation", None),
        ("prod", False),
    ]

    # Provider-specific sections
    if provider == "bedrock":
        bedrock = BedrockConfig()
        template.extend([
            ("", None),
            ("# Bedrock configuration", None),
            ("bedrock", {"region": bedrock.region, "tier1": bedrock.tier1,
                         "tier2": bedrock.tier2, "tier3d": bedrock.tier3d, "tier3p": bedrock.tier3p}),
        ])
    elif provider == "mlx-gemma":
        mlx = MLXGemmaConfig()
        template.extend([
            ("", None),
            ("# MLX Gemma configuration", None),
            ("mlx_gemma", {"base_url": mlx.base_url, "api_key": mlx.api_key,
                           "tier1": mlx.tier1, "tier2": mlx.tier2, "tier3d": mlx.tier3d,
                           "tier3p": mlx.tier3p, "tier1_num_ctx": mlx.tier1_num_ctx}),
        ])
    elif provider == "mlx-qwen":
        mlx = MLXQwenConfig()
        template.extend([
            ("", None),
            ("# MLX Qwen configuration", None),
            ("mlx_qwen", {"base_url": mlx.base_url, "api_key": mlx.api_key,
                          "tier1": mlx.tier1, "tier2": mlx.tier2, "tier3d": mlx.tier3d,
                          "tier3p": mlx.tier3p, "tier1_num_ctx": mlx.tier1_num_ctx}),
        ])
    elif provider == "openai":
        oai = OpenAIConfig()
        template.extend([
            ("", None),
            ("# OpenAI configuration — set OPENAI_API_KEY in the environment (not here)", None),
            ("openai", {"base_url": oai.base_url, "tier1": oai.tier1,
                        "tier2": oai.tier2, "tier3d": oai.tier3d, "tier3p": oai.tier3p}),
        ])
    elif provider == "gemini":
        gem = GeminiConfig()
        template.extend([
            ("", None),
            ("# Gemini (OpenAI-compatible endpoint) — set GEMINI_API_KEY in the environment", None),
            ("gemini", {"base_url": gem.base_url, "tier1": gem.tier1,
                        "tier2": gem.tier2, "tier3d": gem.tier3d, "tier3p": gem.tier3p}),
        ])
    elif provider == "anthropic":
        ant = AnthropicConfig()
        template.extend([
            ("", None),
            ("# Anthropic direct — set ANTHROPIC_API_KEY in the environment.", None),
            ("# Requires the optional SDK: pip install anthropic", None),
            ("# Note: Anthropic has no embedding API — keep rag.embedding_provider on", None),
            ("# bedrock/ollama/openai/gemini.", None),
            ("anthropic", {"base_url": ant.base_url, "tier1": ant.tier1,
                           "tier2": ant.tier2, "tier3d": ant.tier3d, "tier3p": ant.tier3p}),
        ])
    else:  # ollama
        ollama = OllamaConfig()
        template.extend([
            ("", None),
            ("# Ollama configuration", None),
            ("ollama", {"base_url": ollama.base_url, "tier1": ollama.tier1,
                        "tier2": ollama.tier2, "tier3d": ollama.tier3d, "tier3p": ollama.tier3p,
                        "tier1_num_ctx": ollama.tier1_num_ctx}),
        ])

    # RAG config
    rag = RagConfig()
    template.extend([
        ("", None),
        ("# RAG configuration", None),
        ("rag", {"embedding_provider": rag.embedding_provider, "bedrock_model": rag.bedrock_model,
                 "ollama_model": rag.ollama_model, "chunk_size": rag.chunk_size,
                 "chunk_overlap": rag.chunk_overlap, "top_k": rag.top_k}),
    ])

    # Process Mining config
    mining = ProcessMiningConfig()
    template.extend([
        ("", None),
        ("# Flow Statistics Configuration (Phase 16) — OPTIONAL", None),
        ("# Set enabled: true to enable per-scenario flow statistics reports", None),
        ("process_mining", {"enabled": mining.enabled,
                            "max_traces": mining.max_traces, "output_reports": mining.output_reports}),
    ])

    # Advisor config
    advisor = AdvisorConfig()
    template.extend([
        ("", None),
        ("# Advisor Configuration (beta) — OPTIONAL", None),
        ("# Set enabled: true to enable advisor tool integration", None),
        ("advisor", {"enabled": advisor.enabled, "provider": advisor.provider,
                     "model": advisor.model, "tiers": advisor.tiers,
                     "max_uses_per_call": advisor.max_uses_per_call,
                     "tier3_executor_override": advisor.tier3_executor_override}),
    ])

    return _template_to_yaml(template)


def _yaml_scalar(v: object) -> str:
    """Format a scalar value safely for YAML output."""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return str(v)
    # String: quote if it contains special YAML chars or newlines
    s = str(v)
    if not s or '\n' in s or ':' in s or '#' in s or s.startswith('{') or s.startswith('['):
        return f'"{s}"'
    return s


def _template_to_yaml(entries: list[tuple[str, object]]) -> str:
    """Convert a list of (key, value) tuples to YAML text with comments and blank lines."""
    lines = []
    for key, value in entries:
        if key.startswith("#"):
            lines.append(key)
        elif key == "":
            lines.append("")
        elif value is None:
            continue
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for k, v in value.items():
                lines.append(f"  {k}: {_yaml_scalar(v)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"
