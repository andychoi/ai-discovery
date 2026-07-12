# AI / Anthropic Integration Review

**Date**: 2026-07-12
**Reviewer**: Principal Architect review session
**Scope**: `src/ai_discovery/shared/{llm_router,llm_invoke,model_defaults,json_extract}.py`, `src/ai_discovery/ai/*`, `discovery.yaml`

> **Stated assumption.** The review request asked how "Anthropic Fable" is integrated. There is **no Fable/Mythos integration anywhere in this repository** — the Anthropic integration today targets Haiku 4.5 / Sonnet 4.6 / Opus 4.6–4.8 via Bedrock and the direct Anthropic API. This document therefore (a) reviews the Anthropic/Claude integration as it exists, and (b) recommends where a Fable-class model (`claude-fable-5`) would fit and what API changes its adoption requires. Where uncertain, assumptions are labeled.

---

## 1. Current architecture of the LLM layer

The LLM stack is three layers, cleanly separated by dependency direction:

```
ai/llm_client.py        LLMClient — Discovery-facing: tier mapping (tier1/2/3 →
  (622 lines)           fast/standard/expert), cost ledger, structured output
                        dispatch, advisor escalation, Ollama warm/unload
        │
shared/llm_router.py    Flat-key config resolution (runtime override > YAML >
  (754 lines)           env > defaults), provider dispatch, usage-logger hook,
                        thinking-artifact stripping, doc-type→tier map
        │
shared/llm_invoke.py    Raw transport: boto3 InvokeModel + Converse (Bedrock),
  (609 lines)           anthropic SDK (direct), httpx (Ollama/MLX/OpenAI/Gemini),
                        hand-rolled retry with exponential backoff
```

**Providers**: `bedrock` (boto3), `anthropic` (official SDK), `openai`/`gemini` (OpenAI-compatible HTTP), `ollama`/`mlx-gemma`/`mlx-qwen` (local, free). Default provider is **ollama** (`model_defaults.py:142`), i.e. local-first by default with cloud opt-in.

**Tier design** (the pipeline's core cost lever):

| Discovery tier | Router tier | Default Anthropic model | Used by |
|---|---|---|---|
| tier1 | fast | Haiku 4.5 | Phase 11 chunk summarization, claim extraction |
| tier2 / screen | standard | Sonnet 4.6 | Phase 12 flow analysis, Phase 2 screen specs, Phase 13 scenario inference |
| tier3 (tier3d/tier3p via `--prod`) | expert | Opus 4.6 (Bedrock) / Opus 4.8 (direct) | Phase 14 doc rollup, onboarding guides |
| heavy | heavy | Opus | batch synthesis (rare) |
| embedding | embedding | Titan v2 (Bedrock) / mxbai (Ollama) | Phase 10 RAG |

This is a sound, deliberate architecture. Notable strengths worth preserving:

- **Structured output done right** (`llm_client.py:159-287`): forced `tool_choice` on Bedrock Converse and the Anthropic SDK (`invoke_anthropic_structured`, `llm_invoke.py:527`), `response_format: json_schema` on OpenAI/Gemini, and a single shared tolerant-JSON fallback (`shared/json_extract.py`) for local models. `via_tool` is tracked so callers know whether the schema was enforced.
- **Multi-layered anti-hallucination**: Tier-1 → RAG-verified self-review with abstention on weak evidence (`self_review.py`, `_MAX_VERIFY_DISTANCE`), plus a *deterministic* prose validator (`prose_validator.py`) that flags file/symbol citations absent from the parsed graph. This is genuinely more rigorous than most doc-generation pipelines.
- **Semantic batching** (`semantic_batching.py`): Louvain communities over the confidence-weighted call graph group Tier-1 chunks so one call summarizes code that actually references itself — better context *and* fewer calls. Deterministic seed, loud fallback.
- **Cost governance**: per-tier ledger (`_track_cost`), `budget_limit_usd` guard that halts LLM phases, local providers priced at $0 (fixes phantom-budget aborts), persisted `llm_costs` table.
- **Advisor tool (beta)**: native `advisor_20260301` integration when `ANTHROPIC_API_KEY` is present, with a simulated two-step (plan-then-execute) fallback on other providers, gated by complexity heuristics and a cost ceiling.

## 2. Gaps against current Anthropic API capabilities

These are ranked by expected impact on cost/quality for this workload (a large offline batch pipeline making hundreds-to-thousands of calls per scan).

### 2.1 No prompt caching (highest cost impact)

No `cache_control` anywhere. Every tier builds a single flat user-message string with instructions inlined per call (`summarizer.py`, `flow_analyzer.py`, `rollup.py`, `screen_spec_generator.py`). Tier-1 alone sends the same instruction preamble hundreds of times per scan; Tier-3 re-sends large domain context across the three doc types (`as-is`, `as-is-detail`, `as-is-schema`) of the same domain.

**Fix**: restructure prompts as `system` (stable instructions + schema description, cache-controlled) + `messages` (volatile chunk/domain content), and mark the shared prefix with `cache_control: {"type": "ephemeral"}`. Cache reads bill at ~0.1× input price — for the instruction-heavy Tier-1 workload this is a large fraction of input spend. Caveats: minimum cacheable prefix (1–4K tokens depending on model) and per-model caches; keep the system prompt byte-stable (no timestamps/slugs in it). Note the current `invoke_*` transports accept only a bare prompt string — the transport signatures need a `system=` parameter, which is a small, mechanical change.

### 2.2 No Batches API (50% off everything)

The pipeline is an *offline batch workload* — the textbook case for the Message Batches API (50% discount on all tokens, 100k requests per batch, results within ~1h). Phase 11 (hundreds of independent Tier-1 summaries) and Phase 17 (claim verification) are embarrassingly parallel and latency-insensitive.

Today concurrency is simulated with a `ThreadPoolExecutor` (`max_concurrent: 10`) over synchronous calls — paying full price and fighting rate limits. A `--batch` execution mode on the `anthropic` provider that submits phase-sized batches and polls would halve the cloud LLM bill with no quality change.

**Platform caveat**: Batches are **not available on Amazon Bedrock**. This is a strategic reason to make the direct Anthropic provider (or Claude Platform on AWS, which has same-day API parity including batches, SigV4 auth, and AWS billing) the recommended cloud path, rather than the current boto3 Bedrock path.

### 2.3 Thinking / effort never configured (quality left on the table)

`invoke_anthropic` calls `messages.create` with only `model/max_tokens/messages`. On Opus 4.7/4.8, omitting `thinking` runs **without** thinking — so Phase 14 (the deepest reasoning step in the whole pipeline: multi-document synthesis per domain) runs Opus with reasoning effectively off. Meanwhile `enable_thinking=True` passed down from `LLMClient.invoke` only affects a Qwen-specific Ollama option; it is a no-op for Anthropic models — a misleading parameter.

**Fix**: add `thinking={"type": "adaptive"}` plus `output_config={"effort": ...}` to the Anthropic transport, tiered: `low` for tier1 (bulk extraction — cheaper *and* faster), `high` for tier2, `high`/`xhigh` for tier3 rollups and self-review verdict regeneration. This is the single biggest *quality* lever available for Phase 14 and costs one small code change. (On Bedrock Converse, pass the equivalent `additionalModelRequestFields`.)

### 2.4 `stop_reason` is never inspected

Both `invoke_anthropic` and `invoke_bedrock` extract text and ignore `stop_reason`. Consequences today:

- `max_tokens` truncation is only *guessed* by the heuristic `tok_out >= 0.95 * max_tokens` (`llm_router.py:460`) instead of read from the response.
- A `refusal` stop reason (possible on Opus 4.7+ for cyber-adjacent content — plausible for a security-heavy codebase under scan) yields an empty/partial string that flows silently into a generated doc.

This becomes a hard blocker for Fable adoption (see §3): Fable 5's safety classifiers return HTTP 200 with `stop_reason: "refusal"` and possibly empty content, so the transport must branch on `stop_reason` before using content.

### 2.5 No streaming, small default `max_tokens`

Defaults are 1000–4096 tokens; Tier-3 rollups generate long documents non-streaming. Long Opus generations at high effort risk both truncation and HTTP timeouts (mitigated today only by a 300s boto3 read timeout). Adopt `messages.stream()` + `get_final_message()` for tier3 with `max_tokens` ≥ 16–32K.

### 2.6 Token accounting is estimated, not counted

`len(text) // 4` estimates appear in at least six places (router fallbacks, embedding logging, converse fallback). The `count_tokens` endpoint is never used. The budget guard and the `truncated` heuristic both run on estimates. Low effort to fix where it matters (budget guard inputs); acceptable to keep estimates for local providers.

### 2.7 Pricing table will misprice Fable (latent budget-guard bug)

`MODEL_RATES` (`model_defaults.py:152`) matches by substring — `("opus", 5.0, 25.0)`. `claude-fable-5` contains neither "opus", "sonnet", nor "haiku", so it would fall to the `_DEFAULT_CLOUD_RATE` of $3/$15 — **under-pricing Fable ($10/$50) by ~70%** and defeating the budget guard exactly when the most expensive model is in use. Add explicit entries (`("fable", 10.0, 50.0)`, `("mythos", 10.0, 50.0)`) before any Fable experiment, and prefer exact-prefix matching over substring for new entries.

## 3. Where a Fable-class model fits (adoption plan)

Fable 5 (`claude-fable-5`, $10/$50 per MTok, 1M context, thinking always on) is **not** a drop-in tier upgrade — at 2× Opus pricing it only pays for itself where reasoning depth is the bottleneck. Recommended placement:

| Slot | Recommendation | Rationale |
|---|---|---|
| tier1 (fast) | **No.** Keep Haiku 4.5. | Bulk extraction; Fable is ~10× the input price for no benefit. |
| tier2 (standard) | **No.** Sonnet 4.6 → consider Sonnet 5. | Flow analysis is well within Sonnet-class capability. |
| tier3p (`--prod` deep rollup) | **Yes — this is the slot.** `anthropic.tier3p: claude-fable-5` | Cross-domain synthesis, FSM narrative rollup, and gap analysis are exactly "most demanding reasoning" work. tier3p is already the opt-in premium path, so cost exposure is controlled by the existing `--prod` flag + budget guard. |
| `heavy` (batch synthesis / consistency audits) | **Yes, selectively.** | Long-context cross-artifact consistency checking benefits from the 1M window. |
| advisor model | **Optional.** | Advisor is capped at ~256 output tokens; Opus 4.8 remains the pragmatic advisor. Note the advisor-tool executor/advisor pairing rules if changed. |

**Prerequisites (all currently missing) — treat as a checklist before flipping the config:**

1. **`stop_reason == "refusal"` handling** in `invoke_anthropic` / `invoke_anthropic_structured` (§2.4). Recommended: opt into server-side fallbacks by default — `betas=["server-side-fallback-2026-06-01"]`, `fallbacks=[{"model": "claude-opus-4-8"}]` — so a classifier false-positive on security-adjacent source code degrades to Opus instead of producing an empty doc.
2. **No `thinking` param may be sent** (Fable rejects `{"type": "disabled"}` and `budget_tokens` with a 400; omit or send `adaptive`). The transport currently sends none — safe — but any §2.3 work must special-case Fable.
3. **Pricing entries** in `MODEL_RATES` (§2.7) and a sanity check that `budget_limit_usd` defaults are sized for $10/$50.
4. **Org data-retention check**: Fable requires 30-day retention; ZDR orgs get a 400 on every request. Surface this as a preflight validation with a clear error, not a per-call stack trace.
5. **Timeout headroom**: Fable turns at high effort can run many minutes; the direct-SDK default timeout is 10 min, but tier-3 threads in Phase 14/17 use per-future timeouts (300s in self-review) that would need raising.
6. **Structured output path works unchanged** (forced tool use is supported), but verify `max_tokens` headroom — thinking is always on and shares the output budget.

**Sequencing**: do §2.1–§2.3 (caching, batches, thinking/effort) *first*. They improve the existing Opus/Sonnet pipeline immediately and are prerequisites for evaluating Fable fairly — comparing "Fable without thinking config" against "Opus without thinking config" would measure the wrong thing.

## 4. Architectural smells inside the AI layer

1. **Dual cost ledgers.** `llm_router._log_usage` (pluggable callback) and `LLMClient._track_cost` (in-memory dict → `llm_costs` table) both account usage; `invoke_structured` bypasses the router entirely and only feeds `_track_cost`. One source of truth should own token accounting; the other should subscribe to it.
2. **Global mutable router config + per-call `configure()`.** `llm_router` keeps module-level `_discovery_overrides`/`_runtime_overrides`, and `LLMClient.invoke` calls `llm_router.configure(self._config)` on *every* invocation (`llm_client.py:148`). With `ThreadPoolExecutor` fan-out this is benign only because all threads share one config; two `LLMClient`s with different configs in one process would race. Make the router config an object passed down (or freeze it once at pipeline start) and drop the per-call global mutation.
3. **Three copies of the retry loop.** `invoke_bedrock`, `converse_bedrock`, and `_openai_compat_chat` each hand-roll the same retry/backoff with string-matching transience detection (`_TRANSIENT_PATTERNS` matched against `str(e)`), while the Anthropic SDK path relies on SDK retries. Extract one `with_retries(fn)` helper; match on botocore error codes (`e.response["Error"]["Code"]`) and `httpx` status codes rather than substrings.
4. **`extract_claims` predates its own fix.** `self_review.extract_claims` hand-rolls fence-stripping and truncated-array repair (`self_review.py:72-107`) — precisely the logic `invoke_structured` + `json_extract` were introduced to eliminate. Port it to `invoke_structured` with a `{"claims": [...]}` schema.
5. **Advisor escalation heuristics are keyword-brittle.** `_should_escalate_to_advisor` scores prompts by substring counts ("multiple", "analyze"…) and `_is_advisor_cost_justified` uses hard-coded rates (`tier_rates = {"tier1": 4.0, ...}`) that are already inconsistent with `MODEL_RATES`. Since callers already pass an explicit `domain`, per-domain thresholds carry the real signal — the keyword scoring adds noise. Simplify to domain policy + prompt-length; derive cost estimates from `rates_for_model`.
6. **`strip_thinking` is aggressive regex surgery** on every response (`llm_router.py:47-80`), including stripping `<function_calls>`-style XML to work around a distilled local model. This is applied to *all* providers — a generated doc that legitimately quotes such XML (plausible: this tool documents codebases that may contain LLM code) would be corrupted. Scope the stripping to the Ollama-like path, or to the specific models that need it.
7. **`invoke_llm_with_meta` duplicates `_invoke_text`'s dispatch** almost line-for-line (`llm_router.py:410-469` vs 395-407 + private wrappers) — two parallel provider dispatch chains to keep in sync. One should call the other.
8. **Two "screen" tier notions**: `_ROUTER_TIER["screen"] → "standard"` in `llm_client.py` and a `screen` pseudo-tier in `config.get_model` — the mapping lives in two files and can drift.

## 5. Prioritized recommendations (AI layer only)

| # | Change | Impact | Effort |
|---|---|---|---|
| A1 | Prompt caching: system/user split + `cache_control` on stable prefixes (tier1, tier3, self-review) | Very high (input-cost ↓ up to ~80% on cached prefixes) | M |
| A2 | Batches API mode for phases 11/17 (anthropic provider); document Bedrock's lack of batch support | Very high (50% off batched phases) | M |
| A3 | Adaptive thinking + per-tier `effort` on Anthropic transport | High (tier3 quality; tier1 latency/cost at `low`) | S |
| A4 | `stop_reason` handling (truncation + refusal) with server-side fallbacks | High (correctness; Fable prerequisite) | S |
| A5 | Fix `MODEL_RATES` (Fable/Mythos entries; exact matching) + preflight retention check | High (budget-guard correctness) | S |
| A6 | Fable 5 pilot on `anthropic.tier3p` behind `--prod`, A/B against Opus 4.8 using `generated_docs.confidence` + triage-report deltas | Medium-high (quality ceiling) | S (after A3–A5) |
| A7 | Unify cost ledger; single retry helper; port `extract_claims` to `invoke_structured` | Medium (maintainability) | M |
| A8 | Streaming + larger `max_tokens` for tier3; `count_tokens` for budget inputs | Medium | S |
| A9 | Freeze router config per pipeline run (kill per-call `configure()` global mutation) | Medium (latent concurrency bug) | S |
| A10 | Scope `strip_thinking` to providers that need it | Low (latent corruption bug) | S |

Effort: S ≈ hours–1 day, M ≈ 2–5 days.
