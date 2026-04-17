# Pipeline Profiling & Performance Optimization

Guide for identifying bottlenecks and optimizing pipeline performance.

---

## Quick Performance Check

```bash
# Run pipeline with profiling enabled
python -m app.pipeline scan \
    /path/to/repo \
    --project-slug myproj \
    --profile \
    --verbose

# Output:
# Phase 1 (DB init): 0.5s
# Phase 2 (Resolve repo): 25.3s
# Phase 3 (Resume check): 0.1s
# Phase 4 (Scan run): 0.2s
# Phase 5 (Language detect): 2.1s
# Phase 6 (Parse files): 45.2s
# Phase 7 (Classify domains): 1.8s
# Phase 8 (Persist nodes): 3.2s
# Phase 9 (Build call graph): 28.5s
# Phase 10 (Execution slices): 8.3s
# Phase 11 (Distribute edges): 0.3s
# Phase 12 (Persist domains): 0.8s
# Phase 13 (Chunk & embed): 65.4s
# Phase 14 (Tier 1 summarize): 240s
# Phase 15 (Tier 2 flow): 180s
# Phase 16 (Tier 3 doc rollup): 360s
# Phase 17 (Self-review): 90s
# Phase 18 (Render markdown): 2.1s
# Phase 19 (Finalize): 0.1s
# =====================================
# Total: ~1060s = 17.7 minutes
```

---

## Identifying Bottlenecks

### 1. Parsing Phase (Phase 6)

If **Phase 6 (Parse)** > 10% of total time → Likely bottleneck.

**Symptoms**:
- Parsing is slow on large codebases
- Many files, complex AST structures
- Framework-heavy code (lots of decorators, annotations)

**Optimization**:
```yaml
config.yaml:
  # Reduce scope
  skip_framework_dirs: [vendor, node_modules, .venv, target]
  max_file_size: 100_000  # Skip very large files
  
  # Parallelize
  max_parsing_workers: 8
```

**Cost**: Accuracy may drop slightly (fewer files analyzed).

### 2. Call Graph Resolution (Phase 9)

If **Phase 9 (Call Graph)** > 15% of total time → Likely bottleneck.

**Symptoms**:
- Many unresolved calls
- Complex namespace/package structures
- Lots of dynamic dispatch

**Optimization**:
```yaml
config.yaml:
  # Reduce resolution strictness
  confidence_threshold_for_graph: 0.5  # Include more ambiguous calls
  
  # Parallelize per domain
  max_resolution_workers: 8
```

**Cost**: More low-confidence calls (lower quality flows).

### 3. Embedding Phase (Phase 13)

If **Phase 13 (Chunk & Embed)** > 20% of total time → Likely bottleneck.

**Symptoms**:
- Large codebase (10K+ functions)
- Slow embedding model or API
- Network latency

**Optimization**:
```yaml
config.yaml:
  # Use faster embedding model
  embedding_model: "text-embedding-3-small"  # Faster than Ada
  
  # Batch requests
  embedding_batch_size: 100
  
  # Or: skip embeddings
  skip_embeddings: true  # If RAG search not needed
```

**Cost**: Less semantic search capability, or slower searches.

### 4. LLM Phases (Tier 1, 2, 3)

If **Tier 1/2/3 combined** > 50% of total time → Likely bottleneck.

**Symptoms**:
- Many API calls or slow model
- Rate limiting from LLM provider
- Large context windows

**Optimization**:

**For Tier 1 (Summarization)**:
```yaml
config.yaml:
  max_concurrent_tier_1: 20  # Increase parallelism
  tier_1_batch_size: 5       # Batch small chunks
  tier_1_model: haiku        # Use faster model (already default)
```

**For Tier 2/3**:
```yaml
config.yaml:
  # Skip if not needed
  skip_tier_2: false
  skip_tier_3: false
  
  # Or: use faster models
  tier_2_model: haiku        # Faster, cheaper
  tier_3_model: sonnet       # Instead of opus
```

**Cost**: Quality degrades; less detailed summaries/docs.

---

## Profiling Deep-Dive

### Per-Domain Profiling

Which domains are most expensive?

```bash
sqlite3 data/discovery.db "
  SELECT 
    d.name,
    COUNT(cn.id) as num_nodes,
    SUM(lc.estimated_usd) as total_cost,
    (SUM(lc.estimated_usd) / COUNT(cn.id)) * 1000 as cost_per_1k_nodes
  FROM code_nodes cn
  JOIN domains d ON cn.domain_id = d.id
  LEFT JOIN llm_costs lc ON cn.id = lc.node_id
  WHERE cn.scan_id = 'scan_12345'
  GROUP BY d.name
  ORDER BY total_cost DESC
  LIMIT 10;
"

# Output:
# name     | num_nodes | total_cost | cost_per_1k
# ---------|-----------|------------|----------
# orders   | 450       | 18.50      | 41.11
# payments | 280       | 12.30      | 43.93
# ...
```

### Call Graph Complexity

How many calls, and what's the confidence distribution?

```bash
sqlite3 data/discovery.db "
  SELECT 
    COUNT(*) as total_edges,
    SUM(CASE WHEN confidence >= 0.9 THEN 1 ELSE 0 END) as high_conf,
    SUM(CASE WHEN confidence >= 0.7 AND confidence < 0.9 THEN 1 ELSE 0 END) as med_conf,
    SUM(CASE WHEN confidence < 0.7 THEN 1 ELSE 0 END) as low_conf,
    AVG(confidence) as avg_confidence
  FROM call_edges
  WHERE scan_id = 'scan_12345';
"

# Output:
# total_edges | high_conf | med_conf | low_conf | avg_confidence
# ------------|-----------|----------|----------|---------------
# 1200        | 850       | 250      | 100      | 0.87
```

**Interpretation**:
- High % low_conf edges → call resolution is struggling
- Low avg_confidence → consider adjusting resolution heuristics

### Execution Slice Characteristics

How complex are the scenarios?

```bash
sqlite3 data/discovery.db "
  SELECT 
    scenario_id,
    entry_point,
    LENGTH(primary_path) / 2 as num_steps,  # Rough estimate
    COUNT(*) as num_alternate_paths
  FROM scenario_flows
  WHERE scan_id = 'scan_12345'
  ORDER BY num_steps DESC
  LIMIT 10;
"

# Output:
# scenario_id | entry_point | num_steps | num_alternate
# ------------|---|-----------|----------
# scen_1001   | POST /orders | 8 | 2
# scen_1002   | POST /payments | 5 | 1
# ...
```

**Interpretation**:
- Many steps per scenario → deep execution paths → slower Tier 2/3
- Many alternates → complex conditional logic → harder to analyze

---

## Performance Tuning Checklist

### For Large Codebases (> 100K LOC)

- [ ] **Skip large files**: `max_file_size: 50_000`
- [ ] **Skip vendor directories**: `skip_framework_dirs: [vendor, node_modules, .venv]`
- [ ] **Limit domains**: `analyze_top_n_domains: 20`
- [ ] **Reduce execution slice depth**: `execution_slice_depth: 3`
- [ ] **Increase Tier 1 concurrency**: `max_concurrent_tier_1: 20`
- [ ] **Use faster Tier 2/3 models**: `tier_2_model: haiku`, `tier_3_model: sonnet`

**Expected impact**: 50–70% faster, but less detailed.

### For Real-Time Feedback (Fast Turnaround)

- [ ] **Skip Tier 2/3**: `max_tier: 1` (only summaries)
- [ ] **Skip self-review**: `skip_self_review: true`
- [ ] **Skip embeddings**: `skip_embeddings: true`
- [ ] **Limit domains**: `analyze_top_n_domains: 5`

**Expected impact**: 80–90% faster, but minimal doc generation.

### For Best Quality (Comprehensive Analysis)

- [ ] **Use deepest models**: `tier_3_model: opus`
- [ ] **Increase depth**: `execution_slice_depth: 7`
- [ ] **Enable self-review**: `skip_self_review: false`
- [ ] **Enable embeddings**: `skip_embeddings: false`
- [ ] **No domain limits**: `analyze_all_domains: true`

**Expected impact**: 2–3x slower, but highest quality docs.

---

## Benchmarks: Typical Performance

### Small Codebase (5K LOC, 5 domains)

```
Duration: ~3–5 minutes
Cost: ~$2–4
Phases:
  - Parsing: 5–10s
  - Call graph: 5–10s
  - Tier 1: 1–2 min
  - Tier 2: 1–2 min
  - Tier 3: 0.5–1 min
```

### Medium Codebase (50K LOC, 15 domains)

```
Duration: ~15–20 minutes
Cost: ~$20–30
Phases:
  - Parsing: 30–60s
  - Call graph: 20–40s
  - Tier 1: 3–5 min
  - Tier 2: 5–10 min
  - Tier 3: 2–5 min
```

### Large Codebase (500K LOC, 100+ domains)

```
Duration: ~2–3 hours
Cost: ~$200–500
Phases:
  - Parsing: 5–10 min
  - Call graph: 2–5 min
  - Tier 1: 20–40 min
  - Tier 2: 30–60 min
  - Tier 3: 30–60 min
```

---

## Optimization Experiments

### Experiment 1: Tier 1 Concurrency

**Hypothesis**: Increasing concurrency reduces Tier 1 time.

```bash
# Baseline
python -m app.pipeline scan repo --profile
# Tier 1: 240s

# With increased concurrency
python -m app.pipeline scan repo --profile --max_concurrent_tier_1=20
# Tier 1: 120s

# Result: 50% speedup
```

### Experiment 2: Skip Low-Confidence Edges

**Hypothesis**: Excluding low-confidence edges simplifies Tier 2 analysis.

```bash
# Baseline (all edges)
python -m app.pipeline scan repo --profile
# Tier 2: 180s, num_edges: 1200

# With confidence threshold
python -m app.pipeline scan repo --profile --min_confidence=0.7
# Tier 2: 90s, num_edges: 600

# Result: 50% speedup, but some flows missing
```

---

## Memory Profiling

For very large codebases, memory usage can become a bottleneck.

```bash
# Run with memory profiling
python -m memory_profiler app/pipeline.py scan repo --project-slug myproj --profile

# Output shows memory usage per phase
```

**Memory-hungry phases**:
- **Phase 6 (Parsing)**: Entire codebase in memory (ASTs)
- **Phase 9 (Call Graph)**: Call edge graph
- **Phase 13 (Embedding)**: Vector store (sqlite-vec)

**Optimization**:
```yaml
config.yaml:
  # Process in batches
  processing_batch_size: 100  # Process 100 files at a time
  
  # Clear intermediate results
  clear_parsed_ast_after_phase_6: true
```

---

## Monitoring Ongoing Performance

### Grafana Dashboard

Set up to track:
- Duration per phase
- Cost per scan
- Accuracy metrics (confidence distribution)
- Error rates

```sql
SELECT 
  DATE(created_at) as date,
  AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) as avg_duration_sec,
  AVG(estimated_usd) as avg_cost,
  COUNT(*) as num_scans
FROM scan_runs
GROUP BY DATE(created_at)
ORDER BY date DESC;
```

---

## See Also
- `docs/guides/pipeline/phase-breakdown.md` — Detailed phase information
- `docs/guides/pipeline/cost-tracking.md` — Cost optimization strategies
