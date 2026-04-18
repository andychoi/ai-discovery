# AI-Discovery: Development Infrastructure

A sophisticated brownfield reverse-engineering engine that parses code, infers business processes, and generates SDLC documentation using a tiered LLM pipeline.

---

## Quick Links

**Documentation**:
- **Start here**: `docs/INDEX.md` — Navigation guide
- **Architecture**: `docs/architecture/overview.md` — High-level design
- **Guides**: `docs/guides/` — How-to reference for development

**Development Workflows**:
- `docs/guides/call-graph/` — Call resolution & debugging (Area A)
- `docs/guides/parsers/` — Adding language support (Area D)
- `docs/guides/pipeline/` — Performance & cost optimization

**Design Records**:
- `docs/architecture/decisions.md` — Why we chose specific heuristics
- `docs/specs/` — Design decisions per session

---

## Development Principles

### 1. Phase-Forward Workflow
Design → Plan → Implement → Verify. Don't skip design.

### 2. Heuristics Are Load-Bearing
Every heuristic (7-level confidence scoring, domain classification, framework detection) has documented reasoning in `docs/architecture/decisions.md`. When changing a heuristic, update both code and documentation.

### 3. Confidence Scoring Is Multi-Signal
No single heuristic is authoritative. Call resolution uses depth, state transitions, data boundaries, external APIs, and read-after-write patterns. See `docs/guides/call-graph/resolution-heuristics.md` for details.

### 4. Parser Extension Is Templated
Adding a new language (Go, Rust, etc.) follows a checklist in `docs/guides/parsers/extension-checklist.md`. Don't improvise; follow the template.

### 5. Measure Before Optimizing
Profile phases to find bottlenecks (Phase 6: parsing? Phase 14: Tier 1? Phase 16: Tier 3?). See `docs/guides/pipeline/profiling.md`.

---

## Using Skills

Three custom skills accelerate development in the hard problem areas:

### `/parser-extension`
**When**: You want to add Go, Rust, Java, C#, JavaScript, or any other language.

**What it does**: Guided workflow for:
1. Pre-flight (grammar availability, test corpus)
2. AST mapping (function, class, call types)
3. Name resolution (qualified names, imports)
4. Framework detection (common libraries)
5. Test harness (unit + integration tests)
6. Integration (register in pipeline)
7. Validation (accuracy baseline)

**Reference**: `docs/guides/parsers/extension-checklist.md`

---

### `/call-graph-debug`
**When**: You're debugging why a call resolved incorrectly, or validating that heuristics are working.

**What it does**: Step-by-step tracing:
1. Identify suspect call (file, line, function name)
2. Trace call through resolution logic
3. Analyze confidence signals (which signals contributed?)
4. Validate against source code (is the resolution correct?)
5. Decide: accept score, adjust heuristics, or mark unresolved
6. Document findings

**Reference**: `docs/guides/call-graph/debugging-workflow.md`

---

### `/pipeline-analyze`
**When**: You want to understand where time/money is being spent, or optimize for speed/cost.

**What it does**: Profiling & analysis:
1. Run pipeline with profiling enabled
2. Identify bottleneck phases (parsing? LLM? embedding?)
3. Analyze per-domain costs (which domains are expensive?)
4. Recommend optimizations (trade-offs explained)
5. Measure impact before/after

**Reference**: `docs/guides/pipeline/profiling.md`

---

## Hard Problem Areas

### Area A: Call Graph Resolution

**Why it's hard**: 
- Same function name can exist in multiple modules
- Dynamic calls can't be resolved statically
- Confidence scoring must balance false positives vs false negatives

**Key insight**: Multi-signal confidence (0.5–1.0) allows nuanced decisions. A call with 0.65 confidence might be good enough for BPMN but needs human review in tech specs.

**Start here**: `docs/guides/call-graph/resolution-heuristics.md`

---

### Area D: Language Parser Extension

**Why it's hard**:
- Each language has different AST structure (tree-sitter helps)
- Naming conventions vary (snake_case vs camelCase vs PascalCase)
- Framework patterns are language-specific (Flask vs Spring vs Express)

**Key insight**: Template approach (in `extension-checklist.md`) makes adding languages straightforward. Don't invent new patterns; follow the template.

**Start here**: `docs/guides/parsers/extension-checklist.md`

---

## Common Tasks

### Task: "The pipeline is slow. Where should I optimize?"

1. Run with profiling: `discover scan repo --project-slug myproj --profile`
2. Identify bottleneck phase (parsing? Tier 1? Tier 3?)
3. Check `/pipeline-analyze` for optimization strategies
4. Measure cost/benefit trade-offs
5. Update discovery.yaml and re-profile

**Reference**: `docs/guides/pipeline/profiling.md`

---

### Task: "A call didn't resolve correctly. Why?"

1. Find the suspect call (file, line, function name)
2. Use `/call-graph-debug` skill
3. Trace through resolution (which level matched?)
4. Analyze signals (depth, state transitions, etc.)
5. Validate against source code
6. Decide: change heuristic or accept score

**Reference**: `docs/guides/call-graph/debugging-workflow.md`

---

### Task: "Add support for Go (or another language)"

1. Follow `/parser-extension` skill checklist
2. Check tree-sitter grammar exists
3. Collect test corpus (3–5 real Go projects)
4. Map AST types (function, method, class, call)
5. Implement name resolution (Go-specific qualified names)
6. Test on real code
7. Measure confidence baseline (target: >= 80% accuracy)
8. Register in lang_detector.py

**Reference**: `docs/guides/parsers/extension-checklist.md`

---

## Testing Strategy

### Unit Tests
- Test individual heuristics (exact match, prefix overlap, etc.)
- Test signal scoring (depth, state transitions, etc.)
- Test parser AST mapping (function, class, call types)

### Integration Tests
- Test heuristics working together (resolve realistic call chains)
- Test parsers on real code snippets
- Test full pipeline on small test corpus

### Corpus Validation
- Collect 3–5 real repositories per language
- Manually validate ~10% of resolved calls
- Measure accuracy (target: >= 85% correct resolution)
- Record baseline for regression testing

**Reference**: `docs/guides/call-graph/test-strategy.md`

---

## File Organization

```
ai-discovery/
├── CLAUDE.md ← You are here
├── README.md
├── src/ai_discovery/
│   ├── graph/call_graph.py (Area A: call resolution)
│   ├── parsers/ (Area D: language-specific parsing)
│   └── ...
├── docs/
│   ├── INDEX.md (Navigation guide)
│   ├── architecture/
│   │   ├── overview.md (High-level design)
│   │   ├── components.md (Module breakdown)
│   │   └── decisions.md (Heuristic rationale)
│   ├── guides/
│   │   ├── call-graph/ (Debugging, heuristics, testing)
│   │   ├── parsers/ (Architecture, checklist, patterns)
│   │   └── pipeline/ (Phases, costs, profiling)
│   └── specs/
│       └── 2026-04-17-dev-infrastructure-design.md (This design)
└── tests/
    ├── fixtures/ (Test corpus per language)
    └── test_*.py (Unit + integration tests)
```

---

## Design Records

**Current**: `docs/specs/2026-04-17-dev-infrastructure-design.md`

This document records:
- Documentation reorganization (architecture → guides → specs)
- Three custom skills (parser-extension, call-graph-debug, pipeline-analyze)
- Modular, focused approach for solo development
- Design approval and implementation plan

---

## Extending This CLAUDE.md

When you learn something new about development workflow, add it here:

- New heuristic? Document in `docs/architecture/decisions.md` *and* update relevant guide
- New task pattern? Add a "Common Tasks" section above
- Discovered anti-pattern? Add to principles or specific guide sections

Keep this file as the index; detailed docs live in subdirectories.

---

## Key Metrics & Targets

| Metric | Target | Why |
|--------|--------|-----|
| Call resolution accuracy | >= 85% | Confidence scoring is load-bearing |
| Low-confidence edge ratio | <= 15% | Too many low-conf edges means bad coverage |
| Parser accuracy per language | >= 80% | Parsing is phase 6; errors compound downstream |
| Tier 1 cost per LOC | <= $0.001 | Cost-effective, scalable |
| Tier 2/3 quality baseline | Opus model | Deep reasoning, high-quality docs |

---

## See Also

- `docs/INDEX.md` — Full navigation guide
- `docs/architecture/` — System design & decisions
- `docs/guides/` — How-to reference (call-graph, parsers, pipeline)
- `docs/specs/` — Design records per session
