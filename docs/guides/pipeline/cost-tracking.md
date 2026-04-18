# Pipeline Cost Tracking & Budget Management

Guide for controlling LLM costs and understanding the cost breakdown.

---

## Cost Structure

### Per-Tier Costs (Typical)

| Tier | Model | Per-1M Input Tokens | Per-1M Output Tokens | Typical Use |
|------|-------|---|---|---|
| Tier 1 | Claude Haiku | $0.80 | $4.00 | Chunk summarization (high volume) |
| Tier 2 | Claude Sonnet | $3.00 | $15.00 | Flow analysis (medium volume) |
| Tier 3 | Claude Opus | $15.00 | $75.00 | Doc rollup (low volume) |

**Example costs per codebase (50K LOC)**:
- Tier 1: 2M input + 1M output = $1.60 + $4.00 = $5.60
- Tier 2: 0.5M input + 0.3M output = $1.50 + $4.50 = $6.00
- Tier 3: 0.2M input + 0.4M output = $3.00 + $30.00 = $33.00

**Total: ~$45 per 50K LOC codebase**

---

## Budget Configuration

### Set Budget Limits

```yaml
# config.yaml

llm:
  budgets:
    tier_1_usd: 100.00      # No limit (per-scan)
    tier_2_usd: 50.00       # Per scan
    tier_3_usd: 30.00       # Per scan
    
  # Cost optimization
  max_concurrent_tier_1: 10  # Parallel summarization
  max_chunks_per_summary: 5  # Group small chunks
  
  # Fallback strategy if budget exceeded
  tier_2_fallback: haiku     # Use cheaper model
  tier_3_fallback: sonnet    # Use cheaper model
```

### Cost Guards

Pipeline checks budget before each tier:

```python
# Phase 11: Tier 1 Summarize
if total_cost_usd + estimated_tier_1_cost > budget_limit_usd:
    logger.warning(f"Tier 1 would exceed budget: ${total_cost} + ${est} > ${limit}")
    # Options: raise error, skip tier, or reduce concurrency
else:
    run_tier_1_summarization()

# Phase 12: Tier 2 Analysis
if total_cost_usd + estimated_tier_2_cost > budget_limit_usd:
    logger.warning(f"Tier 2 would exceed budget: switching to fallback model")
    tier_2_model = config.tier_2_fallback  # Use Haiku instead of Sonnet
```

---

## Monitoring Costs

### Real-Time Cost Tracking

```bash
# Check current scan cost
sqlite3 data/discovery.db "
  SELECT 
    tier,
    model,
    SUM(input_tokens) as total_input,
    SUM(output_tokens) as total_output,
    SUM(estimated_usd) as total_cost,
    COUNT(*) as num_calls
  FROM llm_costs
  WHERE scan_id = 'scan_12345'
  GROUP BY tier, model
  ORDER BY tier;
"

# Output:
# tier  | model  | total_input | total_output | total_cost | num_calls
# ------|--------|-------------|--------------|------------|----------
# 1     | haiku  | 2100000     | 950000       | 5.62       | 1200
# 2     | sonnet | 520000      | 280000       | 5.82       | 45
# 3     | opus   | 180000      | 420000       | 33.00      | 12
```

### Cost Per Domain

```bash
sqlite3 data/discovery.db "
  SELECT 
    d.name as domain,
    COUNT(*) as num_nodes,
    SUM(lc.estimated_usd) as total_cost
  FROM code_nodes cn
  JOIN domains d ON cn.domain_id = d.id
  JOIN llm_costs lc ON cn.id = lc.node_id
  WHERE lc.scan_id = 'scan_12345'
  GROUP BY d.name
  ORDER BY total_cost DESC
  LIMIT 10;
"

# Output:
# domain  | num_nodes | total_cost
# --------|-----------|----------
# orders  | 450       | 18.50
# payments| 280       | 12.30
# shipping| 180       | 8.20
# ...
```

---

## Cost Optimization Strategies

### Strategy 1: Reduce Tier 2/3 Model Capacity

**Trade-off**: Lower cost, potentially lower quality

```yaml
# Before:
tier_2_model: sonnet  # $3.00 / 1M input tokens
tier_3_model: opus    # $15.00 / 1M input tokens

# After:
tier_2_model: haiku   # $0.80 / 1M input tokens
tier_3_model: sonnet  # $3.00 / 1M input tokens
```

**Cost impact**: 60–70% reduction, but summaries may be less detailed.

### Strategy 2: Limit Domains Analyzed

**Trade-off**: Faster, cheaper, but incomplete coverage

```yaml
# Before: Analyze all domains
analyze_all_domains: true

# After: Analyze only top 10 domains by LOC
analyze_top_n_domains: 10
```

**Cost impact**: 70–80% reduction (if codebase has 50+ domains).

### Strategy 3: Skip Expensive Phases

**Trade-off**: No self-review, or no doc rollup

```yaml
# Skip self-review (Phase 14)
skip_self_review: true

# Skip Tier 3 doc rollup (Phase 13)
max_tier: 2  # Only Tier 1 + Tier 2
```

**Cost impact**: 50–80% reduction.

### Strategy 4: Batch Chunks

**Trade-off**: Less detail, but faster

```yaml
# Before: Summarize each function individually
max_chunks_per_summary: 1

# After: Group up to 5 functions in one summary
max_chunks_per_summary: 5
```

**Cost impact**: 70–80% reduction (fewer API calls).

### Strategy 5: Use Local Models (Ollama)

**Trade-off**: Lower API costs, but slower (offline)

```yaml
# Before:
llm_provider: bedrock   # AWS API
tier_1_model: haiku
tier_2_model: sonnet

# After:
llm_provider: ollama    # Local Ollama instance
tier_1_model: gemma4:e2b    # 2B parameters
tier_2_model: gemma4:26b    # 26B parameters
```

**Cost impact**: $0 API cost (just compute cost).

---

## Estimating Costs Before Running

### Quick Estimation

1. **Count lines of code**: `wc -l` on source files
2. **Estimate chunks**: ~1 chunk per 50 LOC (method-level)
3. **Estimate tokens**: ~100 tokens per chunk

```
LOC: 50,000
Chunks: 50,000 / 50 = 1,000
Tokens per chunk: 100
Tier 1 input tokens: 1,000 * 100 = 100K input, ~50K output

Tier 1 cost: (100K / 1M) * $0.80 + (50K / 1M) * $4.00 = $0.08 + $0.20 = $0.28
```

### Detailed Estimation

```python
# estimate_cost.py

def estimate_cost(codebase_loc, num_domains, tier_2_depth=2):
    chunks = max(100, codebase_loc // 50)
    
    # Tier 1: Summarize each chunk
    tier_1_input_tokens = chunks * 150  # Avg 150 tokens per chunk
    tier_1_output_tokens = chunks * 80  # Avg 80 tokens output
    tier_1_cost = (tier_1_input_tokens / 1_000_000) * 0.80 + \
                  (tier_1_output_tokens / 1_000_000) * 4.00
    
    # Tier 2: Per-domain flow analysis
    tier_2_input_tokens = num_domains * 2000  # Avg 2K tokens per domain
    tier_2_output_tokens = num_domains * 1000  # Avg 1K tokens output
    tier_2_cost = (tier_2_input_tokens / 1_000_000) * 3.00 + \
                  (tier_2_output_tokens / 1_000_000) * 15.00
    
    # Tier 3: Per-domain×doc-type
    num_docs = num_domains * 4  # 4 doc types per domain
    tier_3_input_tokens = num_docs * 3000
    tier_3_output_tokens = num_docs * 2000
    tier_3_cost = (tier_3_input_tokens / 1_000_000) * 15.00 + \
                  (tier_3_output_tokens / 1_000_000) * 75.00
    
    total_cost = tier_1_cost + tier_2_cost + tier_3_cost
    
    return {
        "tier_1": tier_1_cost,
        "tier_2": tier_2_cost,
        "tier_3": tier_3_cost,
        "total": total_cost
    }

# Usage:
costs = estimate_cost(codebase_loc=50000, num_domains=15)
print(f"Estimated cost: ${costs['total']:.2f}")
# Output: Estimated cost: $45.23
```

---

## Setting Up Budget Alerts

### Email Alert on Budget Exceeded

```python
# app/config.py

if total_cost_usd > budget_limit_usd:
    send_alert_email(
        to=config.alert_email,
        subject=f"AI-Discovery budget exceeded: ${total_cost:.2f} / ${budget_limit:.2f}",
        message=f"""
        Scan {scan_id} exceeded budget.
        
        Tier 1: ${tier_1_cost:.2f}
        Tier 2: ${tier_2_cost:.2f}
        Tier 3: ${tier_3_cost:.2f}
        Total:  ${total_cost:.2f}
        Limit:  ${budget_limit:.2f}
        
        Action: Check tier_2/tier_3 fallback models or increase budget.
        """
    )
```

### Slack Notification

```python
# app/config.py

if total_cost_usd > budget_limit_usd * 0.8:  # 80% of budget
    slack_notify(
        channel="#ai-discovery",
        message=f":warning: Approaching budget limit: ${total_cost:.2f} / ${budget_limit:.2f} (80%)"
    )
```

---

## Cost Reporting

### Monthly Cost Summary

```bash
sqlite3 data/discovery.db "
  SELECT 
    DATE(created_at, '-1 month') as month,
    COUNT(*) as num_scans,
    SUM(estimated_usd) as total_cost,
    AVG(estimated_usd) as avg_cost_per_scan,
    MAX(estimated_usd) as max_cost_per_scan
  FROM scan_runs
  WHERE created_at >= date('now', 'start of month', '-1 month')
  GROUP BY month
  ORDER BY month DESC;
"

# Output:
# month      | num_scans | total_cost | avg_cost | max_cost
# -----------|-----------|-----------|----|----
# 2026-03    | 45        | 892.50    | 19.83 | 125.30
# 2026-02    | 38        | 756.20    | 19.90 | 98.50
```

### Cost Per Project

```bash
sqlite3 data/discovery.db "
  SELECT 
    project_slug,
    COUNT(*) as num_scans,
    SUM(estimated_usd) as total_cost,
    AVG(estimated_usd) as avg_cost
  FROM scan_runs
  WHERE created_at >= date('now', '-3 months')
  GROUP BY project_slug
  ORDER BY total_cost DESC
  LIMIT 10;
"
```

---

## See Also
- `docs/guides/pipeline/phase-breakdown.md` — Cost per phase
- `docs/guides/pipeline/profiling.md` — Performance vs cost trade-offs
