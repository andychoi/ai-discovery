# Processing-Logic Level Structuring

**Date**: 2026-06-01
**Status**: Implemented
**Area**: Tier-2 flow inference → PF artifacts (`ScenarioFlow`)

---

## 1. The Question

> Reverse-spec를 만드는 시스템에서, 함수/메소드 호출이 루프·조건문을 거치며
> 복잡해진다. 이것들이 roll-up되면서 **데이터 처리 로직을 적절한 레벨로 설명**해야
> 하는데, 이 부분이 어떻게 고려되고 있는가?

How does the system roll complex control flow (loops, conditionals, nested calls)
up into a *human-readable processing-logic narrative at the right altitude*?

---

## 2. Assessment (as-of 2026-06-01, pre-change)

### Core design: state-centric, not flow-centric

The roll-up unit is the **entity state machine**, not procedural control flow.
The system projects code onto "entity X transitions S1→S2 under condition C"
rather than "function A loops calling B N times". This projection is *what
produces an appropriate altitude* — only business-meaningful state changes
survive, so the level naturally rises.

### What was already handled well

| Concern | Mechanism | Output |
|---|---|---|
| Conditionals (`if`/`switch`) | `parsers/base.py:find_enclosing_guard()` → `StateTransition.guard_expr` | DMN decision tables (`dmn_generator.py`) |
| Cross-entity conditions | `EntityConditionCorrelation` (statistical mining) | DMN rule context columns |
| Branching | Tier-2 prompt maps `if/else` → `GATEWAY` | BPMN `exclusiveGateway` / Mermaid `alt` |
| Abstraction level | 3-tier models (Haiku→Sonnet→Opus); doc-type prompts (`as-is` / `as-is-detail` / `as-is-schema`); IPO framework; "name in business terms / ground in nodes" | Leveled docs |

### The gap this change addresses

`ScenarioFlow` (the PF artifact) represented processing logic **flat**:

- `steps` — a linear `[{step, name, type, description}]`. `GATEWAY` only *marked*
  a decision; it did **not contain** its branch arms (arm steps were lost).
- `process` — a string list rendered in the IPO table as
  `', '.join(flow.process)` — **all processing logic mashed into one cell**.
- The Mermaid flowchart's `GATEWAY` used generic `Continue`/`Skip` edges that
  reconverged immediately — no real condition, no branch contents.

Net: code-level conditional structure collapsed into a single flat altitude.
**Loops/iteration were (and remain) not explicitly modeled** — that was a
deliberate non-goal here (see §5).

---

## 3. Design: hierarchical step model + leveled rendering

**Key insight**: the raw material for leveling already existed but was not
exposed to Tier-2 — `StateTransition.guard_expr` (real source conditions) and
`Scenario.alternate_paths` (mined branches). No new AST extraction was needed;
the work was (a) surface those signals to the LLM, (b) make the output schema
nested, (c) render the hierarchy.

### Backward-compatibility strategy

The legacy flat `steps` is **derived** from the new hierarchy via
`_flatten_structured_steps`. So every existing consumer — BPMN XML renderer,
`verify_flow` (builds its claim narrative from `flow.steps`), resume/load — keeps
working unchanged, while leveled rendering reads `structured_steps`.

### `structured_steps` node shape

```jsonc
{
  "name": "Validate & Price",
  "type": "PHASE|PROCESS|GATEWAY|USER_TASK|DB|EXTERNAL|LOOP",
  "description": "...",
  "source_ref": "svc/order.py:42",          // grounding (file:line)
  "children": [ <node>, ... ],               // PHASE / LOOP body
  "branches": [                              // GATEWAY only
     {"condition": "order.total > 1000", "steps": [<node>, ...]},
     {"condition": "else",                 "steps": [<node>, ...]}
  ]
}
```

`PHASE` and `LOOP` are *structural wrappers* — not emitted into flat `steps`.
`GATEWAY` flattens to its marker step followed by inlined branch steps.

---

## 4. What changed (files)

| File | Change |
|---|---|
| `ai/flow_analyzer.py` | `ScenarioFlow.structured_steps` field; `_flatten_structured_steps()`; `_nodes_context` now prints `... WHEN <guard_expr>`; `_alternate_paths_context()`; `_build_steps_prompt` rewritten to request a 2–3 level hierarchy with branch arms; `infer_flow` builds structured then derives flat; persist/load read/write `structured_steps_json` |
| `generators/bpmn_generator.py` | `generate_ipo_markdown` Process → nested bullet list (`_render_process_tree`); `generate_mermaid_flowchart` → `_flowchart_from_structured` (real condition edge-labels, branch contents, LOOP dashed `repeat` back-edge); `generate_mermaid_sequence` → `_sequence_from_structured` (`alt/else <condition>`, `loop` blocks). All three fall back to flat rendering when `structured_steps` is absent. |
| `db.py` | `scenario_flows.structured_steps_json` column; migration block `current < 11`; `SCHEMA_VERSION = 11` |

### Example output

```
#### Process
- Validate & Price
  - Validate order items — check inventory
  - Apply pricing
    - if order.total > 1000:
      - Apply premium discount
    - else:
      - Apply standard pricing
- Persist & Notify
  - Save order (DB) — INSERT orders
  - for each: line items
    - Reserve stock
```

Flowchart gateways now carry `-->|order.total > 1000|` / `-->|else|` edge labels;
sequence diagrams emit `alt order.total > 1000 … else … end` and `loop … end`.

---

## 5. Non-goals / follow-ups

- **Loop/iteration semantics from AST** — for/while are still not extracted; a
  `LOOP` node only appears if the LLM infers iteration from `data_flow` over a
  collection. Capturing real loop bounds/bodies is a separate, larger change.
- **Conditional fidelity** — `Scenario.alternate_paths` conditions are still the
  slice builder's heuristic (callee-name based) except where they coincide with
  `guard_expr`. Unifying heuristic branches with real source conditions is future
  work.
- **BPMN XML gateway condition labels** — XML still renders from flat steps; the
  condition expressions are surfaced in Mermaid/IPO but not yet in BPMN
  `<sequenceFlow>` `conditionExpression`.

---

## 6. Verification

- Unit tests: `tests/test_structured_steps.py` (flatten round-trip, wrapper
  omission, nested IPO bullets, flowchart condition labels + loop back-edge,
  sequence `alt/else/loop`, flat fallbacks).
- Migration: fresh DB starts at v11 with the column; a simulated v10 DB gains the
  column on `init_db`.
- Integration: `infer_flow` with a mocked LLM returning nested JSON sets
  `structured_steps` and derives flat `steps`; `guard_expr` confirmed present in
  the steps prompt; persist→load round-trips `structured_steps`.
- Full suite: **949 passed** (incl. 13 new in `test_structured_steps.py`); no regressions.
