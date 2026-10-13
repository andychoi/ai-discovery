"""LLM cost accounting (roadmap item M1.11.a).

``Usage`` carries every billable token class a provider reports: uncached
input, output, cache reads and cache writes. ``price_for(model, provider)``
returns per-1M-token prices: input/output from the catalog's per-model table
(``catalog.rates_for_model``), cache prices from per-family multipliers of the
input price. ``cost_usd`` sums all four.

Cache multipliers:
* Anthropic Claude (direct, Bedrock, OpenRouter ids): write 1.25× input (5-minute
  cache), read 0.1× input — per Anthropic's prompt-caching pricing.
* OpenAI gpt-5 family: read 0.1× input (no write surcharge) — (Unverified,
  OpenAI pricing page not re-checked for this release).
* Gemini: read 0.25× input — (Unverified).
* Local providers: everything $0. Unknown cloud models: cache priced as input
  (conservative, so budget guards don't under-count).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .catalog import _LOCAL_PROVIDERS, rates_for_model


@dataclass
class Usage:
    input_tokens: int = 0          # uncached input
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens,
                     self.cache_read_tokens + other.cache_read_tokens,
                     self.cache_write_tokens + other.cache_write_tokens)


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float


def _family(model: str) -> str:
    m = model.lower()
    if any(k in m for k in ("claude", "opus", "sonnet", "haiku")):
        return "anthropic"
    if "gpt-" in m:
        return "openai"
    if "gemini" in m:
        return "gemini"
    return "other"


_CACHE_MULT = {"anthropic": (0.10, 1.25), "openai": (0.10, 1.0), "gemini": (0.25, 1.0), "other": (1.0, 1.0)}


def price_for(model: str, provider: Optional[str] = None) -> Price:
    if provider in _LOCAL_PROVIDERS:
        return Price(0.0, 0.0, 0.0, 0.0)
    in_rate, out_rate = rates_for_model(model, provider)
    read_mult, write_mult = _CACHE_MULT[_family(model)]
    return Price(in_rate, out_rate, in_rate * read_mult, in_rate * write_mult)


def cost_usd(usage: Usage, price: Price) -> float:
    return (usage.input_tokens * price.input + usage.output_tokens * price.output
            + usage.cache_read_tokens * price.cache_read
            + usage.cache_write_tokens * price.cache_write) / 1_000_000
