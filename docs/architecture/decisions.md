# Design Decisions

This document explains *why* we chose specific heuristics and trade-offs in AI-Discovery.

---

## Call Graph Resolution: Closed Origin Vocabulary (ADR-0004)

> **Superseded 2026-10-13 (M1.12.a).** The "4-stage graded confidence" cascade
> below (one origin spanning 0.95–0.6, fan-out to ≤ 8 candidates, a 0.5 edge to
> the bare name for every unresolved call, A-3 community narrowing) is retired.
> The resolver now follows [ADR-0004](../roadmap/adr/0004-edge-origin-vocabulary.md);
> the origin → confidence table lives **only** there and in
> `src/ai_discovery/graph/origins.py`.

### Decision
- **One confidence per origin.** Each edge has exactly one origin from a closed
  enum (`scip`, `import_exact`, `import_module`, `self_scope`, `inherited`,
  `receiver_typed`, `receiver_global`, `framework`, `dispatch`, `same_file`,
  `same_package`, `global_unique`, `external_client`, `sql_regex`). The origin
  fixes the confidence, the edge kind and the evidence kind. Stored in
  `call_edges.resolved_by` / `edge_kind` / `evidence_kind`.
- **Ambiguity → no edge.** Two or more candidates in a tier: no edge, a row in
  `unresolved_calls` with `reason=ambiguous` and `candidate_count`.
- **Unresolved → no edge.** `not_found`, `builtin`, `dynamic`, `external`,
  `dispatch_cap`, `di_ambiguous` rows instead of 0.5 edges to bare names.
- **Name-only guards** (`global_unique`, 0.50): same language family, callable
  target, name not in the per-language builtin/stdlib-member list, and in
  Python/JS a receiver-less call never binds to a class method. Java/C#
  receiver-less calls are `this` calls: they bind only on the enclosing class
  or its ancestors (`self_scope` / `inherited`), else `not_found`/`external`.
- **Types:** a receiver whose type is known (field/ctor-param type, or a type
  name used as a static receiver / constructor call) resolves on that type —
  `receiver_typed` when the type is visible locally (same file, same package,
  imported/`using`), `receiver_global` when it is known only by a unique
  repo-wide name. An interface-typed receiver with one implementing method is
  `framework` (DI binding); 2–24 implementations give one `dispatch` edge each;
  more is refused (`dispatch_cap`). A graph-wide pass also adds base-method →
  override `dispatch` edges.

### Why
- One origin with several confidences made confidence meaningless as a filter;
  fan-out and bare-name edges flowed into slices and BFS as if they were calls
  (ADR-0004 context; repowise changelog lessons in the edge-case catalog).
- **A-3 community narrowing is retired:** being wired to a module elsewhere in
  the graph is not evidence of which same-named method a call reaches. The
  `python-community-collision` fixture now forbids both candidate edges.

### Trade-offs
- **Recall drops, precision rises.** Calls that used to fan out or bind by
  graph locality are now `unresolved_calls` rows. They are reported (scan
  summary, overview page, bench) — never hidden. The M1.12.b bench run
  measures the deltas against the M0.12 baseline.
- Constructor-DI references whose type has no visible import (e.g. a missing
  `using`, or a CommonJS `require` the JS parser does not record) drop from
  0.8 to `receiver_global` 0.75; the corpus bars were lowered to 0.75 with a
  note per fixture.

### History (pre-ADR-0004)
The 2026-05/06 cascade — stage 0 index (1.0), exact (1.0), import-scope
(0.95), receiver-type (0.93; HIGH-3), short-name contextual 0.95…0.6 with A-3
community narrowing, unresolved 0.5 — is in git history (`call_graph.py`
before M1.12.a). Its HIGH-3 receiver-type and P0-2 interface→impl ideas
survive as `receiver_typed` and `framework`; Stage 0 survives as `scip`.

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


### Following rules (M1.12.b, ADR-0004 §2)
- Slices, alternate paths and the backward entry-point walk (`graph/entry_point_linker.py`)
  follow only edges that pass `origins.followable`: evidence `static|framework|scip`, edge kind
  `calls|dispatches_to|framework_binds|calls_external`, origin confidence ≥ 0.75. Name-only
  `global_unique` (0.50) edges are never walked; they stay in the graph for search and pages.
- A node is visited once (the visited set is filled at enqueue time).
- Every point where a slice stops records one reason in `Scenario.stop_reasons`:
  `db_boundary`, `external_boundary` (synthesized `external::` systems and EXTERNAL_API/QUEUE
  hints), `depth_cap`, `cycle` (all followable callees already in the slice), `dynamic_dispatch`
  (the node has `dynamic`/`dispatch_cap`/`di_ambiguous` unresolved calls), `unresolved_call`
  (calls exist but none can be followed), else `leaf_no_callees`.
- Depth stays 5 here; the depth-8 / 50-flows-per-entry flow tracer is M6.12.

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
Explicitly coded state transitions (literal assignments such as
`order.status = APPROVED`) are extracted at confidence 1.0.

> **Reconciled 2026-05-31 (HIGH-9):** earlier prose here claimed a separate
> *name-inferred* transition path scored 0.6–0.8. That path is **not
> implemented** — every transition currently extracted comes from an explicit
> assignment and is confidence 1.0. The LLM does, separately, invent `USER_TASK`
> steps (approval/review) during flow analysis; those are not state transitions
> and are now rendered with a `⚠ inferred` marker in BPMN/Mermaid so they are
> never read as code-grounded facts.

---

## Pseudo Event Log Generation for Process Mining

> **Reversed 2026-06-06 — pm4py removed.** An objective code review found the
> pm4py integration carried no information in its implemented form:
> - The only real pm4py call (`inductive_miner.apply`) ran on a **single
>   linear trace per scenario** — discovery of a single sequential trace
>   returns the input unchanged (zero information gain). Its output
>   (`result.net`) was never consumed by any report, doc, or DB write.
> - `token_replay` was imported but never called; **fitness was hardcoded to
>   1.0**, so the quality gate (≥ 0.90) always passed.
> - Bottleneck/cycle-time metrics were computed over **synthetic 1-second
>   timestamps**, producing fabricated numbers (every duration exactly
>   1000 ms) that were injected into PF spec docs.
>
> Phase 16 now computes pure-Python edge-frequency flow statistics only
> (`ai/process_miner.py`). The pseudo event log **format below is retained**
> as the Phase 16 input.
>
> **Reintroduction condition**: pm4py becomes meaningful only with *real*
> runtime event logs (app logs / APM traces) — conformance-checking the
> code-derived model against actual execution. If that ingestion is built,
> reintroduce pm4py as an optional extra with lazy imports.

### Decision (superseded — see reversal above)
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

## Node Ranking Score: Multi-Signal Approach

> **Reconciled 2026-05-31 (MED-1):** this score ranks nodes *within* an
> execution slice; it is distinct from call-edge confidence (0.5–1.0, above).
> `call_graph._score_node` returns the **raw, unnormalized** sum below (range
> ~0–17) and stores it on `ExecutionNode.confidence` purely for ordering
> (primary_path = top-N by this score). The `min(1.0, …)/10` normalization in
> earlier revisions of this doc was **never implemented**; do not compare this
> field against a 0–1 threshold — it is a ranking key, not a probability.

### Decision
Rank = sum of independent signals (depth, state transition, data boundary, external call, state dependency):

```
# As implemented (raw ranking score, NOT normalized to 0–1):
rank = max(0, 5 - depth) + state_transition*4 + boundary*3 + external*2 + dependency*3   # 0..17
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
| Pseudo Event Logs | Synthetic JSON format (pm4py removed 2026-06-06) | Simplicity vs realism | `docs/guides/pipeline/profiling.md` |
| Confidence Scoring | Multi-signal sum | Explainability vs accuracy | `docs/guides/call-graph/test-strategy.md` |

---

**Next**: When you want to change a heuristic or add a new one:
1. Document the **why** here (decision + rationale)
2. Update the relevant guide doc (call-graph, parsers, pipeline)
3. Add test cases to validate the change
4. Re-measure confidence scores on a corpus before merging
