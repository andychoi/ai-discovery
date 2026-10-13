"""Provider registry and capability matrix (roadmap item M1.10.a).

One ``ProviderSpec`` per provider name accepted in ``provider:``. The spec
says which wire protocol the provider speaks and what it can do; the invoke
path (``llm/invoke.py``) and ``LLMClient`` branch on these fields instead of
on provider-name lists scattered through the code.

Capability columns:
* ``protocol`` — ``bedrock`` | ``anthropic`` | ``openai_compat`` | ``ollama``
* ``structured`` — how schema-enforced output is obtained: ``tool`` (forced
  tool use: Bedrock Converse, Anthropic), ``json_schema`` (OpenAI
  ``response_format``), ``prompt`` (plain prompt + tolerant JSON extraction)
* ``max_tokens_field`` — output-cap parameter name on the wire
* ``temperature`` — ``any`` or ``default_only`` (OpenAI gpt-5/o-series accept
  only the default; nothing in Discovery sets a temperature today, recorded so
  callers that start doing so check it)
* ``timeout_class`` — ``cloud`` (300 s read) or ``local`` (600 s, env-tunable)
* ``embeddings`` — has an embeddings API; ``metered`` — costs money per token
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    protocol: str
    default_url: str = ""
    api_key_env: str = ""
    max_tokens_field: str = "max_tokens"
    structured: str = "prompt"
    temperature: str = "any"
    timeout_class: str = "cloud"
    embeddings: bool = True
    metered: bool = True
    url_env: str = ""

    @property
    def model_suffix(self) -> str:
        """Flat settings-key suffix for this provider's models (``tier.<t>.<suffix>``)."""
        return f"{self.name}_model"

    @property
    def url_key(self) -> str:
        return f"{self.name}.url"


_SPECS = (
    ProviderSpec("bedrock", "bedrock", structured="tool"),
    ProviderSpec("anthropic", "anthropic", default_url="https://api.anthropic.com",
                 api_key_env="ANTHROPIC_API_KEY", structured="tool", embeddings=False,
                 url_env="ANTHROPIC_BASE_URL"),
    ProviderSpec("openai", "openai_compat", default_url="https://api.openai.com/v1",
                 api_key_env="OPENAI_API_KEY", max_tokens_field="max_completion_tokens",
                 structured="json_schema", temperature="default_only", url_env="OPENAI_BASE_URL"),
    ProviderSpec("gemini", "openai_compat",
                 default_url="https://generativelanguage.googleapis.com/v1beta/openai",
                 api_key_env="GEMINI_API_KEY", structured="json_schema", url_env="GEMINI_BASE_URL"),
    ProviderSpec("aigw", "openai_compat", default_url="http://localhost:11433/v1",
                 api_key_env="AIGW_API_KEY", structured="json_schema", url_env="AIGW_BASE_URL"),
    ProviderSpec("omlx", "openai_compat", default_url="http://localhost:11434/v1",
                 api_key_env="OMLX_API_KEY", structured="json_schema", timeout_class="local",
                 metered=False, url_env="OMLX_BASE_URL"),
    ProviderSpec("openrouter", "openai_compat", default_url="https://openrouter.ai/api/v1",
                 api_key_env="OPENROUTER_API_KEY", structured="json_schema",
                 url_env="OPENROUTER_BASE_URL"),
    ProviderSpec("ollama", "ollama", timeout_class="local", metered=False, url_env="OLLAMA_URL"),
    ProviderSpec("mlx-gemma", "ollama", default_url="http://localhost:11435",
                 api_key_env="MLX_API_KEY", timeout_class="local", metered=False,
                 url_env="MLX_SERVER_URL"),
    ProviderSpec("mlx-qwen", "ollama", default_url="http://localhost:11435",
                 api_key_env="MLX_API_KEY", timeout_class="local", metered=False,
                 url_env="MLX_SERVER_URL"),
)
PROVIDERS: dict[str, ProviderSpec] = {s.name: s for s in _SPECS}


def get_provider(name: str) -> ProviderSpec:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(f"unknown LLM provider {name!r}; expected one of {sorted(PROVIDERS)}") from None


def is_openai_compat(name: str) -> bool:
    return name in PROVIDERS and PROVIDERS[name].protocol == "openai_compat"


def is_ollama_like(name: str) -> bool:
    return name in PROVIDERS and PROVIDERS[name].protocol == "ollama"


def max_tokens_field_for(name: str) -> str:
    """Output-cap parameter name (gpt-5 family needs ``max_completion_tokens``)."""
    return get_provider(name).max_tokens_field
