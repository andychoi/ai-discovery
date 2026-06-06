"""Discovery LLM client — routes invocation through shared.llm_router.

Adds Discovery-specific cost tracking and tier mapping on top of the shared
multi-provider router (bedrock | ollama | mlx-gemma | mlx-qwen | openai |
gemini | anthropic). Config comes from DiscoveryConfig (YAML > env >
defaults) and is registered with the router via ``llm_router.configure()``.

The Bedrock structured-output path (``invoke_structured``) calls
``converse_bedrock`` directly — it needs a forced ``tool_choice``, which the
router's converse fallback does not expose.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DiscoveryConfig

log = logging.getLogger(__name__)

from ai_discovery.shared import llm_router
from ai_discovery.shared.model_defaults import MODELS
from ai_discovery.shared.llm_invoke import (
    converse_bedrock, warm_ollama, unload_ollama,
)
from ai_discovery.shared.json_extract import extract_json_object

# Discovery tier → router tier. The router's configure() flattening encodes
# the same prod-flag logic as DiscoveryConfig.get_model (expert ≡ tier3p if
# prod else tier3d), so model resolution is identical on both paths.
_ROUTER_TIER = {
    "tier1": "fast",
    "tier2": "standard",
    "tier3": "expert",
    "screen": "standard",  # screen uses the tier2 model (see get_model)
}


@dataclass
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    model: str
    tier: str


@dataclass
class StructuredResponse:
    """Result of a schema-enforced invocation.

    `data` is the parsed JSON object. `via_tool` is True when the model returned
    it through Bedrock tool use (structurally guaranteed to match the schema) and
    False when it came from the text-parsing fallback (Ollama / no tool support).
    """
    data: dict
    tokens_in: int
    tokens_out: int
    model: str
    tier: str
    via_tool: bool = False
    raw_text: str = ""


def _first_tool_input(content_blocks: list[dict], tool_name: str) -> dict | None:
    """Return the input of the first matching toolUse block, or None."""
    for block in content_blocks or []:
        tu = block.get("toolUse") if isinstance(block, dict) else None
        if tu and tu.get("name") == tool_name and isinstance(tu.get("input"), dict):
            return tu["input"]
    return None


def _concat_text(content_blocks: list[dict]) -> str:
    """Concatenate the text of all text blocks in a Converse response."""
    return "".join(
        block["text"] for block in (content_blocks or [])
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )


@dataclass
class AdvisorContext:
    """Context for advisor escalation decisions.

    Enables intelligent escalation based on task complexity, domain, and cost tolerance.
    """
    complexity: str = "auto"          # auto | low | medium | high
    domain: str = ""                  # e.g., "flow_analysis", "claim_extraction"
    max_advisor_cost_pct: float = 0.2 # don't let advisor cost >N% of tier cost
    _retry_count: int = field(default=0, init=False)  # internal: track escalation retries


# Per-domain heuristic thresholds for escalation
# Lower = more readily escalate to advisor
# Higher = only escalate for clearly complex cases
_DOMAIN_THRESHOLDS = {
    "flow_analysis": 1,           # domain-level analysis is inherently complex
    "scenario_steps": 1,          # multi-step scenario inference is complex
    "scenario_ipo": 2,            # structured extraction, moderate complexity
    "scenario_interfaces": 2,     # interface detection, moderate complexity
    "doc_generation": 0,          # always escalate for doc quality
    "claim_extraction": 3,        # structured JSON extraction, only escalate if truly complex
    "claim_verification": 99,     # binary verdict, almost never escalate
    "section_regeneration": 2,    # rewrite only what's needed, moderate
}


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
        """Invoke LLM for given tier — dispatched through shared.llm_router."""
        if self._config.provider not in MODELS:
            raise ValueError(
                f"Unknown provider {self._config.provider!r} — expected one of "
                f"{sorted(MODELS)}"
            )
        router_tier = _ROUTER_TIER.get(tier)
        if router_tier is None:
            raise ValueError(f"Invalid tier {tier!r}, must be one of {sorted(_ROUTER_TIER)}")

        llm_router.configure(self._config)
        meta = llm_router.invoke_llm_with_meta(
            router_tier, prompt, max_tokens,
            enable_thinking=True,  # preserve the pre-router invoke_ollama default
            num_ctx=self._tier1_num_ctx(tier),
        )
        self._track_cost(tier, meta["model_id"], meta["input_tokens"], meta["output_tokens"])
        return LLMResponse(text=meta["text"], tokens_in=meta["input_tokens"],
                           tokens_out=meta["output_tokens"], model=meta["model_id"],
                           tier=tier)

    def invoke_structured(
        self,
        tier: str,
        prompt: str,
        schema: dict,
        *,
        tool_name: str = "emit",
        tool_description: str = "",
        max_tokens: int = 4096,
    ) -> StructuredResponse:
        """Invoke the model and get back a JSON object conforming to *schema*.

        On Bedrock this uses the Converse API with a forced tool choice: the
        model MUST return its answer as the tool's input, which Bedrock validates
        against *schema*. There is no free text to fence-strip or truncate
        mid-object, so the markdown-fence and token-truncation failure modes that
        previously broke screen-spec generation cannot occur.

        On Ollama-compatible providers (no reliable tool use) it falls back to a
        plain prompt plus tolerant JSON extraction — same robustness as before,
        but the fence handling now lives in one shared helper. Raises
        ``json.JSONDecodeError`` only on the fallback path when no JSON is found.
        """
        model = self._config.get_model(tier)

        if self._config.provider == "bedrock":
            tool = {
                "toolSpec": {
                    "name": tool_name,
                    "description": tool_description or "Return the structured result.",
                    "inputSchema": {"json": schema},
                }
            }
            result = converse_bedrock(
                model,
                [{"role": "user", "content": [{"text": prompt}]}],
                max_tokens=max_tokens,
                tools=[tool],
                tool_choice={"tool": {"name": tool_name}},
                region=self._config.bedrock.region,
            )
            tok_in = result["usage"]["input_tokens"]
            tok_out = result["usage"]["output_tokens"]
            self._track_cost(tier, model, tok_in, tok_out)

            data = _first_tool_input(result["content"], tool_name)
            if data is not None:
                return StructuredResponse(
                    data=data, tokens_in=tok_in, tokens_out=tok_out,
                    model=model, tier=tier, via_tool=True,
                )
            # Rare: model emitted text instead of calling the tool. Parse it.
            text = _concat_text(result["content"])
            return StructuredResponse(
                data=extract_json_object(text), tokens_in=tok_in, tokens_out=tok_out,
                model=model, tier=tier, via_tool=False, raw_text=text,
            )

        # Ollama-compatible fallback: plain invoke + tolerant parse.
        resp = self.invoke(tier, prompt, max_tokens)
        return StructuredResponse(
            data=extract_json_object(resp.text), tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out, model=resp.model, tier=tier,
            via_tool=False, raw_text=resp.text,
        )

    def invoke_with_advisor(
        self,
        tier: str,
        prompt: str,
        max_tokens: int = 4096,
        context: AdvisorContext | None = None,
    ) -> LLMResponse:
        """Invoke with intelligent advisor escalation if enabled.

        Routes to native Anthropic SDK (if ANTHROPIC_API_KEY present) or simulated
        advisor (Bedrock/Ollama two-step). Falls back to plain invoke on any error.

        Args:
            tier: tier1, tier2, or tier3
            prompt: the prompt to send to the executor
            max_tokens: max tokens for executor (advisor uses fixed budget)
            context: optional AdvisorContext for complexity/cost control
        """
        context = context or AdvisorContext()
        path = self._get_advisor_provider()
        if path == "disabled" or tier not in self._config.advisor.tiers:
            return self.invoke(tier, prompt, max_tokens)

        # Determine whether to escalate to advisor
        should_advise = self._determine_escalation(tier, prompt, context)

        if not should_advise:
            return self.invoke(tier, prompt, max_tokens)

        # Escalate with retry logic (max 1 retry on error)
        try:
            if path == "anthropic":
                return self._invoke_native_advisor(tier, prompt, max_tokens)
            return self._invoke_simulated_advisor(tier, prompt, max_tokens)
        except Exception as e:
            if context._retry_count < 1:
                log.warning("Advisor failed (retrying): %s", e)
                context._retry_count += 1
                return self.invoke_with_advisor(tier, prompt, max_tokens, context)
            log.warning("Advisor call failed (max retries), falling back: %s", e)
            return self.invoke(tier, prompt, max_tokens)

    def _determine_escalation(self, tier: str, prompt: str, context: AdvisorContext) -> bool:
        """Determine whether to escalate to advisor based on complexity and cost."""
        # Hard complexity signals
        if context.complexity == "high":
            return True
        if context.complexity == "low":
            return False

        # Heuristic escalation (auto or medium)
        if not self._should_escalate_to_advisor(prompt, context.domain):
            return False

        # Cost gate: if advisor cost would exceed threshold, skip it
        if self._config.provider == "bedrock":
            if not self._is_advisor_cost_justified(tier, context.max_advisor_cost_pct):
                log.debug(
                    "Advisor cost exceeds {:.0f}% of tier cost for %s, skipping",
                    context.max_advisor_cost_pct * 100,
                    tier,
                )
                return False

        return True

    def _should_escalate_to_advisor(self, prompt: str, domain: str = "") -> bool:
        """Detect complexity signals in prompt indicating advisor guidance is needed."""
        # Mandatory escalation signals (always advise)
        mandatory_terms = ["architecture", "trade-off", "trade off", "refactor", "reconcile", "conflicting"]
        if any(term in prompt.lower() for term in mandatory_terms):
            return True

        # Get domain-specific threshold
        threshold = _DOMAIN_THRESHOLDS.get(domain, 2)

        # Heuristic scoring
        score = 0

        # Length signal: longer prompts often need planning
        if len(prompt) > 3000:
            score += 2
        elif len(prompt) > 2000:
            score += 1

        # Multi-step signal: branching paths, conditions, alternatives
        multi_step_terms = ["multiple", "different", "various", "approach", "option", "strategy", "alternative"]
        multi_step_count = sum(prompt.lower().count(term) for term in multi_step_terms)
        if multi_step_count >= 3:
            score += 2
        elif multi_step_count >= 1:
            score += 1

        # Ambiguity signal: uncertainty or decision-making
        ambiguity_terms = ["unclear", "ambiguous", "decide", "tradeoff", "trade-off", "uncertain", "competing", "conflicting"]
        if any(term in prompt.lower() for term in ambiguity_terms):
            score += 2

        # Analysis complexity: deep reasoning verbs
        analysis_terms = ["analyze", "compare", "correlate", "infer", "reconcile", "optimize", "identify"]
        analysis_count = sum(prompt.lower().count(term) for term in analysis_terms)
        if analysis_count >= 2:
            score += 1
        elif analysis_count >= 1:
            score += 0  # no boost for single analysis term

        # Domain boost: some domains benefit more from advisor
        if domain in ("flow_analysis", "doc_generation"):
            score += 1

        log.debug(
            "Escalation score for %s: %d (threshold=%d, %s)",
            domain or "unknown", score, threshold, "escalate" if score >= threshold else "skip",
        )
        return score >= threshold

    def _is_advisor_cost_justified(self, tier: str, max_advisor_cost_pct: float) -> bool:
        """Check if advisor cost is justified relative to executor cost."""
        tier_rates = {"tier1": 4.0, "tier2": 15.0, "tier3": 75.0}
        tier_rate = tier_rates.get(tier, 15.0)
        advisor_rate = 75.0

        # Advisor prompt is ~256 tokens, executor output varies by tier
        estimated_advisor_tokens = 256
        estimated_executor_tokens = {"tier1": 512, "tier2": 1024, "tier3": 2048}.get(tier, 1024)

        advisor_cost_estimate = estimated_advisor_tokens / 1_000_000 * advisor_rate
        tier_cost_estimate = estimated_executor_tokens / 1_000_000 * tier_rate

        is_justified = advisor_cost_estimate <= tier_cost_estimate * max_advisor_cost_pct
        log.debug(
            "Cost check %s: advisor ${:.4f} vs {:.0f}%% of tier ${:.4f}",
            "OK" if is_justified else "FAIL",
            advisor_cost_estimate,
            max_advisor_cost_pct * 100,
            tier_cost_estimate,
        )
        return is_justified

    def _get_advisor_provider(self) -> str:
        """Returns 'anthropic', 'simulated', or 'disabled'."""
        cfg = self._config.advisor
        if not cfg.enabled:
            return "disabled"
        if cfg.provider == "disabled":
            return "disabled"
        if cfg.provider == "anthropic":
            return "anthropic"
        if cfg.provider == "simulated":
            return "simulated"
        # auto: native if API key present, else simulated
        return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "simulated"

    def _invoke_native_advisor(self, tier: str, prompt: str, max_tokens: int) -> LLMResponse:
        """Invoke with native Anthropic SDK advisor tool (beta)."""
        try:
            import anthropic as _anthropic
        except ImportError:
            raise RuntimeError(
                "Native advisor requires 'anthropic' package: pip install anthropic"
            )

        cfg = self._config.advisor
        model = (
            cfg.tier3_executor_override if (tier == "tier3" and cfg.tier3_executor_override)
            else self._config.get_model(tier)
        )

        client = _anthropic.Anthropic()
        response = client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            betas=["advisor-tool-2026-03-01"],
            tools=[{
                "type": "advisor_20260301",
                "name": "advisor",
                "model": cfg.model,
                "max_uses": cfg.max_uses_per_call,
            }],
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract text from response content blocks
        text = "".join(
            block.text for block in response.content
            if hasattr(block, "text") and block.type == "text"
        )

        tok_in = response.usage.input_tokens
        tok_out = response.usage.output_tokens

        # Track advisor tokens from iterations breakdown
        for iteration in getattr(response.usage, "iterations", []):
            adv_in = getattr(iteration, "advisor_input_tokens", 0) or 0
            adv_out = getattr(iteration, "advisor_output_tokens", 0) or 0
            if adv_in or adv_out:
                self._track_cost("advisor", cfg.model, adv_in, adv_out)

        self._track_cost(tier, model, tok_in, tok_out)
        return LLMResponse(text=text, tokens_in=tok_in, tokens_out=tok_out, model=model, tier=tier)

    def _invoke_simulated_advisor(self, tier: str, prompt: str, max_tokens: int) -> LLMResponse:
        """Pre-call Opus for strategic plan, inject into executor prompt."""
        advisor_prompt = (
            "You are an expert technical advisor. The following is a task for an AI executor. "
            "Provide a concise numbered plan (under 100 words) describing the best approach. "
            "Focus on structure and strategy only — not execution.\n\n"
            f"<task>\n{prompt[:2000]}\n</task>"  # truncate to avoid bloat
        )

        # Advisor pre-call uses the active tier3 (expert) model on whatever
        # provider is configured — routed through the shared router, so all
        # providers (including openai/gemini/anthropic) get advisor support.
        llm_router.configure(self._config)
        meta = llm_router.invoke_llm_with_meta("expert", advisor_prompt, 256,
                                               enable_thinking=True)
        self._track_cost("advisor", meta["model_id"],
                         meta["input_tokens"], meta["output_tokens"])

        # Inject advisor plan into main prompt
        augmented = f"<advisor_plan>\n{meta['text']}\n</advisor_plan>\n\n{prompt}"
        return self.invoke(tier, augmented, max_tokens)

    def _embedding_provider(self) -> str:
        """Resolve which provider serves embeddings.

        rag.embedding_provider drives the choice: bedrock / openai / gemini are
        honored directly. For ollama-like values, legacy behavior is preserved:
        when the active chat provider is itself ollama-like, embed via the
        active provider's endpoint (mlx servers host their own embedding
        models); otherwise honor the embedding provider as-is (e.g. chat on
        anthropic, embed on local ollama)."""
        ep = self._config.rag.embedding_provider
        if ep in ("bedrock", "openai", "gemini"):
            return ep
        if self._config.provider in self._OLLAMA_LIKE:
            return self._config.provider
        return ep

    def get_embedding(self, text: str) -> list[float]:
        """Get embedding vector — dispatched through shared.llm_router."""
        llm_router.configure(self._config)
        return llm_router.get_embedding(text, provider=self._embedding_provider())

    def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding — one HTTP round-trip per batch where the backend
        supports it. Preserves input order.

        OpenAI-shape backends (Ollama-compatible, OpenAI, Gemini) use the
        native batch endpoint (10–30× less per-request overhead). Bedrock
        Titan has no batch API at the model level, so the router falls back
        to per-text calls (caller still benefits from threaded fan-out at the
        call site)."""
        if not texts:
            return []
        llm_router.configure(self._config)
        return llm_router.get_embeddings_batch(texts, provider=self._embedding_provider())

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
            "screen": (3.0, 15.0),  # Sonnet 4.6 rates (same as tier2)
            "advisor": (15.0, 75.0),  # Opus 4.7 rates
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
