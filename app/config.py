"""Discovery CLI configuration — YAML loader + dataclasses."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

# Import single source of truth for model IDs
try:
    from app.shared.model_defaults import MODELS, DEFAULT_PROVIDER
except ImportError:
    # Fallback when running standalone (outside the app package)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.shared.model_defaults import MODELS, DEFAULT_PROVIDER

_B = MODELS["bedrock"]
_O = MODELS["ollama"]
_MG = MODELS.get("mlx-gemma", {})
_MQ = MODELS.get("mlx-qwen", {})

# ── Provider configs ─────────────────────────────────────────────────────────

_VALID_TIERS = frozenset({"tier1", "tier2", "tier3"})

# Discovery tier mapping:  tier1→fast, tier2→standard, tier3d→fast (dev), tier3p→deep (prod)


@dataclass
class BedrockConfig:
    region: str = _B["region"]
    tier1: str = _B["fast"]
    tier2: str = _B["standard"]
    tier3d: str = _B["standard"]   # dev: use standard model for doc generation
    tier3p: str = _B["deep"]       # prod: use deep model for doc generation


@dataclass
class MLXGemmaConfig:
    """ai-mlx-server (:11435) gemma4 variant — Apple Silicon MLX runtime."""
    base_url: str = _MG.get("url", "http://localhost:11435")
    api_key: str = ""
    tier1: str = _MG.get("fast", "mlx-community/gemma-4-e2b-it-4bit")
    tier2: str = _MG.get("expert", "mlx-community/gemma-4-26b-a4b-it-4bit")
    tier3d: str = _MG.get("expert", "mlx-community/gemma-4-26b-a4b-it-4bit")
    tier3p: str = _MG.get("heavy", "mlx-community/gemma-4-31b-it-4bit")
    tier1_num_ctx: int = 4096


@dataclass
class MLXQwenConfig:
    """ai-mlx-server Qwen3.5 variant — same server, different model tags."""
    base_url: str = _MQ.get("url", "http://localhost:11435")
    api_key: str = ""
    tier1: str = _MQ.get("fast", "mlx-community/Qwen3.5-2B-4bit")
    tier2: str = _MQ.get("standard", "mlx-community/Qwen3.5-4B-MLX-4bit")
    tier3d: str = _MQ.get("expert", "mlx-community/Qwen3.5-9B-MLX-4bit")
    tier3p: str = _MQ.get("heavy", "mlx-community/Qwen3.5-27B-4bit")
    tier1_num_ctx: int = 4096


@dataclass
class OllamaConfig:
    base_url: str = _O["url"]
    # Tier1 follows the shared "fast" default (gemma4:e2b). Discovery runs
    # tier1 concurrently over every code chunk so latency and heat dominate;
    # 2B effective is sufficient for structured JSON extraction.
    tier1: str = _O["fast"]
    # Tier2/3 are pinned to literal model IDs (not shared defaults) because
    # Discovery's flow analysis and doc generation are heavier workloads
    # than DocHub's chat — they should not shrink when the shared "standard"
    # tier is lightened for interactive use. Override in discovery.yaml if
    # you want to share DocHub's lighter tiers.
    tier2: str = "gemma4:26b"   # flow analysis: cross-chunk reasoning
    tier3d: str = "gemma4:26b"  # dev doc generation: same as tier2
    tier3p: str = "gemma4:31b"  # prod doc generation: highest quality
    # Cap tier1's Ollama context window. Gemma/Qwen default to 32K tokens
    # which pre-allocates ~11GB of KV cache on GPU even for ~2K-token chunks.
    # 4096 comfortably fits a 6000-char chunk + RAG context + 1024 output.
    # Set to 0 to use the model's default context window.
    tier1_num_ctx: int = 4096


# ── RAG config ───────────────────────────────────────────────────────────────


@dataclass
class RagConfig:
    embedding_provider: str = DEFAULT_PROVIDER
    bedrock_model: str = _B["embedding"]
    ollama_model: str = _O["embedding"]
    chunk_size: int = 1500
    chunk_overlap: int = 200
    top_k: int = 5


# ── Top-level config ─────────────────────────────────────────────────────────


@dataclass
class DiscoveryConfig:
    provider: str = DEFAULT_PROVIDER
    budget_limit_usd: float = 50.0
    max_concurrent: int = 10
    prod: bool = False  # True → use tier3p for doc generation; False → tier3d

    bedrock: BedrockConfig = field(default_factory=BedrockConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    mlx_gemma: MLXGemmaConfig = field(default_factory=MLXGemmaConfig)
    mlx_qwen: MLXQwenConfig = field(default_factory=MLXQwenConfig)
    rag: RagConfig = field(default_factory=RagConfig)

    # ── helpers ───────────────────────────────────────────────────────────

    def get_model(self, tier: str) -> str:
        """Return the model ID for *tier* (tier1 | tier2 | tier3) from the
        active provider. For tier3, selects tier3p (prod) or tier3d (dev)."""
        if tier not in _VALID_TIERS:
            raise ValueError(f"Invalid tier '{tier}', must be one of {_VALID_TIERS}")
        key = ("tier3p" if self.prod else "tier3d") if tier == "tier3" else tier
        if self.provider == "bedrock":
            return getattr(self.bedrock, key)
        if self.provider == "mlx-gemma":
            return getattr(self.mlx_gemma, key)
        if self.provider == "mlx-qwen":
            return getattr(self.mlx_qwen, key)
        return getattr(self.ollama, key)

    def get_endpoint(self) -> tuple[str, str]:
        """Return (base_url, api_key) for the active ollama-compatible provider.
        Raises for bedrock."""
        if self.provider == "bedrock":
            raise ValueError("get_endpoint() is only valid for ollama-compatible providers")
        if self.provider == "mlx-gemma":
            return self.mlx_gemma.base_url, self.mlx_gemma.api_key
        if self.provider == "mlx-qwen":
            return self.mlx_qwen.base_url, self.mlx_qwen.api_key
        return self.ollama.base_url, ""

    # ── factory ───────────────────────────────────────────────────────────

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "DiscoveryConfig":
        """Load configuration from a YAML file (if provided) with env-var
        override for the LLM provider.

        Environment variable ``DISCOVERY_LLM_PROVIDER`` overrides the
        ``provider`` field when set.
        """
        cfg = cls()
        yaml_has_bedrock = False

        if config_path and Path(config_path).exists():
            with open(config_path) as fh:
                raw = yaml.safe_load(fh) or {}

            # Top-level scalars
            if "provider" in raw:
                cfg.provider = raw["provider"]
            if "budget_limit_usd" in raw:
                cfg.budget_limit_usd = float(raw["budget_limit_usd"])
            if "max_concurrent" in raw:
                cfg.max_concurrent = int(raw["max_concurrent"])

            # Nested sections — filter to known fields so forward/backward
            # compatible YAMLs don't raise TypeError on unexpected keys.
            def _only_known(dc_cls, d: dict) -> dict:
                allowed = {f.name for f in fields(dc_cls)}
                dropped = set(d) - allowed
                if dropped:
                    log.warning("Ignoring unknown %s keys in YAML: %s",
                                dc_cls.__name__, sorted(dropped))
                return {k: v for k, v in d.items() if k in allowed}

            if "bedrock" in raw and isinstance(raw["bedrock"], dict):
                cfg.bedrock = BedrockConfig(**_only_known(BedrockConfig, raw["bedrock"]))
                yaml_has_bedrock = True
            if "ollama" in raw and isinstance(raw["ollama"], dict):
                cfg.ollama = OllamaConfig(**_only_known(OllamaConfig, raw["ollama"]))
            if "mlx_gemma" in raw and isinstance(raw["mlx_gemma"], dict):
                cfg.mlx_gemma = MLXGemmaConfig(**_only_known(MLXGemmaConfig, raw["mlx_gemma"]))
            if "mlx_qwen" in raw and isinstance(raw["mlx_qwen"], dict):
                cfg.mlx_qwen = MLXQwenConfig(**_only_known(MLXQwenConfig, raw["mlx_qwen"]))
            if "rag" in raw and isinstance(raw["rag"], dict):
                cfg.rag = RagConfig(**_only_known(RagConfig, raw["rag"]))

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

        env_region = os.environ.get("AWS_REGION_NAME") or os.environ.get("AWS_DEFAULT_REGION")
        if env_region:
            cfg.bedrock.region = env_region

        # Fall back to shared LLM_* env vars if no YAML bedrock override
        if not yaml_has_bedrock:
            env_fast = os.environ.get("LLM_FAST_BEDROCK_MODEL")
            env_std = os.environ.get("LLM_STANDARD_BEDROCK_MODEL")
            if env_fast:
                cfg.bedrock.tier1 = env_fast
            if env_std:
                cfg.bedrock.tier2 = env_std

        # tier3d / tier3p env-var overrides (always applied)
        env_tier3d = os.environ.get("DISCOVERY_TIER3D_MODEL")
        env_tier3p = os.environ.get("DISCOVERY_TIER3P_MODEL")
        if env_tier3d:
            cfg.bedrock.tier3d = env_tier3d
        if env_tier3p:
            cfg.bedrock.tier3p = env_tier3p

        return cfg
