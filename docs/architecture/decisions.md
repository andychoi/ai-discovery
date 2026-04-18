# Design Decisions

This document explains *why* we chose specific heuristics and trade-offs in AI-Discovery.

---

## Call Graph Resolution: 7-Level Confidence Scoring

### Decision
Multi-signal confidence scoring (7 levels: exact → prefix → suffix → external → unresolved) instead of boolean match/no-match.

### Why
- **Binary resolution is fragile**: A function might be callable even if we can't find it—it might be dynamically imported, passed as argument, or in an unmapped namespace.
- **Confidence separates concerns**: Instead of asking "did we find it?", we ask "how confident are we?". The LLM pipeline can then decide: use high-confidence calls in BPMN, flag low-confidence calls for human review.
- **Real codebases are messy**: Exact qualified-name matches work in ~60% of cases. For the remaining 40%, we need fallback heuristics.

### Scoring Strategy

| Level | Condition | Confidence | When Useful |
|-------|-----------|-----------|------------|
| 1 | Exact qualified_name match | 1.0 | Same module, same class or global namespace |
| 2 | Same class owner prefix | 0.95 | Method calls within same class |
| 3 | Same file, unique in file | 0.90 | Local functions in same file |
| 4 | Same module, unique in module | 0.85 | Module-level functions |
| 5 | Unique suffix match | 0.85 | Uncommon function names (e.g., `validateOrderPayment`) |
| 6 | Best prefix overlap (>50%) | 0.65–0.75 | Namespace partial match, ambiguous |
| 7 | Ambiguous short-name / unresolved | 0.50 | External call or dynamic dispatch |

### Trade-offs
- **Pro**: Captures real-world messy code; separates what we know from what we guess
- **Con**: Requires human judgment on which threshold to use; no single "right answer"

### Future Tuning
As we analyze more code, we can refine thresholds based on observed accuracy. For now, **confidence ≥ 0.8** is good for BPMN, **0.6–0.8** is worth manual review, **< 0.6** is likely external/unresolved.

---

## Execution Slice Depth Limit: BFS ≤ 5 Levels

### Decision
Bounded breadth-first search with depth ≤ 5 for building execution scenarios.

### Why
- **Unbounded traversal is expensive**: Without a limit, we traverse the entire reachable call graph, which grows exponentially.
- **Business processes are modular**: Most meaningful execution paths are ~3–5 hops (entry → validation → processing → storage → logging).
- **LLM context limits**: Summarizing >50 nodes per scenario becomes unwieldy; Haiku/Sonnet work best on focused scenarios.

### Heuristic
```
primary_path = top 15 nodes by confidence in BFS order
alternate_paths = conditional branches (nodes with >1 callee)
```

Depth ≤ 5 means: (Entry) → (Hop 1) → (Hop 2) → ... → (Hop 5) = max 6 levels.

### Trade-offs
- **Pro**: Fast; manageable scenario size; mirrors how humans think about processes
- **Con**: Might miss long-running workflows (e.g., async batch processing with delayed steps)

**If you have a 10-hop process**, you might need to:
1. Increase depth limit to 7–8 (slower, larger scenarios)
2. Break into multiple scenarios (entry points at intermediate steps)
3. Mark long-hop edges as async/delayed

---

## MANUAL Node Injection Heuristics

### Decision
Synthetic user task nodes are injected for:
1. Function names containing "approve", "review", "validate_manually"
2. State transitions with large gaps (DRAFT → APPROVED without intermediate)
3. Integration points with no direct code (business rule gates)

### Why
- **Incomplete code traces**: Real business processes include manual approvals that aren't in code (compliance review, manager sign-off).
- **State-driven semantics**: A state transition DRAFT → APPROVED implies a decision point, even if no code reflects it.
- **Execution credibility**: BPMN diagrams that skip manual steps are misleading to business users.

### Confidence Range
MANUAL nodes are scored 0.3–0.6 (low confidence) to signal "inferred, needs human validation".

### Validation Strategy
When generating docs, flag MANUAL nodes for business owner review. Example:

```markdown
⚠️ **Inferred Manual Step** (confidence: 0.4)
- Step: Manager Approval
- Reason: State transition DRAFT → APPROVED with no intermediate code
- Action: Validate against actual workflow documentation
```

---

## Tier 1/2/3 Model Routing: Haiku → Sonnet → Opus

### Decision
Three-tier LLM pipeline with increasing model capacity:
- **Tier 1 (Haiku)**: Chunk summarization (high concurrency, cost-effective)
- **Tier 2 (Sonnet)**: Flow analysis (per-domain, reasoning-heavy)
- **Tier 3 (Opus)**: Document rollup (final polish, self-review)

### Why
- **Cost optimization**: Haiku is ~50% cheaper than Sonnet, ~85% cheaper than Opus. Use it for high-volume tasks.
- **Quality gradient**: Not all tasks need Opus. Chunk summarization is straightforward; doc rollup is complex reasoning.
- **Budget control**: Tier 1 has no budget limit (cost-effective); Tier 2/3 check budget before running.

### Cost Breakdown (typical)
- Tier 1: 60% of volume, 20% of cost (many chunks, cheap)
- Tier 2: 30% of volume, 40% of cost (per-domain, medium)
- Tier 3: 10% of volume, 40% of cost (few docs, expensive)

### Tuning Levers
```yaml
discovery.yaml:
  # Provider selection: bedrock | ollama | mlx-gemma | mlx-qwen
  provider: bedrock
  max_concurrent: 10  # Increase to speed up
  budget_limit_usd: 50.00

  # Bedrock model overrides (defaults to claude-haiku-4-5/sonnet-4-6/opus-4-6)
  bedrock:
    tier1: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tier2: us.anthropic.claude-sonnet-4-6
    tier3d: us.anthropic.claude-opus-4-6
    tier3p: us.anthropic.claude-opus-4-6

  # Ollama model overrides (defaults to gemma4:e2b / gemma4:26b / gemma4:31b)
  ollama:
    tier1: gemma4:e2b
    tier2: gemma4:26b
    tier3d: gemma4:26b
    tier3p: gemma4:31b
```

---

## Domain Classification: Namespace/Path Heuristics

### Decision
Group code nodes by namespace prefix + directory path, with fallback to file-level grouping.

### Why
- **Scalable**: Doesn't require ML training; works immediately on new codebases
- **Predictable**: Developers can reason about which domain a new file belongs to
- **Language-agnostic**: Works for Java (package), Python (module), C# (namespace), JavaScript (folder)

### Heuristic
```
1. Extract namespace/package prefix (longest common ancestor)
2. Group by domain (e.g., com.company.orders → "orders" domain)
3. Fallback: Group by top-level directory if namespace is unclear
4. Threshold: Need ≥ 3 nodes to form a domain (else singleton)
```

### Trade-offs
- **Pro**: Works without training; easy to explain
- **Con**: Doesn't capture cross-cutting concerns (logging, auth might belong in multiple domains); doesn't handle microservice boundaries

**When heuristics fail**, you can manually define domains in `discovery.yaml`:

```yaml
domains:
  - name: orders
    includes: [com.company.orders, app/src/orders]
  - name: payments
    includes: [com.company.payments, app/src/payments]
```

---

## State Machine Extraction: Explicit State Transitions

### Decision
Extract state machines from code by detecting patterns:
- Enum fields named `*_status`, `*_state`, `status`, `state`
- Assignments to these fields (e.g., `order.status = OrderStatus.APPROVED`)
- Function names that transition states (e.g., `approveOrder()`)

### Why
- **Business-critical**: State machines define the "playbook" for business processes
- **Visualizable**: State machines (FSM) are easy to render as Mermaid or BPMN
- **Verifiable**: We can extract state transitions from code and validate them against real logs

### Pattern Detection
```python
# Detected pattern:
order.status = OrderStatus.SUBMITTED  # State transition: ? → SUBMITTED
approveOrder()  # Trigger function

# Inferred:
state_transition = { entity: "Order", field: "status", from: "?", to: "SUBMITTED", trigger: "approveOrder" }
```

### Confidence
Explicitly coded state transitions (assignments) are high confidence (0.9–1.0). Inferred transitions (function names) are lower confidence (0.6–0.8).

---

## Pseudo Event Log Generation for Process Mining

### Decision
Convert execution scenarios to JSON event logs compatible with PM4Py and other process mining tools.

### Why
- **Bridging worlds**: Code analysis + process mining = comprehensive process discovery
- **Validation**: Process mining can detect bottlenecks, loops, and variant discovery that code analysis alone misses
- **Extensibility**: Pseudo logs can be mixed with real event logs for hybrid analysis

### Format
```json
{
  "case_id": "scenario_create_order_1",
  "process_name": "order_creation",
  "variant": "main",
  "events": [
    { "order": 1, "event_name": "Validate Order", "event_type": "PROCESS", "timestamp": "2026-04-17T10:00:00Z" },
    { "order": 2, "event_name": "Calculate Price", "event_type": "PROCESS", "timestamp": "2026-04-17T10:00:01Z" },
    { "order": 3, "event_name": "Save Order", "event_type": "DB", "timestamp": "2026-04-17T10:00:02Z" }
  ]
}
```

### Limitations
- **No real timestamps**: Pseudo logs use synthetic timestamps (order + 1s intervals)
- **No parallelism**: Event logs assume sequential execution; real code may run concurrently
- **No frequency**: We don't know which variant is most common (all variants have weight = 1)

**Future improvement**: Mix pseudo logs with real event logs to weight variants by actual frequency.

---

## Confidence Scoring: Multi-Signal Approach

### Decision
Confidence = sum of independent signals (depth, state transition, data boundary, external call, state dependency):

```
confidence = min(1.0, max(0, 5 - depth) + state_transition*4 + boundary*3 + external*2 + dependency*3) / 10
```

### Why
- **Composable**: Each signal captures a different kind of evidence (structure, semantics, data)
- **Explainable**: We can show the user exactly which signals contributed to a score
- **Tunable**: If a signal is wrong, we can adjust its weight without retraining

### Signals Explained

| Signal | Weight | Meaning | Example |
|--------|--------|---------|---------|
| Depth (5 - current_depth) | 1 | Nodes near the entry point are more likely in the primary path | Entry point (depth 0) = +5, leaf (depth 5) = 0 |
| State transition | 4 | If a node changes state, it's significant to the business process | `order.status = APPROVED` in a function = +4 |
| Data boundary (DB/Queue) | 3 | Database or queue interaction indicates a domain boundary | Repository call = +3 |
| External API call | 2 | External calls are notable but less central than internal logic | Payment gateway call = +2 |
| Read-after-write | 3 | If a node reads data after writing it, suggests important state | Save order, then fetch inventory = +3 |

**Max score**: 5 + 4 + 3 + 2 + 3 = 17; normalized to 0–1.0.

---

## Summary Table: Key Design Decisions

| Component | Decision | Trade-off | Reference |
|-----------|----------|-----------|-----------|
| Call Resolution | 7-level confidence | Precision vs recall | `docs/guides/call-graph/resolution-heuristics.md` |
| Execution Slices | BFS ≤ 5 depth | Coverage vs manageability | Phase 6 in pipeline docs |
| Manual Nodes | Heuristic injection | Accuracy vs completeness | `docs/guides/pipeline/phase-breakdown.md` |
| LLM Routing | Tier 1/2/3 (Haiku→Sonnet→Opus) | Cost vs quality | `docs/guides/pipeline/cost-tracking.md` |
| Domain Classification | Namespace heuristics | Scalability vs precision | Phase 4 in pipeline docs |
| State Machines | Pattern extraction | Automation vs coverage | `docs/guides/pipeline/phase-breakdown.md` |
| Pseudo Event Logs | Synthetic JSON format | Simplicity vs realism | `docs/guides/pipeline/profiling.md` |
| Confidence Scoring | Multi-signal sum | Explainability vs accuracy | `docs/guides/call-graph/test-strategy.md` |

---

**Next**: When you want to change a heuristic or add a new one:
1. Document the **why** here (decision + rationale)
2. Update the relevant guide doc (call-graph, parsers, pipeline)
3. Add test cases to validate the change
4. Re-measure confidence scores on a corpus before merging
