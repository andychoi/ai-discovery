"""Discovery CLI configuration — YAML loader + dataclasses."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


log = logging.getLogger(__name__)

# Import single source of truth for model IDs
try:
    from ai_discovery.shared.model_defaults import MODELS, DEFAULT_PROVIDER
except ImportError:
    # Fallback when running standalone (outside the app package)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ai_discovery.shared.model_defaults import MODELS, DEFAULT_PROVIDER

_B = MODELS["bedrock"]
_O = MODELS["ollama"]
_MG = MODELS.get("mlx-gemma", {})
_MQ = MODELS.get("mlx-qwen", {})
_OAI = MODELS.get("openai", {})
_GEM = MODELS.get("gemini", {})
_ANT = MODELS.get("anthropic", {})
_GW = MODELS.get("aigw", {})
_OMLX = MODELS.get("omlx", {})
_OR = MODELS.get("openrouter", {})

# ── Provider configs ─────────────────────────────────────────────────────────

# Model roles (M1.10.b): every provider section names a model per role —
#   fast     → Tier-1 summaries, self-review
#   standard → flows, screens, onboarding; also rollups when quality: draft
#   deep     → rollups when quality: final
# (replaces tier1 / tier2 / tier3d / tier3p; tier3d defaulted to the standard model)
ROLES = ("fast", "standard", "deep")
_VALID_ROLES = frozenset({*ROLES, "screen"})   # screen = standard


@dataclass
class BedrockConfig:
    region: str = _B["region"]
    fast: str = _B["fast"]
    standard: str = _B["standard"]
    deep: str = _B["deep"]       # quality: final document generation


@dataclass
class MLXGemmaConfig:
    """ai-mlx-server (:11435) gemma4 variant — Apple Silicon MLX runtime."""
    base_url: str = _MG.get("url", "http://localhost:11435")
    api_key: str = ""
    fast: str = _MG.get("fast", "mlx-community/gemma-4-e2b-it-4bit")
    standard: str = _MG.get("expert", "mlx-community/gemma-4-26b-a4b-it-4bit")
    deep: str = _MG.get("heavy", "mlx-community/gemma-4-31b-it-4bit")
    fast_num_ctx: int = 4096


@dataclass
class MLXQwenConfig:
    """ai-mlx-server Qwen3.5 variant — same server, different model tags."""
    base_url: str = _MQ.get("url", "http://localhost:11435")
    api_key: str = ""
    fast: str = _MQ.get("fast", "mlx-community/Qwen3.5-2B-4bit")
    standard: str = _MQ.get("standard", "mlx-community/Qwen3.5-4B-MLX-4bit")
    deep: str = _MQ.get("heavy", "mlx-community/Qwen3.5-27B-4bit")
    fast_num_ctx: int = 4096


@dataclass
class OllamaConfig:
    base_url: str = _O["url"]
    # The fast role follows the shared "fast" default (gemma4:e2b). Discovery runs
    # Tier 1 concurrently over every code chunk so latency and heat dominate;
    # 2B effective is sufficient for structured JSON extraction.
    fast: str = _O["fast"]
    # standard/deep are pinned to literal model IDs (not shared defaults) because
    # Discovery's flow analysis and doc generation are heavier workloads
    # than DocHub's chat — they should not shrink when the shared "standard"
    # tier is lightened for interactive use. Override in discovery.yaml if
    # you want to share DocHub's lighter tiers.
    standard: str = "gemma4:26b"   # flow analysis + draft docs: cross-chunk reasoning
    deep: str = "gemma4:31b"      # final-quality doc generation
    # Cap the fast role's Ollama context window. Gemma/Qwen default to 32K tokens
    # which pre-allocates ~11GB of KV cache on GPU even for ~2K-token chunks.
    # 4096 comfortably fits a 6000-char chunk + RAG context + 1024 output.
    # Set to 0 to use the model's default context window.
    fast_num_ctx: int = 4096


# ── Cloud API providers (OpenAI-compatible + Anthropic direct) ───────────────


@dataclass
class OpenAIConfig:
    """OpenAI (api.openai.com). Auth via api_key or OPENAI_API_KEY env."""
    base_url: str = _OAI.get("url", "https://api.openai.com/v1")
    api_key: str = ""
    fast: str = _OAI.get("fast", "gpt-5-nano")
    standard: str = _OAI.get("standard", "gpt-5-mini")
    deep: str = _OAI.get("expert", "gpt-5.1")


@dataclass
class GeminiConfig:
    """Google Gemini via its OpenAI-compatible endpoint.
    Auth via api_key or GEMINI_API_KEY env."""
    base_url: str = _GEM.get(
        "url", "https://generativelanguage.googleapis.com/v1beta/openai")
    api_key: str = ""
    fast: str = _GEM.get("fast", "gemini-2.5-flash-lite")
    standard: str = _GEM.get("standard", "gemini-3.5-flash")
    deep: str = _GEM.get("expert", "gemini-2.5-pro")


@dataclass
class AnthropicConfig:
    """Anthropic direct (api.anthropic.com via the anthropic SDK).
    Auth via api_key or ANTHROPIC_API_KEY env (SDK-resolved).
    No embedding models — Anthropic has no embeddings API."""
    base_url: str = _ANT.get("url", "https://api.anthropic.com")
    api_key: str = ""
    fast: str = _ANT.get("fast", "claude-haiku-5-5")
    standard: str = _ANT.get("standard", "claude-sonnet-5-5")
    deep: str = _ANT.get("expert", "claude-opus-5-5")


@dataclass
class AIGWConfig:
    """Local ai-gateway (:11433, OpenAI-compatible, fronts OMLX).
    Model IDs are gateway aliases. Auth via api_key or AIGW_API_KEY env."""
    base_url: str = _GW.get("url", "http://localhost:11433/v1")
    api_key: str = ""
    fast: str = _GW.get("fast", "claude-haiku")
    standard: str = _GW.get("standard", "claude-sonnet")
    deep: str = _GW.get("expert", "claude-opus")


@dataclass
class OMLXConfig:
    """Local OMLX host (:11434, OpenAI-compatible). Auth via api_key or OMLX_API_KEY env."""
    base_url: str = _OMLX.get("url", "http://localhost:11434/v1")
    api_key: str = ""
    fast: str = _OMLX.get("fast", "Qwen3.5-9B-MTPLX-Optimized-Speed")
    standard: str = _OMLX.get("standard", "Qwen3.5-9B-MTPLX-Optimized-Speed")
    deep: str = _OMLX.get("expert", "Qwen3.5-9B-MTPLX-Optimized-Speed")


@dataclass
class OpenRouterConfig:
    """OpenRouter (OpenAI-compatible). Auth via api_key or OPENROUTER_API_KEY env."""
    base_url: str = _OR.get("url", "https://openrouter.ai/api/v1")
    api_key: str = ""
    fast: str = _OR.get("fast", "anthropic/claude-haiku-5.5")
    standard: str = _OR.get("standard", "anthropic/claude-sonnet-5.5")
    deep: str = _OR.get("expert", "anthropic/claude-opus-5.5")


# ── Store config ─────────────────────────────────────────────────────────────


@dataclass
class StoreConfig:
    """Snapshot retention (M1.5): completed scans kept besides the current one's."""
    keep_snapshots: int = 3


# ── RAG config ───────────────────────────────────────────────────────────────


@dataclass
class RagConfig:
    embedding_provider: str = DEFAULT_PROVIDER
    bedrock_model: str = _B["embedding"]
    ollama_model: str = _O["embedding"]
    # Embedding model for embedding_provider; "" = that provider's default.
    embedding_model: str = ""
    chunk_size: int = 1500
    chunk_overlap: int = 200
    top_k: int = 5
    # If False, skip Phase 10 entirely. RAG index is only used by `discover chat`
    # and (optionally) Tier 1 grounding; many scans don't need it.
    enabled: bool = True
    # If True, test files are excluded from RAG embedding — they rarely help
    # when asking questions about production behavior and can drown retrieval.
    skip_tests: bool = True


# ── Process Mining config ────────────────────────────────────────────────────


@dataclass
class ProcessMiningConfig:
    """Configuration for optional Phase 16 flow statistics.

    Formerly configured pm4py-based mining (miner_variant, fitness/precision/
    generalization thresholds); those fields were removed 2026-06-06 along with
    pm4py — old keys are now rejected with a "delete this key" hint (config/loader.py).
    """
    enabled: bool = False  # Default: disabled (opt-in via config or CLI flag)
    max_traces: int = 10000  # Max traces to process per scenario
    output_reports: bool = True  # Save markdown and JSON reports


# ── Advisor config ───────────────────────────────────────────────────────────


@dataclass
class AdvisorConfig:
    """Configuration for optional advisor tool integration (beta).

    The advisor tool allows a faster executor model to consult a higher-intelligence
    advisor model for strategic guidance. Native path uses the Anthropic SDK beta
    (requires ANTHROPIC_API_KEY); simulated path uses Bedrock Opus pre-call.
    """
    enabled: bool = False
    provider: str = "auto"          # auto | anthropic | simulated | disabled
    model: str = "claude-opus-4-7"  # advisor model (native path only)
    tiers: list = field(default_factory=lambda: ["tier2", "tier3"])
    max_uses_per_call: int = 1
    tier3_executor_override: str = ""  # e.g. "claude-sonnet-4-6" to reduce tier3 cost on native path


# ── Model role overrides ─────────────────────────────────────────────────────


@dataclass
class ModelsConfig:
    """Role → model id for the *active* provider; "" keeps that provider's default."""
    fast: str = ""
    standard: str = ""
    deep: str = ""


# ── Top-level config ─────────────────────────────────────────────────────────


@dataclass
class DiscoveryConfig:
    provider: str = DEFAULT_PROVIDER
    budget_limit_usd: float = 50.0
    max_concurrent: int = 10
    # draft: rollups use the standard model, no verification passes (cheap);
    # final: rollups use the deep model + source verification of flows/screens.
    # Replaces `prod:` / `--prod` (M1.10.b).
    quality: Literal["draft", "final"] = "draft"
    models: ModelsConfig = field(default_factory=ModelsConfig)
    # M2.2 spend mode recorded by `discover init`: False = structure only (no
    # LLM calls, no key); True = add LLM prose. `scan --prose/--no-prose` overrides.
    prose: bool = False
    # M0.10 keyless mode: run only deterministic phases (no LLM, no embeddings,
    # no network). Graph, screens (structural specs), FSM/DMN/EARS and the
    # dependency catalog are still produced.
    no_llm: bool = False
    # A-1 Louvain semantic batching: group Tier-1 chunks by call-graph
    # community and fire one structured call per batch (cheaper, and the
    # summarizer sees the callers/callees it describes). Degrades to the
    # per-chunk path automatically when clustering fails.
    semantic_batching: bool = True

    bedrock: BedrockConfig = field(default_factory=BedrockConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    mlx_gemma: MLXGemmaConfig = field(default_factory=MLXGemmaConfig)
    mlx_qwen: MLXQwenConfig = field(default_factory=MLXQwenConfig)
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    aigw: AIGWConfig = field(default_factory=AIGWConfig)
    omlx: OMLXConfig = field(default_factory=OMLXConfig)
    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    process_mining: ProcessMiningConfig = field(default_factory=ProcessMiningConfig)
    advisor: AdvisorConfig = field(default_factory=AdvisorConfig)

    # ── helpers ───────────────────────────────────────────────────────────

    @property
    def is_final(self) -> bool:
        return self.quality == "final"

    def provider_section(self, provider: Optional[str] = None):
        """The config section for *provider* (default: the active one)."""
        return getattr(self, (provider or self.provider).replace("-", "_"))

    def get_model(self, role: str) -> str:
        """Model id for *role* (fast | standard | deep | screen) on the active provider.

        ``models.<role>`` overrides the provider section. ``deep`` resolves to
        the standard model unless ``quality: final``; ``screen`` is standard.
        """
        if role not in _VALID_ROLES:
            raise ValueError(f"Invalid model role '{role}', must be one of {sorted(_VALID_ROLES)}")
        if role == "screen" or (role == "deep" and not self.is_final):
            role = "standard"
        return getattr(self.models, role) or getattr(self.provider_section(), role)

    def get_endpoint(self) -> tuple[str, str]:
        """Return (base_url, api_key) for the active HTTP-endpoint provider.
        Raises for bedrock (SDK/region-based, no base URL)."""
        if self.provider == "bedrock":
            raise ValueError("get_endpoint() is only valid for URL-based providers")
        if self.provider == "mlx-gemma":
            return self.mlx_gemma.base_url, self.mlx_gemma.api_key
        if self.provider == "mlx-qwen":
            return self.mlx_qwen.base_url, self.mlx_qwen.api_key
        if self.provider == "openai":
            return self.openai.base_url, self.openai.api_key
        if self.provider == "gemini":
            return self.gemini.base_url, self.gemini.api_key
        if self.provider == "anthropic":
            return self.anthropic.base_url, self.anthropic.api_key
        if self.provider == "aigw":
            return self.aigw.base_url, self.aigw.api_key
        if self.provider == "omlx":
            return self.omlx.base_url, self.omlx.api_key
        if self.provider == "openrouter":
            return self.openrouter.base_url, self.openrouter.api_key
        return self.ollama.base_url, ""

    # ── factory ───────────────────────────────────────────────────────────

    @classmethod
    def load(cls, config_path: Optional[str] = None, *, cwd: Optional[Path] = None) -> "DiscoveryConfig":
        """Load configuration (M1.3): defaults < user file < project file < env.

        *config_path* (``-c``) replaces project-file discovery and must exist.
        Without it, ``<project>/.discovery/config.yaml`` (walking up from
        *cwd*) or ``./discovery.yaml`` is used when present. Unknown keys and
        wrong types raise ``ConfigError``. CLI flags are applied by the caller
        on top of the returned object.
        """
        from .loader import load_config

        return load_config(config_path, cwd=cwd)


def _apply_env_overrides(cfg: "DiscoveryConfig", yaml_has_bedrock: bool) -> "DiscoveryConfig":
    """Environment-variable overrides (precedence above every config file)."""
    # Env-var overrides
    env_provider = os.environ.get("DISCOVERY_LLM_PROVIDER")
    if env_provider:
        cfg.provider = env_provider

    # Ollama base URL override (needed when running in a container — host LLM via host.docker.internal)
    env_ollama_url = os.environ.get("OLLAMA_URL")
    if env_ollama_url:
        cfg.ollama.base_url = env_ollama_url

    # MLX base URL override — both mlx-gemma and mlx-qwen target the same
    # ai-mlx-server instance, so they share MLX_SERVER_URL. In Docker, set
    # this to http://host.docker.internal:11435 so the container can reach
    # the host-native MLX runtime (MLX is Apple Silicon, can't run in a
    # Linux container).
    env_mlx_url = os.environ.get("MLX_SERVER_URL")
    if env_mlx_url:
        cfg.mlx_gemma.base_url = env_mlx_url
        cfg.mlx_qwen.base_url = env_mlx_url
    env_mlx_key = os.environ.get("MLX_API_KEY")
    if env_mlx_key:
        cfg.mlx_gemma.api_key = env_mlx_key
        cfg.mlx_qwen.api_key = env_mlx_key

    # Cloud API provider overrides — keys belong in env, not YAML.
    # The anthropic SDK resolves ANTHROPIC_API_KEY itself, but we also
    # surface it on the config for symmetry with openai/gemini.
    env_openai_key = os.environ.get("OPENAI_API_KEY")
    if env_openai_key:
        cfg.openai.api_key = env_openai_key
    env_openai_url = os.environ.get("OPENAI_BASE_URL")
    if env_openai_url:
        cfg.openai.base_url = env_openai_url
    env_gemini_key = os.environ.get("GEMINI_API_KEY")
    if env_gemini_key:
        cfg.gemini.api_key = env_gemini_key
    env_gemini_url = os.environ.get("GEMINI_BASE_URL")
    if env_gemini_url:
        cfg.gemini.base_url = env_gemini_url
    env_anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_anthropic_key:
        cfg.anthropic.api_key = env_anthropic_key
    env_aigw_key = os.environ.get("AIGW_API_KEY")
    if env_aigw_key:
        cfg.aigw.api_key = env_aigw_key
    env_aigw_url = os.environ.get("AIGW_BASE_URL")
    if env_aigw_url:
        cfg.aigw.base_url = env_aigw_url
    env_omlx_key = os.environ.get("OMLX_API_KEY")
    if env_omlx_key:
        cfg.omlx.api_key = env_omlx_key
    env_omlx_url = os.environ.get("OMLX_BASE_URL")
    if env_omlx_url:
        cfg.omlx.base_url = env_omlx_url
    env_openrouter_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API")
    if env_openrouter_key:
        cfg.openrouter.api_key = env_openrouter_key
    env_openrouter_url = os.environ.get("OPENROUTER_BASE_URL")
    if env_openrouter_url:
        cfg.openrouter.base_url = env_openrouter_url

    env_region = os.environ.get("AWS_REGION_NAME") or os.environ.get("AWS_DEFAULT_REGION")
    if env_region:
        cfg.bedrock.region = env_region

    # Fall back to shared LLM_* env vars if no YAML bedrock override
    if not yaml_has_bedrock:
        env_fast = os.environ.get("LLM_FAST_BEDROCK_MODEL")
        env_std = os.environ.get("LLM_STANDARD_BEDROCK_MODEL")
        if env_fast:
            cfg.bedrock.fast = env_fast
        if env_std:
            cfg.bedrock.standard = env_std

    # Deep-role env override (always applied). DISCOVERY_TIER3D_MODEL /
    # DISCOVERY_TIER3P_MODEL were removed with tier3d/tier3p (M1.10.b).
    env_deep = os.environ.get("DISCOVERY_DEEP_MODEL")
    if env_deep:
        cfg.bedrock.deep = env_deep
    for old in ("DISCOVERY_TIER3D_MODEL", "DISCOVERY_TIER3P_MODEL"):
        if os.environ.get(old):
            log.warning("%s is no longer read — use DISCOVERY_DEEP_MODEL (deep role, quality: final) "
                        "or models.standard for draft documents", old)

    # Provider-agnostic overrides for the *active* provider (applied after the
    # provider-specific vars above, so they win). DISCOVERY_LLM_MODEL pins one
    # model for every role.
    active = getattr(cfg, cfg.provider.replace("-", "_"), None)
    env_llm_key = os.environ.get("DISCOVERY_LLM_API_KEY")
    if env_llm_key and active is not None and hasattr(active, "api_key"):
        active.api_key = env_llm_key
    env_llm_model = os.environ.get("DISCOVERY_LLM_MODEL")
    if env_llm_model and active is not None:
        for role in ROLES:
            setattr(active, role, env_llm_model)

    # RAG embedding overrides
    env_embed_provider = os.environ.get("DISCOVERY_EMBEDDING_PROVIDER")
    if env_embed_provider:
        cfg.rag.embedding_provider = env_embed_provider
    env_embed_model = os.environ.get("DISCOVERY_EMBEDDING_MODEL")
    if env_embed_model:
        cfg.rag.bedrock_model = env_embed_model
        cfg.rag.embedding_model = env_embed_model

    return cfg
