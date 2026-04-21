---
title: State-First Backbone Plan
date: 2026-04-20
status: proposed
scope: Reverse-engineering output evolution — from per-domain summaries to canonical entity state machines as the organizing artifact
supersedes_partial: docs/assessments/02-reverse-engineering-implementation-plan.md (reorders its phases)
---

# State-First Backbone Plan

## Executive Summary

The organizing bet: **entities and their lifecycles are the stable skeleton of a brownfield system.** Code refactors; frameworks churn; folder structure shifts. An `Order` going `Created → Approved → Fulfilled → Closed` does not. If we can extract canonical state machines per business entity — with the guards that govern each transition and the entry points (UI/API/batch) that trigger them — then every downstream artifact the research document wants (BPMN, DMN, EARS, knowledge graph, traceability, impact analysis) hangs off a single trusted artifact rather than being separately inferred from noisy code.

This spec:

1. Enumerates the concrete blockers that prevent a state-first backbone today
2. Sequences work by ROI into five phases
3. Provides file-and-function-level task lists for Phase 0 and Phase 1 (executable now)
4. Defers or drops work that the research document proposes but which is premature

The shortest path to a demonstrable backbone is **Phase 0 → 1 → 2** (~5–7 sprints). Phase 3 delivers stakeholder-facing views (BPMN/DMN/EARS) on top. Phase 4 federates across repos. Phase 5 is deferred.

---

## Part 1: Concrete Blockers

Grouped by the layer they break. Each blocker cites code or a gap-assessment section.

### Tier A — Signal quality (block everything downstream)

| # | Blocker | Evidence | Impact |
|---|---------|----------|--------|
| A1 | Short-name call collision. Resolver fans out to every candidate when names like `save`, `execute`, `handle` collide. | `graph/call_graph.py`, per-language parsers; gap-assessment §2 | Transitions get attributed to wrong triggers. Worst in Java/C# service layers. |
| A2 | UNRESOLVED vs EXTERNAL_API still partially conflated. `UNRESOLVED` node type exists (`call_graph.py:351`) but default classification paths still pick `EXTERNAL_API` (`call_graph.py:334`). | `graph/call_graph.py:334, 351` | Federated graph inherits the lie — "real external" indistinguishable from "I failed to resolve." |
| A3 | Broken test suite. Parser deps missing, tests stale. | gap-assessment §8, `app/tests/*` | Cannot refactor resolver or pipeline safely. |

*Previously listed but already fixed*: pipeline ordering (domains classified at Phase 7 before scenarios at Phase 8.5, with `domain_classifier.py:83` mutating `node.domain`). Dropped from plan.

### Tier B — State-first specific (required to build the backbone)

| # | Blocker | Evidence | Impact |
|---|---------|----------|--------|
| B1 | `StateTransition` extracted per-function but never aggregated per-entity. | `graph/models.py:51`, `bpmn_generator.generate_fsm_diagram()` visualizes one scenario only | No canonical state machine per entity. No queryable "Order lifecycle." |
| B2 | No guard/predicate capture. Transitions record `from_state`/`to_state` but not the governing `if credit_score > X`. | per-language `_extract_state_transitions()` in `parsers/*` | DMN has no decision data. EARS "WHEN/THEN" has no WHEN. |
| B3 | No entry-point ↔ transition linkage. Parsers identify endpoints/UI/batch; parsers identify transitions; nothing connects them with evidence. | `call_graph.py`, `flow_analyzer.py` | Cannot answer the core state-first question: "what UI/API/job causes this transition?" |
| B4 | `StateTransition` not persisted as first-class graph data. Lives inside scenarios, not as a top-level SQLite table. | SQLite schema, `output/` layer | Cannot query "all transitions on Order" outside pipeline runtime. No JSON export. |
| B5 | Score-sorted `primary_path`, not execution order. | `call_graph.py`, gap-assessment §3 | State machines built on misordered transitions are fiction. |
| B6 | No entity identity consolidation across files/languages. | no owner; needs new module | Cross-system state choreography impossible. Intra-repo still tractable. |

### Tier C — Federation (block enterprise use, not backbone internals)

| # | Blocker | Evidence | Impact |
|---|---------|----------|--------|
| C1 | Single-repo only; no workspace mode. | `app/repo/*`, `pipeline.py`; gap-assessment §1 | Cannot model 50+ app reality. |
| C2 | No contract/artifact ingestion (OpenAPI, proto, queue configs, k8s). | `repo/lang_detector.py`; gap-assessment §6 | A `publishOrderCreated` call stays unresolved because the queue declaration lives in YAML we never read. |
| C3 | Naive domain classifier (first non-framework path segment). | `graph/domain_classifier.py`; gap-assessment §5 | Domain boundaries are technical, not business-capability. L1 hierarchy built on this is junk. |

### Tier D — Output / authoring (premature to fix first)

| # | Blocker | Status |
|---|---------|--------|
| D1 | No JSON graph export; markdown is the only durable output. | Cheap; after Tier B. |
| D2 | Flat sqlite-vec RAG, no subgraph retrieval. | Premature until graph is worth embedding. |
| D3 | No L0/L1 business-capability taxonomy source. | Requires human curation or external taxonomy import. Not a code problem. |
| D4 | No EARS/DMN authoring surface. | Forward-engineering; deferred. |

---

## Part 2: Phased Plan (ROI-Sequenced)

ROI = (downstream artifacts unlocked) / (engineering cost). Prerequisites are strict: Tier A must precede Tier B.

### Phase 0 — Unblock (1 sprint)

**Goal**: Remove the things that make everything else unsafe.

| Task | Blockers | Cost |
|------|----------|------|
| Fix test suite drift; pin parser deps; add enterprise fixtures | A3 | S |
| Harden `UNRESOLVED` classification: ensure no default path assigns `EXTERNAL_API` to unresolvable calls | A2 | S |

**Exit**: Green tests on enterprise fixtures; external vs unresolved honestly distinguishable.

### Phase 1 — Clean signal for transitions (2–3 sprints)

**Goal**: Get transition evidence to trustworthy quality *before* aggregating into FSMs.

| Task | Blockers | Cost |
|------|----------|------|
| Resolver: import/receiver/DI-aware symbol resolution | A1 | M |
| Capture guard predicates on `StateTransition` (walk parent `if`/`when` AST nodes; store as `guard_expr` field) | B2 | S–M |
| Link entry-points to transitions via call-graph BFS; store as `entry_points: [...]` on `StateTransition` | B3 | M |
| Replace score-sorted `primary_path` with topological/execution-order builder | B5 | M |

**Exit**: Every `StateTransition` carries `(entity, field, from, to, guard_expr?, trigger_fn, entry_points, evidence)`. This *is* the backbone.

### Phase 2 — Aggregate into canonical artifacts (2 sprints)

**Goal**: Turn per-function transitions into per-entity state machines that are queryable and exportable.

| Sub-phase | Task | Blockers | Artifact |
|-----------|------|----------|----------|
| 2.1 | Per-entity FSM rollup — aggregate transitions across all code paths touching the same entity | B1 | `EntityStateMachine` dataclass: `states`, `transitions`, `fields`, `source_files`, `confidence` |
| 2.2 | Persist `StateTransition` + `EntityStateMachine` as first-class SQLite tables | B4 | Survives the pipeline run; queryable externally |
| 2.3 | JSON graph export alongside markdown | D1 | Canonical format; unlocks diffing, review, external tooling |
| 2.4 | Intra-repo entity identity consolidation — dual-pass class-backed + classless, inheritance-aware | B6 (partial) | `Order` / `OrderEntity` / `Orders` merged into one FSM |
| 2.5 | Non-class field sources — SQL strings, GraphQL schemas, Mongoose / Django Meta, ORM migrations | B6 (full intra-repo) | Entities discovered from raw SQL + schemaless code |

**Exit**: Every important business entity has a canonical FSM that can be serialized, diffed, and reviewed. **This is the shippable state-first backbone milestone.**

Detailed breakdown of 2.4 and 2.5 is in `docs/guides/entity-identity/`:

- `consolidation-algorithm.md` — the dual-pass algorithm
- `edge-cases.md` — survey of programming styles, MVC variants, and dynamic DB patterns with scope dispositions

#### Phase 2.4 — Intra-repo consolidation (design B)

The class-based fingerprint alone misses half the real-world cases (reducers, utilities, SQL-first code). So 2.4 runs a **dual pass** plus a **cross pass**:

**Identity contract (prerequisite for all three passes)**: every `StateTransition` / `EntityStateMachine` carries both `entity` (short display name) and `entity_id` (unique key — class `qualified_name`, or `enclosing_fn::var` for duck-typed receivers). The rollup groups by `entity_id`, so collisions like `billing.Order` vs `ecommerce.Order` never silently merge. All four parsers, `call_graph.py`, rollup, SQLite persistence, and JSON export flow `entity_id` through unchanged.

1. **Class-backed pass** — FSMs with a backing class CodeNode. Fingerprint = `(bases-adjusted field set, normalized name stem)`. Merge when adjusted Jaccard ≥ 0.9 and field-count ratio ≥ 0.7, or Jaccard ≥ 0.7 + stem similarity ≥ 0.8 + ratio ≥ 0.7. **Projection rule**: low field-count ratio (< 0.5) + high coverage of the smaller set inside the larger (≥ 0.8) → record as projection link, don't merge (handles `OrderDto` ↔ `Order`). Coverage — not Jaccard — gates projection because Jaccard approaches `|smaller|/|larger|` for pure subsets and would reject the intended cases.
2. **Classless pass** — FSMs with no backing class (reducers, utility functions, duck-typed code). Fingerprint = `(transition-derived field set, normalized name stem)`. Stem normalization includes Redux-era suffixes (`Reducer`, `Slice`, `Saga`) so `orderReducer` + `orderSlice` normalize to a common stem.
3. **Cross pass** — classless field set ⊆ classful field set + stem match → merge classless into classful (handles "SQL-only touches orders" once 2.5 lands).

Pre-filters before scoring:

- Drop FSMs whose short `entity` is a generic parameter (`thing`, `obj`, `item`, `arg`, …) *and* no backing class exists
- Drop ORM-meta fields (`__tablename__`, `__table_args__`, `Meta`, `objects`, `id`, `pk`) from fingerprint
- Flag mixin classes (used as base by ≥2 others) — subtract their fields from descendants before scoring; never emit an FSM for the mixin itself
- Same-qualified-name across files merged unconditionally at rollup — handles C# partial classes

Post-consolidation: if two distinct FSMs end up sharing a short `entity` (e.g. surviving `billing.Order` and `ecommerce.Order`), rewrite `entity` to `Order (billing)` / `Order (ecommerce)` for display. `entity_id` is never mutated, so downstream joins remain stable.

Inheritance-awareness needs parser support for `CodeNode.bases`, added in 2.4.

**Exit**: On the enterprise fixture, aliased-entity recall ≥ 80% (manually-identified alias pairs that get correctly merged) and false-merge precision ≥ 95% (merges that a human reviewer agrees with).

#### Phase 2.5 — Non-class field sources

The 2.4 fingerprint is class-shaped. Much of real-world code isn't:

| Source | Pattern | Implementation |
|--------|---------|----------------|
| Raw SQL strings | `conn.execute("INSERT INTO orders (id, status, total) VALUES (...)")` | `sqlparse` + pattern match on call arguments; extract `(table, columns, op)` |
| SQLAlchemy Core | `Table('orders', metadata, Column('id', …), …)` | AST match on `Table(...)` constructor calls |
| Alembic / Django / Flyway migrations | `op.add_column('orders', Column('tracking', String))` | Migration-file parser aggregates `add_column` / `create_table` into entity field set |
| GraphQL schema builders | `new GraphQLObjectType({ name: 'Order', fields: { …} })`, `typeDefs`, `.graphql` files | JS object-literal walker + SDL parser |
| Mongoose schemas | `new mongoose.Schema({ id: String, status: String })` | JS object-literal walker |
| Django `forms.ModelForm` / DRF `Serializer` | `class OrderForm: class Meta: model = Order; fields = [...]` | Nested-Meta resolver with cross-reference to model class |
| Pydantic models | already handled as class fields | (no work) |

Each source feeds into the **pre-rollup** stage: each non-class source produces a **synthetic class-less CodeNode** with `fields` populated. The 2.4 consolidator then does the rest — the classless and cross passes it already runs pick them up.

**Exit**: On a mixed-style corpus (OO + functional + SQL-first), entity discovery recall ≥ 75% compared to human baseline (vs. ~45% baseline for class-only 2.4).

**Deferred further**:

- Immutable-replace transitions (`dataclasses.replace`, Kotlin `.copy()`) — recognizer is a transition-detection concern, not fingerprinting. Phase 2.6 or Phase 3 work.
- State-pattern recognition (GoF State classes as lifecycle stages) — needs cross-class inference; Phase 3.
- Redux-reducer spread tracing to recover non-written fields — Phase 3.
- Stored procs / DB triggers — requires SQL-file parser; Phase 5+.
- Dynamic column access via `setattr` / `getattr(order, config['field'])` — unrecoverable statically. **Documented limit.**

### Phase 3 — Integration-centric outputs on top of the backbone (1–2 sprints)

**Goal**: Make the backbone *visible and useful* to business + IT + security.

| Task | Produces |
|------|----------|
| FSM → BPMN lane diagrams (extend `bpmn_generator.py` to consume aggregated `EntityStateMachine`, not single scenarios) | Process view per entity |
| FSM + guards → DMN decision tables (one row per guarded transition) | Decision logic for business review |
| Entry-points + transitions → EARS-skeleton drafts (`WHEN [entry-point], THEN [from→to] IF [guard]`) | Forward-engineering ramp; human edits refine |
| "Entity impact" query: given an entity, list all code paths touching its transitions | Validates backbone; foundation for impact-analysis tooling |

**Exit**: Three human-facing views (BPMN, DMN, EARS) all sourced from the same canonical FSM.

### Phase 4 — Federation (2–4 sprints; defer until Phase 3 proves value)

| Task | Blockers |
|------|----------|
| Workspace / multi-repo scan mode | C1 |
| Contract ingestion (OpenAPI, proto, queue configs, k8s manifests) | C2 |
| Cross-repo entity consolidation (field fingerprint + endpoint naming + message schemas) | B6 full |
| Cross-system state choreography — which service drives which transition | new |

**Exit**: Enterprise-grade state-first backbone spanning the federated graph. *"Order.Approved is set by billing-svc; Order.Shipped is set by fulfillment-svc."*

### Phase 5 — Deferred / optional

- Graph-RAG (only after Phase 4 graph is rich enough to be worth subgraph embedding)
- L0/L1 business-capability taxonomy (needs human or external source, not code)
- Full EARS/DMN authoring UI (forward-engineering track; separate project)
- Better domain classifier (C3) — couples to taxonomy decision above

---

## Part 3: Executable Task List (Phase 0 + Phase 1)

This section is meant to be pulled into sprint tracking.

### Phase 0.1: Test suite stabilization

**Files**:
- `app/tests/test_doc_generator.py`, `app/tests/test_self_review_regen.py` (stale per gap-assessment §8)
- `app/requirements.txt` or equivalent for parser deps
- `tests/fixtures/` — add three enterprise fixtures: (a) monorepo with service-to-service HTTP calls, (b) Python+Java shared queue, (c) shared library across two apps

**Acceptance**:
- `pytest` collects and passes all tests in clean venv
- All four parsers (Python, Java, C#, JS/TS) smoke-test on their fixture
- CI job runs the enterprise fixtures end-to-end

### Phase 0.2: UNRESOLVED vs EXTERNAL_API hardening

**Files**:
- `src/ai_discovery/graph/call_graph.py:334` — inspect the branch that assigns `EXTERNAL_API`; ensure it triggers only on evidence (HTTP client usage, known integration library, OpenAPI binding)
- `src/ai_discovery/graph/call_graph.py:351` — `UNRESOLVED` path should be the default when evidence is absent

**Acceptance**:
- Unit test: a call to an unknown short-name `execute()` with no import context resolves as `UNRESOLVED`, not `EXTERNAL_API`
- Unit test: a call through a known `HttpClient`/`requests.post` resolves as `EXTERNAL_API` with evidence field populated
- Regression fixture: ratio of `EXTERNAL_API` to `UNRESOLVED` nodes on the enterprise fixture drops from current baseline to <50% of prior value

### Phase 1.1: Resolver — import/receiver/DI-aware

**Files**:
- `src/ai_discovery/parsers/base.py` — extend `CodeNode` parser output with: `imports: list[str]`, `receiver_types: dict[call_site, str]`, `di_bindings: list[(interface, impl)]`
- `src/ai_discovery/parsers/python_parser.py`, `java.py`, `csharp.py`, `javascript.py` — populate new fields
- `src/ai_discovery/graph/call_graph.py` — add resolver stages in order: exact → import-scoped → receiver/type-aware → DI-assisted → interface-impl → short-name fallback
- Each stage should set `CallEdge.edge_type` and attach evidence to `CallEdge.metadata` (new field)

**Acceptance**:
- Fixture: `OrderService.save()` and `CustomerService.save()` in the same repo resolve independently, not as a fan-out
- Java: Spring `@Autowired` field of interface `Foo` with single `@Service` impl `FooImpl` resolves interface calls to `FooImpl` with `di_injection` edge type
- Measured: false-positive short-name edge count on enterprise fixture drops ≥70%

### Phase 1.2: Guard predicate capture on StateTransition

**Files**:
- `src/ai_discovery/graph/models.py:51` — add field `guard_expr: str | None = None` and `guard_ast: dict = field(default_factory=dict)` to `StateTransition`
- `src/ai_discovery/parsers/python_parser.py` — in `_extract_state_transitions()`, walk up to the enclosing `If` node and capture `.test` source
- Same for `java.py` (parent `IfStatement`), `csharp.py`, `javascript.py`
- Normalize guards to a small AST dict: `{op: "gt", left: "credit_score", right: 700}` when possible; otherwise retain raw source

**Acceptance**:
- Unit test per language: `if order.total > 1000: order.status = "review"` extracts `StateTransition(entity="order", field="status", from=None, to="review", guard_expr="order.total > 1000")`
- Guard capture rate ≥80% of transitions that occur inside conditionals on the enterprise fixture

### Phase 1.3: Entry-point linkage

**Files**:
- `src/ai_discovery/graph/models.py:51` — add `entry_points: list[dict] = field(default_factory=list)` on `StateTransition` (shape: `{kind: "UI"|"API"|"Batch"|"Event", qualified_name, confidence, hop_count}`)
- New module `src/ai_discovery/graph/entry_point_linker.py` — BFS backwards from each transition's `trigger_function` through the call graph until an entry-point node is reached (node_type in `endpoint`, `ui_component`, `batch_job`)
- Wire into pipeline at Phase 8.6 (new sub-phase after execution slices)

**Acceptance**:
- Fixture: `POST /orders/:id/approve` → `OrderService.approve()` → `order.status = "approved"` produces a transition with `entry_points=[{kind:"API", qualified_name:"OrdersController.approve", confidence:>=0.8, hop_count:2}]`
- ≥70% of transitions on enterprise fixtures have at least one entry-point linked

### Phase 1.4: Execution-ordered primary_path

**Files**:
- `src/ai_discovery/graph/call_graph.py` — replace score-sort in `ExecutionSliceBuilder` with topological order respecting `ExecutionEdge.edge_type` semantics (CALL = sequential, ASYNC = new ordering root, CONDITIONAL = branch point)
- Preserve branch/async/boundary evidence on edges

**Acceptance**:
- Unit test: synthetic scenario with `entry → validate → (branch: persist | reject) → notify` produces `primary_path` in that order, not confidence-sorted
- On the enterprise fixture, ≥85% of scenarios have `primary_path[0]` equal to the `entry_point` node (trivially true only if ordering is correct)

---

## Part 4: Out-of-Scope (and Why)

The research document proposed work that this plan explicitly defers or drops:

| Proposal | Status | Reason |
|----------|--------|--------|
| Knowledge Graph RAG now | Deferred to Phase 5 | Premature. The graph is not yet worth embedding. Do resolver + backbone first. |
| L0/L1 business-capability hierarchy | Dropped from code-side work | No code signal exists. Requires taxonomy import (ArchiMate/BIAN/APQC) or human curation. Out of scope for this spec. |
| EARS/DMN authoring surface | Deferred to separate project | Forward-engineering. Phase 3 delivers *generated* EARS/DMN skeletons from the backbone, which is enough for now. |
| Markdown ↔ Graph round-trip editing | Deferred | Requires conflict resolution, canonical-source flags, bidirectional sync. Skip until human review actually happens in practice. |
| Runtime signal ingestion (logs, CDC, APM) | Out of scope | Each is a full integration project. Revisit when brownfield reality demands it. |
| Single monolithic LightRAG | Rejected | Per-system with federated linking (Phase 4) is the correct direction. |

---

## Part 5: Success Criteria

Backbone is "done" (Phase 2 exit) when:

1. Running the pipeline on a test repo produces a JSON artifact of the form `entity_state_machines.json` containing one `EntityStateMachine` per significant business entity
2. Each FSM transition carries: `from_state`, `to_state`, `guard_expr?`, `trigger_functions[]`, `entry_points[]`, `evidence[]`
3. The FSM artifact is diffable across runs (stable IDs for states and transitions)
4. At least three downstream artifacts can be generated from the FSM alone: BPMN diagram, DMN table, EARS skeleton (Phase 3)
5. Fixture validation: on the enterprise fixture, ≥80% of manually-identified entity state machines are correctly reconstructed (states + transitions) compared to human baseline

---

## Part 6: Open Questions

- ~~**Entity identity within a repo**: do we fingerprint by class name + field set, or by ORM table mapping, or both?~~ **Resolved (2026-04-21)**: Phase 2.4 uses class + field + bases fingerprint; Phase 2.5 adds ORM/SQL/schema field sources that feed into the same fingerprint. Full algorithm in `docs/guides/entity-identity/consolidation-algorithm.md`.
- **Guard AST normalization scope**: how much cross-language normalization is worth it vs. keeping raw per-language expressions? (Phase 1.2 decision; suggest: normalize simple binary ops only, retain raw otherwise.)
- **Execution-order branch semantics**: when a transition is inside `try/except` or equivalent, does the exception path produce an alternate transition? (Phase 1.4 decision.)
- **FSM confidence aggregation**: how do we combine per-transition confidences into an FSM-level quality score? (Phase 2 decision; current rollup uses simple average — revisit after 2.4 ships.)

These are called out now so they don't block task kickoff; answers can be deferred to the phase where they matter.
