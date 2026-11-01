"""The one config template (roadmap item M1.3).

`discover init`, `src/ai_discovery/discovery.yaml.example` and the repo-root
`discovery.yaml` are all rendered from here; a test keeps the checked-in files
in sync and checks every rendered template loads without errors.
"""

from __future__ import annotations

from ai_discovery.config import (
    BedrockConfig, OllamaConfig, MLXGemmaConfig, MLXQwenConfig,
    OpenAIConfig, GeminiConfig, AnthropicConfig, AIGWConfig, OMLXConfig, OpenRouterConfig,
    RagConfig, ProcessMiningConfig, AdvisorConfig, ScanConfig, StoreConfig, CostGateConfig, PushConfig
    )


def render_template(provider: str = "aigw", *, prose: bool = False) -> str:
    """YAML config template for *provider*, with comments, from the dataclass defaults."""
    # Build template as list of (key, value) tuples to preserve ordering
    # and allow duplicate blank-line entries.
    template: list[tuple[str, object]] = [
        ("# AI-Discovery Configuration", None),
        ("# Customize discovery behavior, model selection, and optional features", None),
        ("", None),
        ("# Provider: aigw | omlx | openrouter | bedrock | ollama | mlx-gemma | mlx-qwen | openai | gemini | anthropic", None),
        ("provider", provider),
        ("", None),
        ("# Spend mode: false = structure only (no LLM calls, no key needed); true = add LLM prose", None),
        ("prose", prose),
        ("", None),
        ("# LLM cost limit (USD) — stops pipeline if exceeded", None),
        ("budget_limit_usd", 50.0),
        ("", None),
        ("# Cost gate for prose scans: runs below ask_above_usd, asks (default Yes) up to default_no_above_usd,", None),
        ("# asks (default No) above it; `scan --dry-run` prints the estimate, `--yes` pre-approves", None),
        ("cost_gate", {"ask_above_usd": CostGateConfig().ask_above_usd,
                       "default_no_above_usd": CostGateConfig().default_no_above_usd}),
        ("", None),
        ("# Max concurrent workers for parallel processing", None),
        ("max_concurrent", 10),
        ("", None),
        ("# Quality: draft = documents use the standard model (cheap); final = deep model + source verification", None),
        ("quality", "draft"),
        ("", None),
        ("# Model per role for the active provider; empty = the provider's default (see its section)", None),
        ("models", {"fast": "", "standard": "", "deep": ""}),
    ]

    # Provider-specific sections
    if provider == "bedrock":
        bedrock = BedrockConfig()
        template.extend([
            ("", None),
            ("# Bedrock configuration", None),
            ("bedrock", {"region": bedrock.region, "fast": bedrock.fast,
                         "standard": bedrock.standard, "deep": bedrock.deep}),
        ])
    elif provider == "mlx-gemma":
        mlx = MLXGemmaConfig()
        template.extend([
            ("", None),
            ("# MLX Gemma configuration", None),
            ("mlx_gemma", {"base_url": mlx.base_url, "api_key": mlx.api_key,
                           "fast": mlx.fast, "standard": mlx.standard, "deep": mlx.deep, "fast_num_ctx": mlx.fast_num_ctx}),
        ])
    elif provider == "mlx-qwen":
        mlx = MLXQwenConfig()
        template.extend([
            ("", None),
            ("# MLX Qwen configuration", None),
            ("mlx_qwen", {"base_url": mlx.base_url, "api_key": mlx.api_key,
                          "fast": mlx.fast, "standard": mlx.standard, "deep": mlx.deep, "fast_num_ctx": mlx.fast_num_ctx}),
        ])
    elif provider == "openai":
        oai = OpenAIConfig()
        template.extend([
            ("", None),
            ("# OpenAI configuration — set OPENAI_API_KEY in the environment (not here)", None),
            ("openai", {"base_url": oai.base_url, "fast": oai.fast,
                        "standard": oai.standard, "deep": oai.deep}),
        ])
    elif provider == "gemini":
        gem = GeminiConfig()
        template.extend([
            ("", None),
            ("# Gemini (OpenAI-compatible endpoint) — set GEMINI_API_KEY in the environment", None),
            ("gemini", {"base_url": gem.base_url, "fast": gem.fast,
                        "standard": gem.standard, "deep": gem.deep}),
        ])
    elif provider == "anthropic":
        ant = AnthropicConfig()
        template.extend([
            ("", None),
            ("# Anthropic direct — set ANTHROPIC_API_KEY in the environment.", None),
            ("# Requires the optional SDK: pip install anthropic", None),
            ("# Note: Anthropic has no embedding API — keep rag.embedding_provider on", None),
            ("# bedrock/ollama/openai/gemini.", None),
            ("anthropic", {"base_url": ant.base_url, "fast": ant.fast,
                           "standard": ant.standard, "deep": ant.deep}),
        ])
    elif provider == "aigw":
        gw = AIGWConfig()
        template.extend([
            ("", None),
            ("# Local ai-gateway (OpenAI-compatible, fronts OMLX) — set AIGW_API_KEY in the", None),
            ("# environment. Models are gateway aliases; the key must allowlist each one.", None),
            ("aigw", {"base_url": gw.base_url, "fast": gw.fast,
                      "standard": gw.standard, "deep": gw.deep}),
        ])
    elif provider == "omlx":
        omlx = OMLXConfig()
        template.extend([
            ("", None),
            ("# Local OMLX host (OpenAI-compatible) — set OMLX_API_KEY in the environment", None),
            ("omlx", {"base_url": omlx.base_url, "fast": omlx.fast,
                      "standard": omlx.standard, "deep": omlx.deep}),
        ])
    elif provider == "openrouter":
        orc = OpenRouterConfig()
        template.extend([
            ("", None),
            ("# OpenRouter (OpenAI-compatible) — set OPENROUTER_API_KEY in the environment", None),
            ("openrouter", {"base_url": orc.base_url, "fast": orc.fast,
                            "standard": orc.standard, "deep": orc.deep}),
        ])
    else:  # ollama
        ollama = OllamaConfig()
        template.extend([
            ("", None),
            ("# Ollama configuration", None),
            ("ollama", {"base_url": ollama.base_url, "fast": ollama.fast,
                        "standard": ollama.standard, "deep": ollama.deep,
                        "fast_num_ctx": ollama.fast_num_ctx}),
        ])

    # RAG config
    rag = RagConfig()
    template.extend([
        ("", None),
        ("# RAG configuration", None),
        ("# embedding_model: model for embedding_provider (empty = provider default)", None),
        ("rag", {"embedding_provider": rag.embedding_provider,
                 "embedding_model": rag.embedding_model, "bedrock_model": rag.bedrock_model,
                 "ollama_model": rag.ollama_model, "chunk_size": rag.chunk_size,
                 "chunk_overlap": rag.chunk_overlap, "top_k": rag.top_k}),
        ("", None),
        ("# Snapshot retention: completed scans kept in the index (the current one is always kept)", None),
        ("store", {"keep_snapshots": StoreConfig().keep_snapshots}),
        ("", None),
        ("# Which files a scan reads (M6.1); .gitignore and .discoveryignore always apply.", None),
        ("# Add gitignore-syntax excludes with  exclude: [\"**/Scripts/vendor/**\"]", None),
        ("scan", {"max_file_kb_source": ScanConfig().max_file_kb_source,
                  "max_file_kb_other": ScanConfig().max_file_kb_other, "submodules": ScanConfig().submodules}),
        ("", None),
        ("# `discover push` targets (tokens only from env: DISCOVERY_DOCHUB_TOKEN, DISCOVERY_GITEA_TOKEN)", None),
        ("push", {"dochub_url": PushConfig().dochub_url, "gitea_url": PushConfig().gitea_url,
                  "gitea_owner": PushConfig().gitea_owner, "gitea_repo": PushConfig().gitea_repo,
                  "batch_size": PushConfig().batch_size}),
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
