# `ai_discovery.shared`

Small helpers shared across the package. The LLM plumbing that used to live
here (`llm_router`, `llm_invoke`, `model_defaults`) moved to `ai_discovery.llm`
in roadmap items M1.10/M1.11 and the shims were removed.

| Module | Purpose | Main users |
|---|---|---|
| `json_extract.py` | Tolerant JSON extraction from LLM free text (fenced or prose-wrapped) — fallback when tool-use / structured output is unavailable | `ai/llm_client.py`, `ai/screen_spec_generator.py` |
