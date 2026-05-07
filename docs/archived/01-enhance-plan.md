# Enhancement Plan: Behavior Reconstruction for Discovery App

> **Archived 2026-05-07.** This is a pre-implementation planning doc that proposed a "Tier 2.5 / Tier 3.5" LLM-tier scheme that never shipped. The actual implementation followed `docs/specs/2026-04-20-state-first-backbone-plan.md` instead — what this doc called "Tier 2.5" became pipeline phase 13 (`scenario_flow_inference`) and "Tier 3.5" was rolled into phase 15 (`visual_artifacts`). Kept here only for historical reference; do not use as current design guidance.

---

This document outlines the strategic evolution of the **Discovery App** from a static "code-understanding" tool (AST/Call-Graph) into a dynamic "behavior-reconstruction" engine (Scenario-Flow/BPMN).

---

## 1. Goal: From Code Understanding → Behavior Reconstruction

The current pipeline excels at **Structure** (AST), **Relationships** (Call Graph), and **Local Intent** (Summaries). The gap is **Process Flow**, **Data Lineage**, and **Business Decomposition (L1–L3)**.

### The Core Shift
Add an **Execution Flow Layer** between the static call-graph and the final rollup.

| Feature | AS-IS | TO-BE |
|---------|-------|-------|
| **Core Model** | Static Call Graph | Scenario-Aware Execution Graph |
| **Logic** | Individual functions | Multi-signal Execution Slices |
| **Sequencing** | None (Topological) | State-Transition + Dependency Chains |
| **Artifacts** | Markdown Summaries | Sequence Diagrams + BPMN 2.0 + IPO Tables |

---

## 2. Updated Pipeline Architecture

1.  **Resolve & Detect** (Current)
2.  **Parse (Tree-sitter)** (Current)
3.  **Build Call Graph** (Current)
4.  **NEW: Execution Slice Builder (Step 4.5)** 
    - Identify Scenario Roots (APIs, Scheduled Jobs, CLI, Event Consumers).
    - Extract bounded DFS/BFS slices from roots to IO boundaries (DB, API, Queue).
5.  **Smart Chunk & Embed** (Current)
6.  **Summarize (Tier 1)** (Current)
7.  **NEW: Flow Inference (Tier 2.5 — Steps 8.5–8.8)**
    - **Step (legacy 8.5) →: Path Extraction (P1)**: Prune call-graphs into candidate paths.
    - **Step 8.6: Flow Inference (P2)**: Convert paths to business terms via GenAI.
    - **Step 8.7: Data Flow (P3)**: Extract Input-Process-Output (IPO).
    - **Step 8.8: Interface Detection (P4)**: Map external systems (DB, Kafka, HTTP).
8.  **NEW: BPMN/Diagram Synthesis (Step 11.5)**
    - Convert inferred flows into Mermaid Sequence diagrams and BPMN 2.0 XML.

---

## 3. Implementation Pillars

### Pillar A: State-Transition Anchoring (The Backbone)
Instead of guessing sequence, detect entity state changes in code.
- **Pattern Matching**: Target assignments to `*.status`, `*.state`, `*.stage`.
- **Logic**: If `submitOrder()` sets `status = SUBMITTED` and `approveOrder()` sets `status = APPROVED`, a high-confidence link `SUBMITTED → APPROVED` is established.
- **Signals**: Guard conditions (`if status == DRAFT`), method names (`approve`, `finalize`), and Read-after-Write patterns.

### Pillar B: Multi-Signal Scoring Model
Since pure call graphs are noisy, sequence nodes based on a weighted scoring model:
- **Call Order (+5)**: AST/Control-flow order.
- **State Transition (+4)**: Continuity in entity status.
- **Data Dependency (+3)**: Output of A is input to B.
- **Read-after-Write (+3)**: A writes `status=X`, B reads `status=X`.
- **Event Chains (+4)**: Publish/Consume pairs.

### Pillar C: BPMN 2.0 Synthesis
Map execution models to business primitives:
- **Start Event**: Scenario Roots.
- **Service Task**: System functions (collapsed business steps).
- **User Task**: Inferred manual steps (Manager Approval, Review).
- **Gateways**: Conditional logic (`if/else` detected in AST).
- **Data Store**: DB interactions.

---

## 4. Phased Implementation Plan

### Phase 1: Data Model & Schema Evolution
- **Action**: Update `src/ai_discovery/graph/models.py`.
- **New Symbols**: `ExecutionNode`, `ExecutionEdge`, `Scenario`.
- **Goal**: Support typed graphs with confidence scores and conditional metadata.

### Phase 2: Execution Slicing & Boundary Detection
- **Action**: Update `src/ai_discovery/parsers/base.py` and `src/ai_discovery/graph/call_graph.py`.
- **Goal**: Implement bounded traversals that stop at persistence (DB) or integration (API/Queue) boundaries.

### Phase 3: GenAI Flow Reconstruction (Tier 2.5)
- **Action**: Create `src/ai_discovery/ai/flow_inference.py`.
- **Goal**: Staged prompts to abstract technical call-stacks into "Process Flows" and detect IPO patterns.

### Phase 4: Artifact Generation (Tier 3.5)
- **Action**: Create `src/ai_discovery/output/bpmn_generator.py`.
- **Goal**: Template-driven generation of Mermaid, PlantUML, and BPMN 2.0 XML.

---

## 5. Technical Requirements

- **Parsers**: Enhance Tree-sitter queries for boundary detection (HTTP, DB, Queue).
- **AI Tiers**:
  - **Tier 2.5**: Path Pruning and Flow Inference.
  - **Tier 3.5**: Diagram Code Generation.
- **Templates**: New Jinja2 templates for `process-flow.md`, `interface-catalog.md`, and `data-model.md`.

---

## 6. Success Metrics
- **Accuracy**: 80%+ of inferred flows align with actual system state transitions.
- **Readability**: BPMN diagrams contain 7–15 tasks (collapsing trivial helpers).
- **Traceability**: Every business step in the doc maps back to a set of functions and files.
