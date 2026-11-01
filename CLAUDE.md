# AI-Discovery: Development Infrastructure

A sophisticated brownfield reverse-engineering engine that parses code, infers business processes, and generates SDLC documentation using a tiered LLM pipeline.

---

## Quick Links

**v1 Roadmap (active development)**:
- **Start here for implementation work**: `docs/roadmap/README.md` — protocol, lanes, read order
- Next ready items: `python scripts/roadmap_lint.py --next`; or run the `/roadmap-next` skill
- Item status lives only in `docs/roadmap/milestones/M*.md`; log every session in `docs/roadmap/PROGRESS.md`
- repowise (`../repowise`) is AGPL — learn from `docs/roadmap/research/edge-case-catalog.md`, never copy its code (ADR-0006)

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
Every heuristic (call-resolution origins with one confidence each — ADR-0004, domain classification, framework detection) has documented reasoning in `docs/architecture/decisions.md`. When changing a heuristic, update both code and documentation.

### 3. Confidence Scoring Is Multi-Signal
No single heuristic is authoritative. Call resolution uses depth, state transitions, data boundaries, external APIs, and read-after-write patterns. See `docs/guides/call-graph/resolution-heuristics.md` for details.

### 4. Parser Extension Is Templated
Adding a new language (Go, Rust, etc.) follows a checklist in `docs/guides/parsers/extension-checklist.md`. Don't improvise; follow the template.

### 5. Measure Before Optimizing
Profile phases to find bottlenecks (Phase 6: parsing? Phase 11: Tier 1? Phase 12–13: Tier 2? page prose?). See `docs/guides/pipeline/profiling.md`.

### 6. Screen-Centric Documentation as User Entry Point
New feature (v0.3+): Generate screen-centric specs that serve as user-facing entry points to the entire system. Screens link OUT to supporting docs (backend, batch, database, interfaces). This reverses the traditional domain-centric model—instead of organizing by business domain, organize by user-facing screens.

---

## Screen-Centric Spec Generation (v0.3+)

### Overview

Traditional ai-discovery generates **domain-centric docs** (organized by business domain). The new screen-centric mode generates **screen-centric docs** (organized by user-facing screens) that link to supporting domain/backend/database docs.

**Key idea**: Screens are the entry point. Users navigate by the menu they see, not by business domains.

### Workflow

1. **Auto-detect menu system** (part of `discover scan`; `discover scan --only screens` runs just this stage)
   - Scans for menu definitions: JSON/YAML files, TypeScript constants, framework routing
   - Extracts screen definitions and menu hierarchy into the index (screen pages in the wiki)

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

4. **Detect drift** (`discover drift --type screen`, or `discover drift` for every page)
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
```

> Everything else is a wiki page (`discover serve`; `discover export --format markdown|html|json`): domain,
> flow, onboarding, entity (FSM + DMN + EARS), table, endpoint, batch-job, stored-procedure and interface
> pages, plus `discover impact` / the `/impact` view. The legacy ASIS/ASD/ASSC/PF/BPMN/DMN/EARS/INTERFACES/
> ONBOARD markdown tree was removed in M3.7.b.

Screen specs **link out** to supporting docs. Screens do NOT replace domain docs.

### CLI Commands

```bash
# Scan (screens included); only the screen stage
discover scan
discover scan --only screens

# Have the sources behind the pages changed since the scan? (exit 6 on drift; CI-friendly)
discover drift
discover drift --type screen

# Export the knowledge-graph JSON (nodes, edges, domains, FKs, FSMs, page index) — portable, diffable
discover export --format json        # also: --format html (wiki from file://), --format markdown

# Deliver the wiki to DocHub / Gitea (one document per page)
discover push --target dochub
```

The full, generated command reference is in README.md ("CLI Reference"; `scripts/gen_cli_reference.py`).

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

`discover drift` flags drifted pages for regeneration (cost-saving: only regen what changed).

---

## Using Skills

Five custom skills accelerate development. The first three help build the discovery pipeline itself (`.claude/skills/<name>/SKILL.md`, thin wrappers over the guides); the last two run *after* a scan to verify its output and ship in the Claude Code plugin (`plugin/skills/<name>/SKILL.md`, roadmap M5.4). `/roadmap-next` (`.claude/skills/roadmap-next/`) drives roadmap items. `tests/test_plugin.py` fails if a skill named here has no file.

**Plugin:** `plugin/` bundles the MCP server and the post-scan skills — `claude plugin marketplace add ./plugin && claude plugin install ai-discovery@ai-discovery` (see `plugin/README.md`). Without the plugin, `discover mcp install --target claude` wires the server alone.

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

**What it does**: Reactive, bounded review of flagged items only (≤ 40):
1. `get_overview` for freshness; open `.discovery/discovery.db` read-only
2. Pull low-confidence pages (< 0.65), guessed call edges (origin confidence < 0.75), unresolved calls that may hide edges, screens without a backend chain
3. For each item, read the source at the cited file:line and verify
4. Decide accept / correct / human-review; name repeated patterns once
5. Emit `.discovery/reports/triage-report.md`; add corrections as page notes only on user confirmation

**Skill**: `plugin/skills/discover-triage/SKILL.md` · **Background**: `docs/guides/output-review/triage-workflow.md` (pre-v1 tables)

---

### `/discover-consistency`
**When**: After triage, or as a periodic audit. Verify cross-artifact links and flag domain-pattern absences.

**What it does**: Read-only consistency check across the v1 index:
1. `get_overview` for freshness; open `.discovery/discovery.db` read-only
2. Link integrity: dangling page links
3. Coverage: unreached endpoints, untouched tables, entities without transitions, unresolved-call hot spots (> 15%)
4. Flag expected-flow absences (logout, refund, password-reset for e-commerce; etc.) — distinguishing **code gaps** from **discovery misses** via source grep
5. Emit `.discovery/reports/consistency-report.md`; never edits anything

**Skill**: `plugin/skills/discover-consistency/SKILL.md` · **Background**: `docs/guides/output-review/consistency-workflow.md` (pre-v1 tables)

---

## Hard Problem Areas

### Area A: Call Graph Resolution

**Why it's hard**: 
- Same function name can exist in multiple modules
- Dynamic calls can't be resolved statically
- Confidence scoring must balance false positives vs false negatives

**Key insight**: Multi-signal confidence (0.5–1.0) allows nuanced decisions. A call with 0.65 confidence is drawn in the graph view but marked for review; flow traces follow only resolved edges. Process views are evidence-backed, with no BPMN from static analysis (ADR-0007).

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
2. Identify bottleneck phase (parsing? Tier 1? Tier 2? page prose?)
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

1. Open the wiki: `discover serve` — pages with confidence and freshness badges, citations with source view, search, notes; `discover status` for scan health
2. Spot-check one entity end-to-end: `discover impact entity:<Entity>` (or any symbol / `table:` / `screen:`)
3. Probe coverage gaps: `discover ask "<what you expect to find>"`
4. Audit low-confidence edges: `sqlite3 .discovery/discovery.db "SELECT ... FROM call_edges WHERE confidence < 0.65 ..."`
5. Onboard onto a domain: open its onboarding page in the wiki (`onboarding:<domain>`, built by `pages/builders/flows.py`) — a pedagogical path through the resolved call graph, entry point first.

**Reference**: `docs/guides/exploring-results/navigation.md`

---

### Task: "A `quality: final` scan spent more but the narratives look the same"

`quality: final` (formerly `--prod`) adds source-verification passes — scenario flows and screen specs are
claim-checked against the code (phase 17) — and lets the `deep` role be used where configured. If a model id
isn't enabled in your Bedrock account/region, **every call on that role fails** with `ValidationException: The
provided model identifier is invalid` while the scan still completes.

1. Check the scan's degradations (`discover status`) and the log for `model identifier is invalid`.
2. Set `bedrock.fast|standard|deep` (or top-level `models.*`) to models you can invoke; `discover doctor --llm`
   probes the configuration.
3. Page narratives (`--prose`) always use the `standard` role (M3.7.a) and are grounded (M3.8).

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
