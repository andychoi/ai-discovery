# `ai_discovery.shared`

LLM plumbing vendored from DocHub and trimmed to what the Discovery CLI uses.
No database code lives here. These modules are scheduled to fold into the
`llm/` package (roadmap items M1.10/M1.11, lane B), after which this package goes away.

| Module | Purpose | Main users |
|---|---|---|
| `llm_invoke.py` | Low-level provider calls (Bedrock Messages/Converse, Ollama, OpenAI-compatible) with retries and token counts; no config or DB dependencies | `llm_router.py`, `ai/llm_client.py` |
| `llm_router.py` | Provider/model resolution (runtime override → `DiscoveryConfig` → env → `model_defaults`) and a usage-logger hook for cost tracking | `ai/llm_client.py`, `pipeline.py`, `config/` |
| `model_defaults.py` | Single source of default model IDs per tier (fast / standard / expert / heavy / embedding) and provider | `config/`, `llm_router.py` |
| `json_extract.py` | Tolerant JSON extraction from LLM free text (fenced or prose-wrapped) — fallback when tool-use / structured output is unavailable | `ai/llm_client.py`, `ai/screen_spec_generator.py` |

Import as `from ai_discovery.shared import llm_router` (it is a normal subpackage, not a `PYTHONPATH` library).
