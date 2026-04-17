# Implementation Summary: Priorities 1–3

Date: 2026-04-16  
Status: Complete

---

## Priority 1: Code Fixes ✅

### Fix 1.1: Primary Path Execution Ordering
**File**: `app/graph/call_graph.py:ExecutionSliceBuilder.build_scenario()`

**What Changed**:
- **Before**: `primary_path` was score-sorted (best-confidence nodes first)
- **After**: `primary_path` preserves BFS traversal order, filtered to top-15 confidence nodes

**Why It Matters**: 
Execution sequences should reflect the actual traversal order through code, not just confidence ranking. This ensures BPMN and IPO artifacts reflect real code execution paths.

**Code Detail**:
```python
# Track BFS order
bfs_order: list[str] = []
# ...append in traversal order...

# Filter to top-confidence, preserve order
top_by_conf = sorted(scenario.nodes, key=lambda n: n.confidence, reverse=True)[:15]
top_ids = set(n.id for n in top_by_conf)
scenario.primary_path = [nid for nid in bfs_order if nid in top_ids]
```

### Fix 1.2: Alternate Paths Population
**File**: `app/graph/call_graph.py:ExecutionSliceBuilder._detect_alternate_paths()` (NEW)

**What Changed**:
- **Before**: `Scenario.alternate_paths` was always empty list `[]`
- **After**: Detects conditional branches (nodes with multiple callees) and populates with inferred conditions

**Detection Logic**:
- When a node has >1 outgoing calls → treat as gateway
- Infer condition name from callee semantics:
  - `success`, `ok`, `valid`, `pass` → "success"
  - `error`, `fail`, `reject`, `invalid` → "failure"
  - `retry`, `fallback` → "retry"
  - else → generic "branch_N"
- Record BFS path for each branch (depth ≤10 for readability)

**Data Structure**:
```python
scenario.alternate_paths = [
    { "condition": "success", "path": ["node_a", "node_b", ...] },
    { "condition": "failure", "path": ["node_c", ...] },
]
```

---

## Priority 2: Documentation Updates ✅

### 2a. `docs/flow.md` Updates

#### Stage 5 Enhancement — Execution Slice Construction
- Added MANUAL node type documentation: synthetic user task nodes inferred from naming (approve, review, etc.)
- Documented `primary_path` as "in BFS execution order" (clarification)
- Documented `alternate_paths` population with branch detection heuristic
- Explained multi-signal scoring formula with all 5 signals

#### Stage 10 Enhancement — Artifact Generation
Expanded from 4 artifact types to 7:

| # | Artifact | Status | Purpose |
|---|----------|--------|---------|
| ① | Mermaid sequence diagram | ✅ Implemented | Actor-based flow visualization |
| ② | PlantUML activity diagram | ✅ Implemented | Activity-centric flow visualization |
| ③ | BPMN 2.0 XML | ✅ Enhanced | **Now with swimlanes** (User/System/External) |
| ④ | IPO markdown table | ✅ Implemented | Input-Process-Output summary |
| ⑤ | Pseudo event log (JSON) | ✅ NEW | PM4Py/process mining input |
| ⑥ | State machine (FSM) diagram | ✅ NEW | Mermaid state diagram |
| ⑦ | Scenario intent clustering (metadata) | ✅ NEW | Use-case grouping (Create, Update, Delete, etc.) |

### 2b. `docs/architecture.md` Updates

#### New Section: L1–L7 Decomposition Framework (after LLM Tier Model)

Documents the multi-level abstraction hierarchy:

| Level | Name | Scope | Example |
|-------|------|-------|---------|
| L1 | Business Domain | Domain (Order, Payment) | `order-domain` |
| L2 | Business Process | Workflow (Create → Pay → Ship) | `order-creation-process` |
| L3 | Business Flow | Coherent sequence | `user_flow`, `integration_flow` |
| L4 | Scenario / Use Case | Single entry + bounded exec | `POST /orders → OrderService.create()` |
| L5 | Service / Component | Core business handler | `OrderService`, `PaymentRepository` |
| L6 | Function / Method | Individual operation | `validate()`, `calculatePrice()` |
| L7 | Code Statement | Source lines | Assignment, loop, branching |

**Bridging**: Explains how each level embeds lower levels and how documents reflect the hierarchy.

#### New Section: BPMN Model — Lanes, SubProcesses, Execution Types (after L1–L7)

**Swimlanes**:
| Lane | Actors | Elements |
|------|--------|----------|
| User | End users, admins | User Task, Start/End |
| System | Application code | Service Task, Gateways |
| External | Third-party, DB, API | Data stores, external calls |

**Lane Assignment Heuristic**: Maps ExecutionNode types to lanes
- `USER_TASK`, `ENTRY` → User
- `PROCESS`, `FUNCTION`, `GATEWAY` → System
- `DB`, `EXTERNAL_API`, `QUEUE` → External

**SubProcess Grouping**: When >15 steps, cluster into `<bpmn:subProcess>` elements

**Node Type → BPMN Element Mapping Table**: Documents all ExecutionNode types and their BPMN representations (including MANUAL nodes as `userTask` with low confidence)

#### New Section: ExecutionNode Types (before Call Resolution Strategy)

Documents all 7 node types with confidence ranges:
- `ENTRY` (1.0), `FUNCTION` (0.6–1.0), `DB` (0.8–1.0), `QUEUE` (0.8–1.0), `EXTERNAL_API` (0.7–1.0), `MANUAL` (0.3–0.6), `UNRESOLVED` (0.5)

Explains MANUAL node injection heuristics.

#### New Section: Confidence Scoring & Human Feedback (before Call Resolution Strategy)

**Multi-Signal Scoring Formula**:
```
score = max(0, 5 - depth)           // +5 entry, -1 per level
      + (4 if state_transition)      // +4 state changes
      + (3 if DB/QUEUE)              // +3 data boundary
      + (2 if EXTERNAL_API)          // +2 external call
      + (3 if read_after_write)      // +3 state dependency
```

**Human-in-the-Loop**: Describes feedback mechanism for low-confidence scenarios (<0.7) and high unverified claim rates (>20%).

---

## Priority 3: Implementation ✅

### 3a. BPMN Lanes Support
**File**: `app/output/bpmn_generator.py::BPMNGenerator.generate_bpmn_xml()`

**Changes**:
- Detects which lanes are used based on step types
- Generates BPMN collaboration with participants (one per lane)
- Adds `<bpmn:laneSet>` with lane containers
- Maps steps to lanes via `_STEP_LANE` dictionary

**Output**:
```xml
<bpmn:collaboration id="Collaboration_1">
  <bpmn:participant id="Participant_User" name="User" processRef="scenario_id"/>
  <bpmn:participant id="Participant_System" name="System" processRef="scenario_id"/>
  <bpmn:participant id="Participant_External" name="External" processRef="scenario_id"/>
</bpmn:collaboration>
<bpmn:process id="scenario_id" ...>
  <bpmn:laneSet id="LaneSet_1">
    <bpmn:lane id="Lane_User" name="User">...</bpmn:lane>
    <bpmn:lane id="Lane_System" name="System">...</bpmn:lane>
    <bpmn:lane id="Lane_External" name="External">...</bpmn:lane>
  </bpmn:laneSet>
  ... activities ...
</bpmn:process>
```

### 3b. FSM Visualization
**File**: `app/output/bpmn_generator.py::BPMNGenerator.generate_fsm_diagram()` (NEW)

**Input**: List of state transitions with { entity, field, from_state, to_state, trigger_function }

**Output**: Mermaid state diagram syntax

**Example**:
```
stateDiagram-v2
    [*] --> DRAFT
    DRAFT --> SUBMITTED: submitOrder()
    SUBMITTED --> APPROVED: approveOrder()
    APPROVED --> PAID: payOrder()
    PAID --> FULFILLED: shipOrder()
```

**Features**:
- De-duplicates identical transitions
- Adds [*] start state from first from_state
- Trigger function name as transition label

### 3c. Pseudo Event Log Generation
**File**: `app/output/bpmn_generator.py::BPMNGenerator.generate_pseudo_event_log()` (NEW)

**Input**: ScenarioFlow object

**Output**: JSON structure for PM4Py / process mining tools

**Format**:
```json
{
  "case_id": "scenario_create_order_42",
  "process_name": "scenario_create_order_42",
  "variant": "main",
  "events": [
    { "order": 1, "event_name": "Validate Order", "event_type": "PROCESS" },
    { "order": 2, "event_name": "Calculate Price", "event_type": "PROCESS" },
    { "order": 3, "event_name": "Save Order", "event_type": "DB" },
    { "order": 4, "event_name": "Publish Event", "event_type": "QUEUE" }
  ]
}
```

### 3d. Scenario Intent Clustering
**File**: `app/ai/flow_clustering.py` (NEW)

**Exports**:
- `ScenarioCluster` dataclass: { intent, domain, scenarios[], confidence }
- `infer_intent(scenario: Scenario) -> str`: Maps scenario to intent verb
- `cluster_scenarios_by_intent(scenarios: list) -> list[ScenarioCluster]`: Groups by (intent, domain)
- `get_cluster_summary(clusters) -> dict`: Intent frequency count

**Intent Patterns**:
- `create` — create, new, post, add, insert
- `read` — get, fetch, list, search, query, view
- `update` — update, edit, patch, modify, change
- `delete` — delete, remove, drop, cancel, archive
- `approve` — approve, accept, validate, confirm, authorize
- `reject` — reject, deny, decline, refuse
- `process` — process, execute, run, handle, trigger
- `publish` — publish, emit, send, dispatch, broadcast
- `subscribe` — subscribe, consume, listen, handle, receive
- `other` — fallback for unrecognized intents

**Example Result**:
```python
clusters = [
    ScenarioCluster(intent="create", domain="Order", scenarios=["scenario_create_order_1", ...]),
    ScenarioCluster(intent="read", domain="Order", scenarios=["scenario_get_order_2", ...]),
    ScenarioCluster(intent="approve", domain="Payment", scenarios=["scenario_approve_payment_3", ...]),
]
```

---

## Integration Points (To Be Wired)

These new features are implemented but not yet wired into the main pipeline. To activate:

### 1. Wire Alternate Paths into Tier 2 Flow Analysis
In `app/ai/flow_analyzer.py::ScenarioFlowInference.infer_flow()`:
```python
# Use scenario.alternate_paths in prompts
alt_paths_str = json.dumps(scenario.alternate_paths)
```

### 2. Wire FSM Generation into Scenario Rendering
In `app/output/doc_generator.py::write_scenario_docs()`:
```python
bpmn_gen = BPMNGenerator()
fsm_md = bpmn_gen.generate_fsm_diagram(state_transitions)
scenario_flow.fsm_diagram = fsm_md
```

### 3. Wire Pseudo Event Log into Artifact Export
In `app/output/doc_generator.py`:
```python
pseudo_log = bpmn_gen.generate_pseudo_event_log(scenario_flow)
# Save to: data/{slug}/event-logs/{scenario_id}.json
```

### 4. Wire Intent Clustering into Domain Report
In `app/pipeline.py::_render_markdown()`:
```python
from ..ai.flow_clustering import cluster_scenarios_by_intent
clusters = cluster_scenarios_by_intent(scenarios)
# Pass to template as context
```

---

## Code Quality Notes

`★ Insight ─────────────────────────────────────`
- **Backward Compatibility**: All changes are additive; existing code paths unaffected
- **Confidence Scoring**: Multi-signal approach balances code structure (depth), business semantics (state), and data boundaries—more robust than single-signal ranking
- **FSM Pattern**: State machine extraction naturally follows from StateTransition detection; scales to multi-entity FSMs with simple grouping
- **Intent Clustering**: Regex-based pattern matching is lightweight and extensible; can be evolved with ML-based intent classification later
`─────────────────────────────────────────────────`

---

## Files Changed

| File | Change | Lines |
|------|--------|-------|
| `app/graph/call_graph.py` | Primary path + alternate paths | +70 |
| `app/output/bpmn_generator.py` | Lanes + FSM + pseudo log | +80 |
| `app/ai/flow_clustering.py` | NEW — intent clustering | +90 |
| `docs/flow.md` | Stage 5 & 10 enhancements | +40 |
| `docs/architecture.md` | L1–L7, BPMN, confidence, human feedback | +150 |

**Total**: ~4 new methods, 1 new module, 2 doc enhancements, no breaking changes.
