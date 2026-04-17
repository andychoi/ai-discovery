---
title: AI-Discovery Development Infrastructure Design
date: 2026-04-17
status: approved
scope: Documentation reorganization + custom skill framework
---

# AI-Discovery Development Infrastructure Design

## Executive Summary

This spec designs a modular development infrastructure for AI-Discovery, a complex reverse-engineering engine. The system has two hard problem areas: (A) call graph resolution & confidence scoring, and (D) language parser extension. Solo development with phase-forward workflow requires:

1. **Clear mental models** for heuristics (not just automated tools)
2. **Debugging utilities** for tracing & validating resolution
3. **Templated workflows** for parser extension
4. **Organized documentation** that connects architecture to guides to decisions

**Deliverables**:
- Reorganized `/docs/` structure (architecture → guides → specs)
- Project CLAUDE.md with quick reference
- Three custom skills: parser-extension, call-graph-debugging, pipeline-analyze
- Global CLAUDE.md entries linking to project skills

---

## Part 1: Documentation Reorganization

### Directory Structure

```
docs/
├── architecture/                    ← System design & decisions
│   ├── overview.md                 (high-level purpose, L1–L7 framework)
│   ├── pipeline.md                 (13 phases, data flow)
│   ├── components.md               (module breakdown, responsibilities)
│   └── decisions.md                (why: heuristics, trade-offs, constraints)
│
├── guides/                         ← How-to & reference (for developers)
│   ├── call-graph/
│   │   ├── resolution-heuristics.md     (7-level scoring, signals, examples)
│   │   ├── debugging-workflow.md        (step-by-step: trace → analyze → validate)
│   │   └── test-strategy.md            (validation patterns, confidence checks)
│   │
│   ├── parsers/
│   │   ├── architecture.md             (how parsers work, tree-sitter setup)
│   │   ├── extension-checklist.md      (template for new language)
│   │   └── language-patterns.md        (Python vs Java vs C# AST differences)
│   │
│   └── pipeline/
│       ├── phase-breakdown.md          (details of each 13 phases)
│       ├── cost-tracking.md            (LLM budget, tier routing, tracking)
│       └── profiling.md                (bottleneck ID, metrics, optimization)
│
├── specs/                          ← Design decisions (per session)
│   └── 2026-04-17-dev-infrastructure-design.md  (this file)
│
└── [preserved]
    ├── architecture.md             (kept for reference during migration)
    ├── flow.md                     (kept for reference during migration)
    └── IMPLEMENTATION_SUMMARY.md   (kept for reference during migration)
```

### Migration Strategy

**Phase 1: Content Reorganization** (no deletions yet)
- Create new `/docs/architecture/` and `/docs/guides/` directories
- Migrate content from `docs/architecture.md` → `docs/architecture/overview.md` + `components.md`
- Create guide files with cross-references to existing docs

**Phase 2: Index & Cross-Reference**
- Create `docs/INDEX.md` as the main entry point
- Update root README.md to point to `docs/INDEX.md`
- Add internal links between old and new locations

**Phase 3: Cleanup** (when confident in new structure)
- Archive old files as `docs/archive/` (keep for history)
- Remove references from root directory

### Content Mapping

| Old File | New Location(s) | Reason |
|----------|-----------------|--------|
| `docs/architecture.md` | `docs/architecture/overview.md` + `components.md` | Split into conceptual overview + module breakdown |
| `docs/flow.md` | `docs/guides/pipeline/phase-breakdown.md` | Detailed phase information → guide section |
| `docs/IMPLEMENTATION_SUMMARY.md` | `docs/specs/YYYY-MM-DD-*.md` | Implementation records belong in specs |
| (new) | `docs/guides/call-graph/*` | Call graph is a hard problem area (A) |
| (new) | `docs/guides/parsers/*` | Parser extension is a hard problem area (D) |
| (new) | `docs/architecture/decisions.md` | Heuristics need documented reasoning |

---

## Part 2: Project-Level CLAUDE.md

File: `/Users/andymini/ai/ai-discovery/CLAUDE.md`

**Purpose**: Quick reference for this project's structure, skills, and development workflows.

**Contents**:
1. **Quick Links** — architecture, guides, design specs
2. **Development Principles** — phase-forward, heuristics as load-bearing, multi-signal confidence, templated extension
3. **Common Tasks** — which skill to invoke for which situation
4. **Testing Strategy** — how to validate parsers & call resolution
5. **Design Decisions** — why we chose specific approaches (references to `docs/architecture/decisions.md`)

**Key Sections**:
- When to use `/parser-extension` (adding a language)
- When to use `/call-graph-debug` (validating resolution)
- When to use `/pipeline-analyze` (profiling & optimization)

---

## Part 3: Three Custom Skills

### Skill 1: `ai-discovery:parser-extension`

**Purpose**: Phased workflow for adding support for a new language (Java, Go, Rust, etc.)

**Scope**:
1. **Pre-flight** — Check tree-sitter grammar availability
2. **AST Mapping** — Map language-specific AST nodes to CodeNode types (class, function, call, etc.)
3. **Name Resolution** — Define qualified-name heuristics for this language
4. **Test Corpus** — Collect 3–5 real-world repos in target language
5. **Validation** — Measure resolution accuracy, confidence distribution
6. **Integration** — Wire parser into lang_detector.py, pipeline.py
7. **Documentation** — Record language-specific patterns in `docs/guides/parsers/language-patterns.md`

**Output**: 
- Runnable parser module (e.g., `app/parsers/go.py`)
- Test harness with corpus
- Documentation of language-specific heuristics

**Linked Docs**: `docs/guides/parsers/architecture.md`, `docs/guides/parsers/extension-checklist.md`

---

### Skill 2: `ai-discovery:call-graph-debugging`

**Purpose**: Analyze & validate call resolution for a code slice (for validating/tuning heuristics)

**Scope**:
1. **Select Code Slice** — Pick a function or module to analyze
2. **Trace Calls** — Show all call resolution attempts for each unresolved reference
3. **Analyze Signals** — For each candidate, show confidence score breakdown (7 signals)
4. **Validate** — Compare resolved targets against source code (manual verification)
5. **Identify Issues** — Which signals were weak? Where did heuristics fail?
6. **Refine** — Decide: change heuristic or accept the score?
7. **Document** — Record finding in project notes

**Workflow is investigation-driven**: Start with a suspect call, trace through the resolution logic, measure signal contributions, validate against reality, document findings.

**Output**:
- Debugging report (which calls were hard to resolve, why)
- Confidence score breakdown per call
- Suggestions for heuristic tuning

**Linked Docs**: `docs/guides/call-graph/resolution-heuristics.md`, `docs/guides/call-graph/debugging-workflow.md`, `docs/guides/call-graph/test-strategy.md`

---

### Skill 3: `ai-discovery:pipeline-analyze`

**Purpose**: Profile & understand pipeline performance, identify bottlenecks, optimize costs

**Scope**:
1. **Run Pipeline with Profiling** — Measure time per phase
2. **Cost Breakdown** — Which LLM tier is most expensive? (Haiku vs Sonnet vs Opus)
3. **Bottleneck Analysis** — Pick slowest phase, show per-component breakdown
4. **Resource Trends** — Memory, disk, token counts as repo size grows
5. **Optimization Opportunities** — What can we trade off? (speed vs quality, cost vs accuracy)
6. **Implement Changes** — Apply suggested optimization, re-profile to measure impact

**Output**:
- Performance profile (timing per phase)
- Cost report (per tier, per phase, total)
- Optimization recommendations with confidence
- Benchmark before/after

**Linked Docs**: `docs/guides/pipeline/phase-breakdown.md`, `docs/guides/pipeline/cost-tracking.md`, `docs/guides/pipeline/profiling.md`

---

## Part 4: Global CLAUDE.md Updates

File: `~/.claude/CLAUDE.md`

**Addition** (after existing content):

```markdown
## ai-discovery Project Skills

When working in `/Users/andymini/ai/ai-discovery/`, use these project-specific skills:

### /parser-extension
Add support for a new language (Java, Go, Rust, etc.). Guided workflow: pre-flight → AST mapping → name resolution → test corpus → validation → integration.

**When to use**: You want to add Python/Java/C#/Go/Rust/etc. support to the parser.

### /call-graph-debug
Trace & validate call resolution for a code slice. Analyze confidence signals, validate against source, identify which heuristics need tuning.

**When to use**: You're debugging why a call didn't resolve correctly, or validating that a heuristic is working as designed.

### /pipeline-analyze
Profile pipeline performance, analyze LLM costs, identify bottlenecks, optimize for speed or cost.

**When to use**: You want to understand where time/money is being spent, or you're trying to make the pipeline faster/cheaper.
```

---

## Part 5: Implementation Plan Summary

**Phase 1: Documentation Structure** (this session)
- [ ] Create `docs/architecture/`, `docs/guides/` directories
- [ ] Move & split content from `docs/architecture.md`
- [ ] Create new guide files with stubs
- [ ] Create `docs/INDEX.md` as entry point

**Phase 2: Skill Templates** (this session)
- [ ] Create `docs/guides/parsers/extension-checklist.md` (parser workflow reference)
- [ ] Create `docs/guides/call-graph/debugging-workflow.md` (call graph tracing guide)
- [ ] Create `docs/guides/pipeline/profiling.md` (bottleneck analysis reference)

**Phase 3: CLAUDE.md Files** (this session)
- [ ] Create `/Users/andymini/ai/ai-discovery/CLAUDE.md` (project-level)
- [ ] Update `~/.claude/CLAUDE.md` (global, link to project skills)

**Phase 4: Create Skills** (next session)
- [ ] `ai-discovery:parser-extension` skill file + activation
- [ ] `ai-discovery:call-graph-debugging` skill file + activation
- [ ] `ai-discovery:pipeline-analyze` skill file + activation

**Phase 5: Git Commit**
- [ ] Commit reorganized docs
- [ ] Commit CLAUDE.md files
- [ ] Tag as "infrastructure-setup"

---

## Tradeoffs & Rationale

### Why Modular Skills?

| Choice | Alternative | Why Chosen |
|--------|-------------|-----------|
| 3 focused skills | 1 handbook skill | Solo dev needs clarity on *why* not just execution; each skill is independently useful and covers a distinct workflow |
| Guided workflows | Automated tools | Heuristics require human judgment; scripting would obscure the decision-making process |
| `/guides/` + `/architecture/` | Flat structure | Separates "what we built" from "how to work on it"; easier to navigate and maintain |
| CLAUDE.md at project level | Relying on global | Project-specific development context (three skills, phased workflow) doesn't belong in a global file; but global pointers help when context-switching |

### Why This Content Mapping?

- **Call graph & parsers in `/guides/`**: These are the hard problem areas (A & D); deserve detailed, discoverable how-to docs
- **Heuristics in `/architecture/decisions.md`**: Multi-signal scoring, 7-level confidence, MANUAL node injection—these are design choices that need documented reasoning
- **Specs per session**: Design records (this file, future design decisions) live in `/docs/specs/` with dates; easy to trace evolution

---

## Success Criteria

After implementation, this project should have:

1. ✅ **Clear mental models**: A developer can read `docs/guides/call-graph/resolution-heuristics.md` and understand why confidence scoring works the way it does
2. ✅ **Self-service debugging**: Using `/call-graph-debug` skill, a developer can trace a call, identify why it scored 0.65, and decide if the heuristic is right
3. ✅ **Templated extension**: Adding a new language is a checklist (in `/docs/guides/parsers/extension-checklist.md`), not a from-scratch effort
4. ✅ **Discoverable structure**: README → docs/INDEX.md → architecture/ or guides/ → specific docs
5. ✅ **Maintainable skills**: Each skill is focused, linked to specific guide docs, and independently useful

---

## Questions & Decisions Deferred

- **Automated skill code**: Skills are templated guides for now. If future development shows need for automation (e.g., parser scaffolding), we can add executable skill code.
- **CI/CD integration**: Project is local-only currently. Design allows for adding CI/CD later (test harnesses in skills can seed automated validation).
- **Team onboarding**: If team grows, this structure scales: CLAUDE.md explains skills, docs explain workflows, specs record decisions. Add team-specific docs in `/docs/team/` as needed.

---

## Files Affected

| File | Action | Content |
|------|--------|---------|
| `docs/architecture/overview.md` | Create | Migrated from `docs/architecture.md` + L1–L7 framework |
| `docs/architecture/components.md` | Create | Module breakdown from architecture.md |
| `docs/architecture/decisions.md` | Create | Design rationale for heuristics & trade-offs |
| `docs/guides/call-graph/resolution-heuristics.md` | Create | 7-level confidence scoring with examples |
| `docs/guides/call-graph/debugging-workflow.md` | Create | Step-by-step tracing guide |
| `docs/guides/call-graph/test-strategy.md` | Create | Validation patterns for confidence scoring |
| `docs/guides/parsers/architecture.md` | Create | Tree-sitter setup, AST mapping, name resolution |
| `docs/guides/parsers/extension-checklist.md` | Create | Templated workflow for new language |
| `docs/guides/parsers/language-patterns.md` | Create | Python vs Java vs C# AST differences |
| `docs/guides/pipeline/phase-breakdown.md` | Create | Details of 13 phases |
| `docs/guides/pipeline/cost-tracking.md` | Create | LLM budget, tier routing, cost analysis |
| `docs/guides/pipeline/profiling.md` | Create | Bottleneck identification, metrics |
| `docs/INDEX.md` | Create | Entry point, navigation guide |
| `CLAUDE.md` (project) | Create | Quick reference, skill links, principles |
| `~/.claude/CLAUDE.md` | Update | Add project skill references |
| `docs/architecture.md` | Keep | Reference during migration, archive later |
| `docs/flow.md` | Keep | Reference during migration, archive later |
| `docs/IMPLEMENTATION_SUMMARY.md` | Keep | Reference during migration, archive later |

---

## Appendix: Skill Invocation Examples

### Using `/parser-extension`

```
User: I want to add Go support
→ /parser-extension
→ Skill guides through: grammar check → AST mapping → name resolution → test corpus → validation → integration
→ Output: app/parsers/go.py + test harness + language patterns documented
```

### Using `/call-graph-debug`

```
User: Why did OrderService.submit() resolve to payment.Process() with only 0.65 confidence?
→ /call-graph-debug
→ Skill traces: exact match? (no) → prefix overlap? (weak) → suffix match? (medium) → external? (no) → etc.
→ Output: Confidence breakdown, validation against source, suggestion to refine heuristic or accept score
```

### Using `/pipeline-analyze`

```
User: The pipeline is slow; where should I optimize?
→ /pipeline-analyze
→ Skill profiles phases, shows LLM costs, identifies bottleneck (e.g., Tier 2 flow analysis is 40% of time)
→ Output: Performance report, cost breakdown, optimization suggestions (e.g., reduce max_concurrent, switch to Haiku for some tasks)
```

---

**Status**: ✅ Approved, ready for implementation
