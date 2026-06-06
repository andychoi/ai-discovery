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
Every heuristic (4-stage graded call-resolution confidence, domain classification, framework detection) has documented reasoning in `docs/architecture/decisions.md`. When changing a heuristic, update both code and documentation.

### 3. Confidence Scoring Is Multi-Signal
No single heuristic is authoritative. Call resolution uses depth, state transitions, data boundaries, external APIs, and read-after-write patterns. See `docs/guides/call-graph/resolution-heuristics.md` for details.

### 4. Parser Extension Is Templated
Adding a new language (Go, Rust, etc.) follows a checklist in `docs/guides/parsers/extension-checklist.md`. Don't improvise; follow the template.

### 5. Measure Before Optimizing
Profile phases to find bottlenecks (Phase 6: parsing? Phase 14: Tier 1? Phase 16: Tier 3?). See `docs/guides/pipeline/profiling.md`.

### 6. Screen-Centric Documentation as User Entry Point
New feature (v0.3+): Generate screen-centric specs that serve as user-facing entry points to the entire system. Screens link OUT to supporting docs (backend, batch, database, interfaces). This reverses the traditional domain-centric model—instead of organizing by business domain, organize by user-facing screens.

---

## Screen-Centric Spec Generation (v0.3+)

### Overview

Traditional ai-discovery generates **domain-centric docs** (organized by business domain). The new screen-centric mode generates **screen-centric docs** (organized by user-facing screens) that link to supporting domain/backend/database docs.

**Key idea**: Screens are the entry point. Users navigate by the menu they see, not by business domains.

### Workflow

1. **Auto-detect menu system** (`discover detect-screens <repo>`)
   - Scans for menu definitions: JSON/YAML files, TypeScript constants, framework routing
   - Extracts screen definitions and menu hierarchy
   - Outputs `screen_map.yaml` and `menu_tree.json`

2. **Map screens to backend** (integrated with existing `discover scan`)
   - For each detected screen, links to:
     - Frontend APIs it calls
     - Backend controllers/services
     - Database tables accessed
     - Batch jobs triggered
     - External interfaces (EAI/ETL)
   - Computes source file hashes for drift detection

3. **Generate screen specs** (integrated with existing `discover scan`)
   - Creates one spec per screen (combined format, not split tech/func)
   - Sections: Purpose, When Used, User Actions, Rules, Data, Downstream Effects, Permissions, Open Items, Technical Reference
   - Frontmatter includes drift-detection hashes

4. **Detect drift** (`discover verify-drift <repo> --spec-dir ./docs/screens`)
   - Reads `source_hashes` from each spec
   - Compares against current source files
   - Reports which specs are out of sync
   - Exit non-zero for CI integration

### Integration with Existing Pipeline

**No replacement, no parallel tracks**: Screen specs are NEW entry points that LINK to existing docs.

```
docs/
├── screens/                    # NEW: User entry point
│   ├── customer-search.md
│   ├── customer-edit.md
│   └── ...
├── ASIS/, ASD/, ASSC/         # EXISTING: Domain-centric docs
├── PF/, BPMN/, DMN/           # EXISTING: Process/visual artifacts
├── database/                   # EXISTING: Table schemas
├── batch-jobs/                 # EXISTING: Batch job specs
└── interfaces/                 # EXISTING: External interface specs
```

Screen specs **link out** to supporting docs. Screens do NOT replace domain docs.

### CLI Commands

```bash
# Detect menu system and extract screens
discover detect-screens /path/to/repo --output ./data

# Check if specs are out of sync with source code
discover verify-drift /path/to/repo --spec-dir ./data/specs

# Export the canonical knowledge-graph JSON (nodes, call edges, domains, FKs,
# FSMs, doc/screen index) — portable, diffable view of a completed scan
discover export-graph -p myproject
```

### Supported Menu Formats (Hybrid Detection)

All five formats are **functional** (detection order = priority; first match wins):

- **JSON/YAML files** (`menu.json`, `navigation.yaml`, etc.) — only leaf menu entries become screens.
- **TypeScript/JS constants** (`export const MENU = [...]`) — tree-sitter parsing via `route_parser.py`.
- **Framework routing** (Vue Router, React Router incl. JSX `<Routes>`, Angular routes) — `route_parser.py` adapters; routes with a component (and no redirect/catch-all) become screens.
- **WebForms** (`.aspx` pages; folder hierarchy = menu) — fallback when no JS menu/router exists.
- **JSP** (`.jsp`/`.jspx` pages; folder hierarchy = menu) — fallback for Spring/Java server-rendered apps.

> When NO format matches, the run says so explicitly — `detect_and_build_screens`
> logs "No menu system detected — tried: … screen generation skipped" and the
> pipeline prints the formats tried (never a silent 0-screen run). Non-menu
> screens (popups, wizards, modals, deep-links, role-conditional) are still
> not detected by any format.

### Drift Detection

Each screen spec includes `source_hashes` in frontmatter—SHA256 of all source files the screen depends on. When source changes:

```yaml
---
doc_id: myapp-screen-customer-search
source_hashes:
  src/pages/Customer/SearchPage.vue: abc123def456
  src/main/java/.../CustomerController.java: def456ghi789
  migration_001_customer.sql: ghi789jkl012
---
```

`discover verify-drift` flags drifted specs for regeneration (cost-saving: only regen what changed).

---

## Using Skills

Five custom skills accelerate development. The first three help build the discovery pipeline itself; the last two run *after* a scan to verify and improve its output.

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

### `/discover-triage`
**When**: After a `discover scan` run, before treating its output as authoritative. Verify low-confidence rows against source code.

**What it does**: Reactive, bounded review of flagged rows only:
1. Prompt user for source repo path + discovery output path (or project slug)
2. Pull rows where `generated_docs.confidence < 0.65` and `review_claims.status IN ('contradicted','unverified')`
3. For each flagged row, read source at the cited file:line and verify
4. Decide accept (RAG miss) / correct (LLM error) / human-review (genuinely ambiguous)
5. Emit `triage-report.md`; apply corrections only on user confirmation

**Reference**: `docs/guides/output-review/triage-workflow.md`

---

### `/discover-consistency`
**When**: After triage, or as a periodic audit. Verify cross-artifact links and flag domain-pattern absences.

**What it does**: Read-only consistency check across the output tree:
1. Prompt user for discovery output path (or project slug)
2. Build the link graph from `links_to` frontmatter across ASIS / ASD / ASSC / PF / BPMN / DMN / EARS / IMPACT
3. Check coverage: every endpoint in PF, every entity in EARS + IMPACT, every UC linked to a PF, every gateway in DMN
4. Flag domain-pattern absences (logout, refund, password-reset for e-commerce; etc.) — distinguishing **code gaps** from **discovery failures** via source grep
5. Emit `consistency-report.md`; never edits artifacts in place

**Reference**: `docs/guides/output-review/consistency-workflow.md`

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

**Import-map tier**: Before writing a full tree-sitter parser, note the lightweight import-map tier (`parsers/import_map.py`) covers Go/Rust/Ruby/PHP via regex — it extracts imports, top-level symbols, and conservative call sites (no endpoint/entity extraction), turning "unsupported language" into a gradient. It's an on-ramp, not a replacement for a real AST parser.

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

### Task: "I ran `discover scan` — how do I check quality?"

1. Open the viewer: `discover view -p <slug>` — quality targets, confidence histogram, weakest-docs ranking, artifact presence, clickable diagrams
2. Spot-check one entity end-to-end: `discover impact <Entity> -p <slug>`
3. Probe coverage gaps: `discover chat -p <slug>`
4. Audit low-confidence edges: `discover query "SELECT ... FROM call_edge WHERE confidence < 0.65 ..."`
5. Onboard onto a domain: read the per-domain tour guides under `ONBOARD/` (`generators/onboarding_generator.py`) — a pedagogical path through the resolved call graph, entry point first.

**Reference**: `docs/guides/exploring-results/navigation.md`

---

### Task: "A `--prod` scan finished but ASIS/ASD/ASSC rollups are missing"

`--prod` swaps in the `tier3p` model slot for doc rollup. If that model isn't enabled in your Bedrock account/region, **every Tier-3 rollup fails** with `ValidationException: The provided model identifier is invalid` — the scan still exits 0 (Tier-1/2, screens, PF, FK, dependency catalog all run), so it's easy to miss.

1. Check the scan log for repeated `Rollup failed for …/as-is*: … model identifier is invalid`.
2. Set `bedrock.tier3p` in `discovery.yaml` to a model you can invoke (e.g. the same Sonnet as `tier2`, or a current Opus); verify against your account's enabled models.
3. Re-run with `--prod` (or resume from the rollup phase). `tier3d` (non-`--prod`) is independent and unaffected.

---

### Task: "Add support for Go (or another language)"

1. Follow `/parser-extension` skill checklist
2. Check tree-sitter grammar exists
3. Collect test corpus (3–5 real Go projects)
4. Map AST types (function, method, class, call)
5. Implement name resolution (Go-specific qualified names)
6. Test on real code
7. Measure confidence baseline (target: ≥ 80% accuracy)
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
- Measure accuracy (target: ≥ 85% correct resolution)
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
| Call resolution accuracy | ≥ 85% | Confidence scoring is load-bearing |
| Low-confidence edge ratio | ≤ 15% | Too many low-conf edges means bad coverage |
| Parser accuracy per language | ≥ 80% | Parsing is phase 6; errors compound downstream |
| Tier 1 cost per LOC | ≤ $0.001 | Cost-effective, scalable |
| Tier 2/3 quality baseline | Opus model | Deep reasoning, high-quality docs |

---

## See Also

- `docs/INDEX.md` — Full navigation guide
- `docs/architecture/` — System design & decisions
- `docs/guides/` — How-to reference (call-graph, parsers, pipeline)
- `docs/specs/` — Design records per session
