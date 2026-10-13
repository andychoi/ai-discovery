"""LLM access for ai-discovery (roadmap M1.10, ADR / design architecture-v1).

* ``providers`` — provider registry + capability matrix
* ``settings``  — ``LLMSettings`` built explicitly from a ``DiscoveryConfig``
* ``invoke``    — the one invoke path (text, converse, embeddings)
* ``transport`` — wire calls per protocol (Bedrock, Anthropic, OpenAI-compatible, Ollama)
* ``catalog``   — default model ids per provider/tier and token rates

Roles: callers ask for a role — ``fast``, ``standard`` or ``deep`` — which maps
onto today's Discovery tiers (tier1, tier2/screen, tier3). The config keys move
to ``models.fast|standard|deep`` in M1.10.b.
"""

from __future__ import annotations

from .invoke import converse, embed, embed_batch, invoke_text, resolve_tier_target, strip_thinking
from .providers import PROVIDERS, ProviderSpec, get_provider, is_ollama_like, is_openai_compat, max_tokens_field_for
from .settings import LLMSettings, settings_from_config

ROLES = ("fast", "standard", "deep")
# Discovery tier → role → router tier (the router's "expert" is the deep role).
TIER_ROLE = {"tier1": "fast", "tier2": "standard", "screen": "standard", "tier3": "deep"}
ROLE_ROUTER_TIER = {"fast": "fast", "standard": "standard", "deep": "expert"}


def role_for_tier(tier: str) -> str:
    try:
        return TIER_ROLE[tier]
    except KeyError:
        raise ValueError(f"Invalid tier {tier!r}, must be one of {sorted(TIER_ROLE)}") from None


__all__ = [
    "LLMSettings", "PROVIDERS", "ProviderSpec", "ROLES", "ROLE_ROUTER_TIER", "TIER_ROLE", "converse",
    "embed", "embed_batch", "get_provider", "invoke_text", "is_ollama_like", "is_openai_compat",
    "max_tokens_field_for", "resolve_tier_target", "role_for_tier", "settings_from_config", "strip_thinking",
]
