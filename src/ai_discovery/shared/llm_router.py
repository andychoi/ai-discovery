"""Shim (M1.10.a): the router moved into the ``ai_discovery.llm`` package.

* settings resolution → ``ai_discovery.llm.settings`` (``settings_from_config``)
* invoke / converse / embeddings → ``ai_discovery.llm.invoke``
* provider lists → ``ai_discovery.llm.providers`` (registry + capability matrix)

The module-global config layer (``configure()``, ``get_config()``,
``save_config()``, ``reset_config()``, ``set_usage_logger()``) is gone: build
``LLMSettings`` from a ``DiscoveryConfig`` and pass it explicitly (``LLMClient``
does this). Only side-effect-free names are re-exported here.
"""

from ai_discovery.llm.invoke import (  # noqa: F401
    converse,
    embed,
    embed_batch,
    invoke_text,
    resolve_tier_target,
    strip_thinking,
)
from ai_discovery.llm.providers import (  # noqa: F401
    PROVIDERS,
    get_provider,
    is_ollama_like as _is_ollama_like,
    is_openai_compat as _is_openai_compat,
    max_tokens_field_for,
)
from ai_discovery.llm.settings import DEFAULTS as _DEFAULTS  # noqa: F401
from ai_discovery.llm.settings import DOC_TYPE_TIER, LLMSettings, settings_from_config  # noqa: F401
