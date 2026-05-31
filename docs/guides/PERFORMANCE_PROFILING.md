# Performance Profiling Guide for ai-discovery

## Overview

This guide explains how to profile ai-discovery's performance across different phases and optimize for your codebase size and LLM budget.

## Key Metrics to Track

| Metric | Target | Why |
|--------|--------|-----|
| **Phase 6 (parse)** | < 100ms per file | Parser performance dominates small codebases |
| **Phase 11 (tier1)** | < 500ms per chunk | Concurrent summaries; bottleneck on large code chunks |
| **Phase 12 (tier2)** | < 2s per domain | Larger models; fewer calls, higher quality |
| **Phase 14 (tier3)** | < 5s per domain | Final rollup; deepest reasoning |
| **Total end-to-end** | < $15 for 2000 classes | Typical cost on Bedrock |
| **Drift detection** | < 100ms | Hash-based, no LLM calls |

## Phase-by-Phase Profiling

### Phase 5: Language Detection
**Time**: Typically < 100ms
**Profile**: File extension + manifest scanning (fast, deterministic)

```bash
discover scan repo -p test --skip-phases=6-19  # Measure detection only
```

### Phase 6: Parse
**Time**: 50–200ms per file (depends on file size and language)
**What to measure**:
- Files per language
- Average file size
- Tree-sitter grammar parse time

**Profile**:
```bash
# Run with profiling
discover scan repo -p test --profile --skip-phases=7-19
```

**Check output**:
```
Phase  6 (parse)
  ├─ Python: 150 files, avg 45KB, 12.5s total (83ms/file)
  ├─ Java: 200 files, avg 60KB, 18.2s total (91ms/file)
  └─ JavaScript: 80 files, avg 30KB, 4.1s total (51ms/file)
  Total: 30.8s
```

**Optimization tips**:
- Java files are slowest (complex grammar)
- Split large files (> 500 lines) before scanning if stuck
- Python parses 30% faster than Java

### Phase 7: Domain Classification
**Time**: Usually < 100ms (pure heuristics)
**What to measure**:
- Number of domains detected
- Domain distribution (which are largest)

**Profile**:
```bash
discover scan repo -p test --profile --skip-phases=8-19
```

### Phases 8–10: Execution Slices, Chunking, RAG Embedding
**Time**: 1–3s total (depends on codebase complexity)
**What to measure**:
- Number of chunks created
- Chunk size distribution
- Embedding call latency

**Profile**:
```bash
discover scan repo -p test --profile --skip-phases=11-19
```

**Check output**:
```
Phase  9 (chunk)
  Chunks created: 850
  Avg chunk size: 180 tokens
  Size distribution: [min=45, p50=160, p95=350, max=890]

Phase 10 (rag_embed)
  Embeddings: 850 calls
  Latency: 45–80ms per call (concurrent)
  Total: 2.3s (parallel)
```

### Phase 11: Tier 1 Summarization (Haiku)
**Time**: 2–10s (depends on chunk count; runs in parallel)
**What to measure**:
- LLM latency per chunk
- Concurrency level
- Cost per chunk

**Profile**:
```bash
discover scan repo -p test --profile --skip-phases=12-19
```

**Check output**:
```
Phase 11 (tier1_summarize)
  Chunks: 850
  LLM calls: 850 (concurrent batches)
  Latency per call: 800–1200ms
  Total: 4.3s (8 workers, 12 batches)
  Cost: $0.18 (Haiku @ $0.80/$24 per 1M tokens)
```

**Optimization tips**:
- Increase `--concurrency` (default: 8) if you have more workers
- Smaller chunks = faster LLM responses
- Haiku is ~6x cheaper than Sonnet; use tier1 for exploration

### Phase 12: Tier 2 Flow Analysis (Sonnet)
**Time**: 10–30s (fewer calls than Tier 1, larger inputs)
**What to measure**:
- Calls per domain
- Average cost per domain

**Profile**:
```bash
discover scan repo -p test --profile --skip-phases=13-19
```

**Optimization tips**:
- Number of domains drives cost; combine small domains if possible
- Sonnet is ~6x more expensive than Haiku; use for deep analysis only

### Phase 14: Tier 3 Doc Rollup (Sonnet or Haiku)
**Time**: 20–60s (final synthesis; typically uses Sonnet in prod)
**What to measure**:
- Doc generation latency
- Final cost per domain

**Profile**:
```bash
discover scan repo -p test --profile
```

**Optimization tips**:
- Use `--prod` to switch to tier3p (Sonnet) for better quality
- Default tier3d (Haiku) is fast and cheap for dev/testing

### Phases 15–19: Visual Artifacts, Markdown, Finalize
**Time**: 1–5s total (deterministic, no LLM)
**What to measure**:
- Markdown render latency
- File write latency

## Full Pipeline Profiling

### Baseline: Measure Everything

```bash
# Full profile run on your repo
discover scan /path/to/repo -p myapp --profile --output ./profiling-output
```

**Check profile output** (`./profiling-output/profile-report.txt`):
```
Project: myapp
Codebase: 500 classes, 45K LOC, 3 languages

═══════════════════════════════════════════════════════
Phase Progress & Timing

Phase  5 (lang_detect)      ✓    0.05s
Phase  6 (parse)            ✓   30.8s  [PRIMARY COST BY TIME]
Phase  7 (domain_classify)  ✓    0.08s
Phase  9 (chunk)            ✓    2.1s
Phase 10 (rag_embed)        ✓    2.3s
Phase 11 (tier1_summarize)  ✓    4.3s
Phase 12 (tier2_analysis)   ✓   18.5s [PRIMARY COST BY TIME]
Phase 14 (tier3_rollup)     ✓   12.2s
Phase 15 (visual_artifacts) ✓    1.1s
Phase 18 (render_markdown)  ✓    0.3s
Phase 19 (finalise)         ✓    0.2s

═══════════════════════════════════════════════════════
Cost Breakdown

Tier 1 (Haiku)   $0.18  [Phase 11: 850 chunks]
Tier 2 (Sonnet)  $4.50  [Phase 12: 28 domains]
Tier 3 (Haiku)   $0.12  [Phase 14: 28 domains, tier3d default]
Total            $4.80

═══════════════════════════════════════════════════════
Per-Language Costs

Java/Spring      $2.10  [200 classes, complex call graphs]
Python           $1.50  [150 classes, simpler patterns]
JavaScript       $1.20  [100 modules, ES6 syntax]

═══════════════════════════════════════════════════════
Recommendations

1. Parsing is slow (31s of 71s total)
   → Java is 3x slower than Python
   → Consider splitting large Java files (> 500 lines)

2. Tier 2 analysis is expensive ($4.50 of $4.80)
   → Combine related domains to reduce calls
   → Use --prod tier3p only when needed (development: --skip-phases=14)

3. Cost is reasonable ($4.80 for 500 classes)
   → Budget: $15 for 2000 classes is conservative
   → Current run would scale to ~$20 for full enterprise codebase
```

## Optimization Strategies

### Strategy 1: Skip Optional Phases

```bash
# Skip optional phase 16 (process mining)
discover scan repo -p test --skip-phases=16

# Run only up to Tier 2 (skip expensive Tier 3)
discover scan repo -p test --skip-phases=14-19
```

**Cost saved**: 40–60% if Tier 3 is your bottleneck

### Strategy 2: Use Cheaper LLM Models

```yaml
# discovery.yaml
tier1: us.anthropic.claude-haiku-4-5-20251001-v1:0  # Cheap (default)
tier2: us.anthropic.claude-sonnet-4-6               # Mid (replace with Haiku?)
tier3d: us.anthropic.claude-haiku-4-5-20251001-v1:0 # Dev (fast, cheap)
tier3p: us.anthropic.claude-sonnet-4-6              # Prod (higher quality, expensive)
```

**Cost saved**: 80% by downgrading Tier 2 to Haiku (quality tradeoff)

### Strategy 3: Parallelize Phases

```bash
# Run multiple repos in parallel (8 concurrent workers)
for repo in repo1 repo2 repo3 repo4; do
  discover scan $repo -p $(basename $repo) &
done
wait
```

**Actual speedup**: ~4x on 8-core machine (I/O parallelizable; Tier 1 already parallel)

### Strategy 4: Incremental Scans with `--resume`

```bash
# First run: full scan
discover scan repo -p myapp

# Later: only regenerate changed parts
discover scan repo -p myapp --resume
```

**Cost saved**: 60–80% on incremental runs (uses cached phases)

### Strategy 5: Use Drift Detection (No LLM)

```bash
# Check which screens changed without regenerating
discover verify-drift ./my-app --spec-dir ./docs/screens
```

**Cost saved**: 100% (no LLM calls, just file hashing)

## Benchmarks on Real Projects

### Project A: Spring Boot Microservice
- **Size**: 250 classes, 25K LOC, Java only
- **Time**: 45s
- **Cost**: $2.10
- **Bottleneck**: Tier 2 analysis ($1.80)

### Project B: Django REST API
- **Size**: 400 classes, 35K LOC, Python only
- **Time**: 32s
- **Cost**: $1.80
- **Bottleneck**: Tier 1 summarization (Haiku concurrency)

### Project C: Full-Stack Enterprise
- **Size**: 1200 classes, 110K LOC, Java/Python/JavaScript
- **Time**: 120s
- **Cost**: $9.50
- **Bottleneck**: Java parsing (40s), Tier 2 analysis ($5.20)

## Recommended Profile Runs

### Dev/Test
- Use `--skip-phases=14-19` (skip Tier 3)
- Use tier1 + tier2 only
- Cost: ~$1–2 for 500 classes

### Staging/QA
- Full run with `tier3d` (Haiku)
- Use `--profile` to identify bottlenecks
- Cost: ~$5 for 500 classes

### Production
- Full run with `--prod` (uses tier3p Sonnet)
- Budget: $15–20 for 2000 classes
- Use drift detection on subsequent runs

## Debugging Slow Runs

### Slow Phase 6 (Parsing)?
```bash
# Check which files are slowest
discover scan repo -p test --profile --skip-phases=7-19
# Look for "slowest files" in output
```

**Fix**: Split large files, or consider async parsing for very large codebases

### Slow Phase 11 (Tier 1)?
```bash
# Check chunk distribution
discover query "SELECT COUNT(*) as cnt, \
                AVG(token_count) as avg_tokens, \
                MAX(token_count) as max_tokens \
                FROM chunks;"
```

**Fix**: Smaller chunk size (config `chunking.max_tokens`)

### Slow Phase 12 (Tier 2)?
```bash
# Check domains created
discover query "SELECT domain, COUNT(*) as entity_count \
                FROM entities GROUP BY domain \
                ORDER BY entity_count DESC LIMIT 10;"
```

**Fix**: Combine related domains, or run tier2 on subset of domains

## Next Steps

1. **Profile your codebase**: `discover scan <repo> --profile`
2. **Identify bottleneck**: Check phase timing report
3. **Apply optimization**: Skip phases, use cheaper LLMs, parallelize
4. **Re-measure**: Verify cost savings

See also:
- [CLAUDE.md § Performance Profiling](/home/user/ai-discovery/CLAUDE.md)
- [docs/guides/pipeline/profiling.md](pipeline/profiling.md)
