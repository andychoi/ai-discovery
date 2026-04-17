# Advisor Tool Integration — Design Specification

**Date**: 2026-04-17  
**Status**: Implemented  
**Commit**: 9165029

---

## Context

The Anthropic Advisor Tool (beta API feature) allows a faster executor model to consult a higher-intelligence advisor model mid-generation for strategic guidance within a single API request. Benchmarks show Sonnet+Opus-advisor matches or beats Opus-solo at 11.9% lower cost.

**Challenge**: ai-discovery uses AWS Bedrock exclusively, which does not support the advisor tool beta. This specification designs a dual-path architecture that enables native advisor tool when `ANTHROPIC_API_KEY` is available, with graceful fallback to Bedrock-simulated advisory otherwise.

---

## Architecture Overview

### Provider Resolution (auto mode)

```
advisor.enabled: true
advisor.provider: auto
         │
         ▼
ANTHROPIC_API_KEY set?
   ├── yes → native Anthropic SDK path (advisor_20260301 beta tool)
   └── no  → simulated path (Bedrock Opus pre-call → inject plan → executor call)
```

### Components Added

| Component | File | Role |
|-----------|------|------|
| `AdvisorConfig` | `config.py` | Configuration dataclass with tier selection |
| `invoke_with_advisor()` | `llm_client.py` | Public entry point, routes to appropriate path |
| `_get_advisor_provider()` | `llm_client.py` | Determines which implementation to use |
| `_invoke_native_advisor()` | `llm_client.py` | Direct Anthropic SDK with beta header |
| `_invoke_simulated_advisor()` | `llm_client.py` | Two-step Bedrock: advisor pre-call → augmented executor |

---

## Configuration

### AdvisorConfig Fields

```python
@dataclass
class AdvisorConfig:
    enabled: bool = False                  # Master switch
    provider: str = "auto"                 # auto | anthropic | simulated | disabled
    model: str = "claude-opus-4-7"         # Advisor model (native path only)
    tiers: list = ["tier2", "tier3"]       # Which tiers get advisor
    max_uses_per_call: int = 1             # Advisor invocations per request
    tier3_executor_override: str = ""      # e.g. "claude-sonnet-4-6" for cost reduction
```

### YAML Configuration

```yaml
advisor:
  enabled: true
  provider: auto
  model: claude-opus-4-7
  tiers: [tier2, tier3]
  tier3_executor_override: "claude-sonnet-4-6"  # optional, for native path
```

### Environment Variables

- `ANTHROPIC_API_KEY` — enables native Anthropic SDK path (required for native mode)
- `DISCOVERY_LLM_PROVIDER` — existing, controls Bedrock/Ollama/MLX selection
- No new discovery-specific env vars added

---

## Implementation Details

### Native Path (`_invoke_native_advisor`)

**When**: `ANTHROPIC_API_KEY` set AND `provider="anthropic"` or `provider="auto"`

**Flow**:
1. Lazy import `anthropic` SDK (fails with clear error if not installed)
2. Create `Anthropic()` client (reads `ANTHROPIC_API_KEY` from environment)
3. Call `client.beta.messages.create()` with:
   - `model`: executor model (tier3 can override to Sonnet for cost)
   - `betas=["advisor-tool-2026-03-01"]`: beta header
   - `tools`: single advisor tool definition with model=`claude-opus-4-7`
   - `messages`: user prompt
4. Parse response, extract text from content blocks
5. Track advisor tokens from `response.usage.iterations[]`
6. Track executor tokens normally
7. Return combined `LLMResponse`

**Cost Tracking**: Advisor tokens billed at Opus rates (`$15/$75` per 1M).

### Simulated Path (`_invoke_simulated_advisor`)

**When**: `provider="simulated"` OR (`provider="auto"` AND `ANTHROPIC_API_KEY` unset)

**Flow**:
1. Build advisor prompt: "Provide a concise numbered plan (≤100 words)..."
2. Truncate main prompt to first 2000 chars (avoid bloat)
3. Call Bedrock/Ollama Opus with advisor prompt (256 max tokens)
4. Track advisor tokens separately
5. Inject advisor plan: `<advisor_plan>\n{plan}\n</advisor_plan>\n\n{prompt}`
6. Call executor normally with augmented prompt
7. Return executor response

**Rationale**: Limits advisor cost by truncating; executor still sees full prompt as argument.

### Error Handling

**Fallback strategy**: Any exception in advisor path → log warning, fall back to plain `invoke()`.

```python
try:
    if path == "anthropic":
        return self._invoke_native_advisor(tier, prompt, max_tokens)
    return self._invoke_simulated_advisor(tier, prompt, max_tokens)
except Exception as e:
    log.warning("Advisor call failed (%s), falling back to plain invoke: %s", path, e)
    return self.invoke(tier, prompt, max_tokens)
```

**Common failures** and handling:
- `anthropic` package not installed → RuntimeError with install hint → fallback
- `ANTHROPIC_API_KEY` invalid or rate-limited → exception → fallback
- Bedrock timeout in simulated path → exception → fallback
- Ollama unavailable → exception → fallback
- Advisor tier not in `tiers` list → bypass advisor, use plain invoke

---

## Integration Points

### Tier 2 — Flow Analyzer (`ai/flow_analyzer.py`)

**Methods updated**:
- `ScenarioFlowInference._infer_steps()` — domain flow identification
- `ScenarioFlowInference._infer_ipo()` — scenario IPO extraction
- `ScenarioFlowInference._infer_interfaces()` — external interface detection
- `analyze_domain_flows()` — per-domain business flow identification

**Benefit**: Advisor guides Sonnet on domain boundaries and flow naming.

### Tier 3 — Rollup (`ai/rollup.py`)

**Method updated**:
- `_generate_doc()` — per-domain document generation

**Benefit**: With executor override to Sonnet, achieves Opus-level quality at lower cost.

**Optional executor downgrade**:
```yaml
advisor:
  tier3_executor_override: "claude-sonnet-4-6"
```
Executor becomes Sonnet, advisor stays Opus → ~15% cost reduction with comparable quality.

### Tier 1 — Self-Review (`ai/self_review.py`)

**Methods updated**:
- `extract_claims()` — LLM claim extraction from generated doc
- `verify_claim()` — source code verification of claim
- `regenerate_sections()` — rewrite contradicted sections

**Activation**: Opt-in via `advisor.tiers: [tier1, tier2, tier3]` (default is `[tier2, tier3]`).

**Benefit**: Haiku + Opus advisor approximately doubles claim extraction quality (benchmark: 19.7% → 41.2% on BrowseComp).

---

## Cost Tracking

### New Virtual Tier: `"advisor"`

In `_track_cost`, advisor tokens accumulate under a virtual `"advisor"` tier with Opus rates:

```python
cost_per_1m = {
    "tier1": (0.80, 4.0),
    "tier2": (3.0, 15.0),
    "tier3": (15.0, 75.0),
    "advisor": (15.0, 75.0),  # Opus 4.7 rates
}
```

### Budget Guard Behavior

Existing `_budget_ok()` in pipeline automatically includes advisor costs:
```python
total_cost_usd() = sum(all tiers) → includes advisor
if total_cost_usd() >= config.budget_limit_usd: halt pipeline
```

**Example**: Budget=`$50`, Tier1=`$5`, Tier2=`$15`, Advisor=`$28`, Total=`$48` ✓ Within budget.

---

## Optional Dependency

The `anthropic` package is optional. Install with:

```bash
pip install ai-discovery[advisor]
```

Or manually:

```bash
pip install anthropic>=0.50.0
```

**Without package**: Advisor falls back to simulated path (no error, graceful degradation).

---

## Verification Checklist

| Scenario | Command | Expected Result |
|----------|---------|-----------------|
| Advisor disabled (default) | Run pipeline normally | No advisor entries in `llm_costs` table |
| Simulated path | Set `advisor.enabled: true`, unset `ANTHROPIC_API_KEY` | Two Bedrock calls per tier2 invoke (advisor → executor) |
| Native path | Set `ANTHROPIC_API_KEY`, `advisor.enabled: true` | `advisor_tool_result` in logs, single Anthropic API call |
| Budget guard | Set `budget_limit_usd=5`, enable advisor | Pipeline halts counting advisor tokens |
| Tier3 executor downgrade | Set `tier3_executor_override: claude-sonnet-4-6` | Rollup uses Sonnet, advisor is Opus |
| Self-review off (default) | Check Phase 14 logs | No advisor calls (tier1 not in default `tiers`) |
| Self-review on | Set `tiers: [tier1, tier2, tier3]` | Advisor calls in Phase 14 extract_claims/verify_claim |
| Missing package (native forced) | Set `provider: anthropic`, uninstall anthropic | RuntimeError: "Native advisor requires..." |
| Fallback on error | Mock SDK to raise | Warning log, plain invoke result returned |

---

## Design Decisions

1. **Lazy import of `anthropic`** — Module-level import never happens; SDK loaded only when native path activates. Zero install requirement for Bedrock-only users.

2. **Fallback on any error** — Advisor failure never halts pipeline. Degrades gracefully to plain invoke.

3. **`"advisor"` virtual tier** — Reuses existing `_track_cost` infrastructure; budget guard automatically inclusive.

4. **Prompt truncation in simulated path** — Advisor pre-prompt uses first 2000 chars of main prompt to avoid advisor cost exceeding executor cost.

5. **`tiers` list in config** — Per-tier opt-in allows power users to enable tier1 advisory without affecting default pipeline.

6. **No client-side multi-turn logic** — Advisor tool output is ephemeral; no conversation history maintained across calls.

---

## Trade-offs

| Option | Cost | Quality | Implementation |
|--------|------|---------|-----------------|
| **Native (recommended)** | Higher (Opus tokens + Sonnet executor) | High (server-side, coordinated) | SDK + beta header |
| **Simulated** | Lower (single Opus pre-call) | Medium (pre-generated plan) | Two sequential calls |
| **Plain invoke** | Lowest (no advisor) | Lower (single-tier reasoning) | No changes |

---

## Future Enhancements

1. **Prompt caching for advisor** — If 3+ advisor calls per conversation, add `"caching": {"type": "ephemeral"}` to tool definition.

2. **Per-domain advisor configuration** — Allow different advisor models or max_uses by domain for ultra-fine-grained tuning.

3. **Advisor latency metrics** — Log server-side advisor latency from `response.usage` to monitor performance impact.

4. **Cost-benefit analysis** — Collect before/after confidence scores to quantify quality gain per dollar spent on advisor.

---

## References

- Anthropic Blog: [The Advisor Strategy](https://claude.com/blog/the-advisor-strategy)
- Anthropic API Docs: [Advisor Tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool)
- ai-discovery CLAUDE.md: Project architecture and principles
- Commit: 9165029
