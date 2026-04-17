"""Discovery LLM client — thin wrapper over shared.llm_router invoke functions.

Adds Discovery-specific cost tracking on top of the shared Bedrock/Ollama
invoke layer.  Config comes from DiscoveryConfig (YAML > env > defaults).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import DiscoveryConfig

try:
    from ai_discovery.shared.model_defaults import MODELS
except ImportError:
    from ai_discovery.shared.model_defaults import MODELS

# llm_invoke has no DB dependencies — safe to import in both Docker and local.
#   Docker (PYTHONPATH=/app): 'shared' is a top-level package
#   Local (python -m app): 'app.shared' is the correct path
try:
    from ai_discovery.shared.llm_invoke import (
        invoke_bedrock, invoke_ollama, embed_bedrock, embed_ollama,
        warm_ollama, unload_ollama,
    )
except ImportError:
    from ai_discovery.shared.llm_invoke import (
        invoke_bedrock, invoke_ollama, embed_bedrock, embed_ollama,
        warm_ollama, unload_ollama,
    )


@dataclass
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    model: str
    tier: str


class LLMClient:
    def __init__(self, config: DiscoveryConfig):
        self._config = config
        self._costs: dict[str, dict] = {}

    _OLLAMA_LIKE = frozenset({"ollama", "mlx-gemma", "mlx-qwen"})

    def _tier1_num_ctx(self, tier: str) -> int | None:
        """Tier1 summarizes many small chunks — cap context window to avoid
        pre-allocating a 32K KV cache per call. Returns None for other tiers."""
        if tier != "tier1":
            return None
        p = self._config.provider
        if p == "ollama":
            return self._config.ollama.tier1_num_ctx or None
        if p == "mlx-gemma":
            return self._config.mlx_gemma.tier1_num_ctx or None
        if p == "mlx-qwen":
            return self._config.mlx_qwen.tier1_num_ctx or None
        return None

    def invoke(self, tier: str, prompt: str, max_tokens: int = 4096) -> LLMResponse:
        """Invoke LLM for given tier. Routes to Bedrock or Ollama-compatible."""
        model = self._config.get_model(tier)
        if self._config.provider == "bedrock":
            text, tok_in, tok_out = invoke_bedrock(
                model, prompt, max_tokens, self._config.bedrock.region,
            )
        elif self._config.provider in self._OLLAMA_LIKE:
            base_url, api_key = self._config.get_endpoint()
            text, tok_in, tok_out = invoke_ollama(
                model, prompt, max_tokens, base_url,
                num_ctx=self._tier1_num_ctx(tier),
                api_key=api_key,
            )
        else:
            raise ValueError(
                f"Unknown provider {self._config.provider!r} — expected "
                f"'bedrock', 'ollama', 'mlx-gemma', or 'mlx-qwen'"
            )

        self._track_cost(tier, model, tok_in, tok_out)
        return LLMResponse(text=text, tokens_in=tok_in, tokens_out=tok_out, model=model, tier=tier)

    def get_embedding(self, text: str) -> list[float]:
        """Get embedding vector."""
        if self._config.rag.embedding_provider == "bedrock":
            return embed_bedrock(
                self._config.rag.bedrock_model, text, self._config.bedrock.region,
            )
        # Ollama-compatible: use the active provider's endpoint
        base_url, api_key = self._config.get_endpoint()
        model = self._embedding_model_for(self._config.provider)
        return embed_ollama(model, text, base_url, api_key=api_key)

    def _embedding_model_for(self, provider: str) -> str:
        """Return the embedding model ID for the active ollama-compatible provider."""
        if provider in ("mlx-gemma", "mlx-qwen"):
            return MODELS.get(provider, {}).get("embedding", self._config.rag.ollama_model)
        return self._config.rag.ollama_model

    # ── Ollama keep-alive helpers (no-op for Bedrock) ────────────────────
    def warm(self, tier: str, keep_alive: str = "30m") -> bool:
        """Pre-load a tier model into memory. No-op for Bedrock."""
        if self._config.provider not in self._OLLAMA_LIKE:
            return True
        base_url, api_key = self._config.get_endpoint()
        return warm_ollama(
            self._config.get_model(tier), base_url, keep_alive, api_key=api_key,
        )

    def warm_embedding(self, keep_alive: str = "30m") -> bool:
        """Pre-load the embedding model into memory. No-op for Bedrock."""
        if self._config.rag.embedding_provider == "bedrock":
            return True
        if self._config.provider not in self._OLLAMA_LIKE:
            return True
        base_url, api_key = self._config.get_endpoint()
        return warm_ollama(
            self._embedding_model_for(self._config.provider), base_url, keep_alive,
            api_key=api_key,
        )

    def unload(self, tier: str) -> bool:
        """Evict a tier model from memory (keep_alive=0). No-op for Bedrock."""
        if self._config.provider not in self._OLLAMA_LIKE:
            return True
        base_url, _ = self._config.get_endpoint()
        return unload_ollama(self._config.get_model(tier), base_url)

    def get_costs(self) -> dict[str, dict]:
        return dict(self._costs)

    def total_cost_usd(self) -> float:
        return sum(t.get("est_usd", 0) for t in self._costs.values())

    def _track_cost(self, tier: str, model: str, tokens_in: int, tokens_out: int):
        if tier not in self._costs:
            self._costs[tier] = {
                "calls": 0, "tokens_in": 0, "tokens_out": 0,
                "est_usd": 0.0, "model": model,
            }
        entry = self._costs[tier]
        entry["calls"] += 1
        entry["tokens_in"] += tokens_in
        entry["tokens_out"] += tokens_out
        cost_per_1m = {
            "tier1": (0.80, 4.0), "tier2": (3.0, 15.0), "tier3": (15.0, 75.0),
        }
        in_rate, out_rate = cost_per_1m.get(tier, (3.0, 15.0))
        entry["est_usd"] = (
            entry["tokens_in"] / 1_000_000 * in_rate
            + entry["tokens_out"] / 1_000_000 * out_rate
        )

    def persist_costs(self, scan_id: int, db_path: Path):
        from ..db import get_conn

        conn = get_conn(db_path)
        for tier, data in self._costs.items():
            conn.execute(
                """INSERT OR REPLACE INTO llm_costs
                   (scan_id, tier, model, calls, tokens_in, tokens_out, est_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (scan_id, tier, data.get("model", ""),
                 data["calls"], data["tokens_in"], data["tokens_out"], data["est_usd"]),
            )
        conn.commit()
        conn.close()
