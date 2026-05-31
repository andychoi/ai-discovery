# Screen-Centric Spec Generation for AI-Discovery

**Status**: Design approved, foundation implemented
**Date**: 2026-05-31
**Author**: Claude (AI-Discovery Development)
**Related**: CLAUDE.md § Screen-Centric Documentation

---

## Executive Summary

ai-discovery gains a new **user-centric documentation mode**: instead of organizing docs by business domain (current), organize by user-facing screens. Screens are the entry point; supporting docs (backend logic, databases, batch jobs, interfaces) link outward.

**Why**: Users navigate by menu, not by domain. Screens directly answer: "What can I do?" and "What happens when I do it?"

**How**: Auto-detect menu system → map to backend → generate linked specs → detect drift when source changes.

**Impact**: Backward-compatible addition. Domain docs remain; screens are new. No cost multiplier—screens link to existing docs, not duplicating them.

---

## Problem Statement

### Current Situation
ai-discovery generates **domain-centric docs**:
- ASIS: "What does the customer domain do?"
- ASD: "What are the detailed business rules?"
- ASSC: "What database tables?"
- PF/BPMN: "What are the processes?"

This works for architects and domain experts who think in terms of "domains" and "entities."

### User Perspective
End users (product managers, QA, new engineers) think differently:
- "What screens can I access?"
- "What happens when I click Submit?"
- "Where does that data come from?"
- "What other systems does this screen talk to?"

**Gap**: Users must cross-reference domain docs to understand a single screen. No single document answers "what does this screen do end-to-end?"

### Example
A support agent wants to understand the "Customer Search" screen:
1. Find which domain it belongs to (manual search) → "customer domain"
2. Read the ASIS for customer domain (general overview)
3. Read the ASD for customer domain (rules, APIs)
4. Trace API calls to backend (grep source code)
5. Find related batch jobs (manual search)
6. Find database tables (manual search)

**Solution**: One screen spec covers all of this in one place.

---

## Design Goals

1. **User-centric navigation** — Screens as the primary entry point, not domains
2. **End-to-end coverage** — One spec links to everything a screen touches
3. **Backward-compatible** — Keep existing domain docs; screens are additive
4. **Auto-detected menus** — No manual screen definitions; detect from code
5. **Drift detection** — Flag stale specs for cost-effective regeneration
6. **Multi-architecture** — Java, .NET, Node.js, any web framework

---

## Architecture

### Core Concept: Screen as Entry Point

```
┌─────────────────────────────────────┐
│        SCREEN SPEC                  │
│  (User-facing single doc)           │
│  • Purpose, When Used, User Actions │
│  • Data Sources, Rules, Roles       │
│  • Downstream Effects, Open Items   │
└──────────────────────────────────────┘
       ↓ (links to supporting docs)
  ┌────────────┬──────────────┬──────────────┬─────────────────┐
  │ Backend    │ Database     │ Batch Jobs   │ External APIs   │
  │ Specs      │ Schemas      │ Specs        │ Interfaces      │
  │ (existing) │ (existing)   │ (existing)   │ (existing)      │
  └────────────┴──────────────┴──────────────┴─────────────────┘
```

### Three Phases

#### Phase 0: Menu Detection (New)
**Input**: Repository source code
**Output**: `screen_map.yaml`, `menu_tree.json`

Auto-detect menu structure using hybrid strategy:
1. Look for JSON/YAML menu files (menu.json, navigation.yaml)
2. Look for TypeScript constants (export const MENU = [...])
3. Look for framework routing configs (Vue Router, React Router)
4. Return first found format + menu hierarchy

**Key insight**: Every web app has a menu. Users navigate it. That menu == the screens they can access.

**Extractors**:
- `JsonYamlDetector`: Parse menu.json, navigation.yaml, etc.
- `TypeScriptConstantDetector`: Extract menu constants from .ts/.js
- `FrameworkRoutingDetector`: Parse routing configs (Vue, React, Angular)
- `HybridMenuDetector`: Try all, return first success

**Output**: List of `MenuItem` objects (id, label, path, roles, children...)

#### Phase 1: Screen-to-Backend Mapping (New)
**Input**: `screen_map.yaml` + repo source
**Output**: Enhanced `screen_map.yaml` with backend links

For each detected screen:
1. Find FE component file (Vue, React, Angular)
2. Extract API calls from FE code (fetch, axios, $http patterns)
3. Resolve API paths to backend controllers (Spring @RequestMapping, ASP.NET routes)
4. Trace controller → service → repository (for Java/C#)
5. Extract database tables (from MyBatis, JPA, EF)
6. Find related batch jobs (Spring Batch Job classes, .NET scheduled tasks)
7. Identify external interfaces (RestTemplate, WebClient, partner APIs)
8. **Detect data injection points** (orphaned tables, stored procedures, views)
9. **Link ETL batch jobs** that populate external data sources
10. **Compute SHA256 hashes of all touched files** (for drift detection)

**Data Injection Point Detection** (NEW):
Identifies external data sources and injection mechanisms:
- **Orphaned tables** (read but never written in code) → direct database injection by EAI/ETL systems
- **Stored procedures** (CALL, EXECUTE, @Procedure) → database-level integrations
- **Database views** (V_*, VIEW* patterns) → data aggregation from external sources
- **External imports** (FTP, SFTP, HTTP downloads) → file-based data integration

**ETL Batch Job Pattern Detection** (NEW):
Links ETL/EAI batch jobs to the external tables they populate:
- Detects batch jobs that access orphaned tables
- Identifies trigger types: internal (@Scheduled), external REST (@PostMapping), external queue (@KafkaListener)
- Tracks external data sources (FTP, SFTP, HTTP) accessed by each ETL job
- Provides complete data lineage: External System → ETL Job → Orphaned Table → Screen
- Confidence scoring (0.7-0.9) based on external source detection

**Current state**: 
- ✅ Data injection point detection fully implemented (orphaned tables, stored procedures, views, external imports)
- ✅ ETL batch job pattern detection fully implemented (trigger types, external source tracking)
- ⏳ Real backend resolution: Parse Java annotations (@Service, @Repository, @Autowired), .NET attributes, traverse call chains

#### Phase 2: LLM Spec Generation (New)
**Input**: Enhanced screen_map.yaml
**Output**: `docs/screens/{screen_id}.md` with frontmatter

For each screen, invoke Tier 2 LLM (Sonnet) to generate 9 sections:
1. **Purpose** — What the screen does (2-3 sentences, inferred from menu path + CRUD profile)
2. **When Used** — Persona, workflow context, trigger conditions
3. **User Actions & System Responses** — Table of interactions (extracted from API calls)
4. **Important Rules** — Business rules, validations, guards (from service layer)
5. **Data & Fields** — Fields shown, sources (database tables with links)
6. **Downstream Effects** — What happens: screens, batches, external feeds
7. **Permissions & Roles** — Who can access (from RBAC + controller annotations)
8. **Open Items** — TODOs, missing implementations, broken links (from code scan)
9. **Technical Reference** — Collapsed: FE paths, API routes, BE chain, DB tables

**Frontmatter** includes:
- `doc_id` — unique identifier
- `menu_path` — breadcrumb [root, ..., leaf]
- `crud_profile` — Read-only, Create/Edit, Manage, etc.
- `source_hashes` — file_path → SHA256 (for drift detection)
- `related_docs` — links to supporting docs

**Template**: Single `screen-spec.md.j2` (Jinja2)

#### Phase 3: Drift Detection (New)
**Input**: Screen specs (with source_hashes frontmatter) + repo source
**Output**: Drift report

CLI command: `discover verify-drift <repo> --spec-dir ./docs/screens`

For each spec:
1. Extract `source_hashes` from frontmatter
2. Recompute current hashes of those files
3. Compare; if mismatch → screen is "drifted"
4. Report which files changed, which specs affected
5. Exit non-zero for CI (e.g., block merge if specs are stale)

**Cost implication**: Only regenerate drifted specs, not all (60–80% savings on subsequent runs).

---

## Implementation Status

### ✅ Completed
- **menu_detector.py** (320 lines): Full hybrid detection + MenuItem building
- **screen_mapper.py** (390 lines): Skeleton with file-path heuristics + hash computation
- **drift_checker.py** (180 lines): Full drift detection + reporting
- **screen-spec.md.j2**: Complete Jinja2 template
- **CLI commands**: `detect-screens`, `verify-drift`
- **Database schema v7**: Tables for screens, screen_mappings, screen_source_hashes
- **Tests**: 9 unit tests, all passing
- **Documentation**: CLAUDE.md updated with screen-centric mode overview

### ⏳ To Do (Lower Priority)

**Phase 1 Enhancement (real backend resolution)**:
- Parse Spring @RequestMapping, @Service, @Repository
- Parse .NET [HttpGet], [HttpPost], [Service], [Inject]
- Traverse call chains (not just controller → method)
- Extract MyBatis SQL, JPA queries
- Resolve batch job dependencies

**Phase 2 Enhancement (LLM integration)**:
- Wire into pipeline (add phases 0–2)
- Invoke Sonnet for prose generation
- Handle cross-doc linking (markdown [text](#/docs/...))
- Preserve MANUAL blocks in existing specs

**Phase 3 Enhancement**:
- CI/CD integration example (GitHub Actions, GitLab CI)
- Dashboard showing drift status
- Auto-trigger regeneration on drift

---

## Design Decisions & Tradeoffs

### Decision 1: Additive, Not Replacement
**Choice**: Screen specs are NEW entry points. Domain docs remain.
**Why**: 
- Domain docs have value (entity lifecycles, process mining, BPMN diagrams)
- Screens don't replicate that; they're orthogonal views
- Users can choose: navigate by domain OR by screen
**Tradeoff**: Slight duplication of information (e.g., a table might be documented in both ASSC and a screen spec), but information lives in ONE canonical place and is linked from others.

### Decision 2: Hybrid Menu Detection
**Choice**: Try JSON → TypeScript → framework routing; return first found.
**Why**: Web apps use different patterns. One detection strategy won't cover all.
**Tradeoff**: Detection isn't perfect (some apps mix patterns). Falls back to manual menu.yaml if heuristics fail.

### Decision 3: Source Hashes in Frontmatter
**Choice**: Store SHA256 hashes of touched files in spec frontmatter.
**Why**: 
- Drift detection is deterministic
- No external state (DB, config) needed for `verify-drift`
- Cost-saving: only regenerate drifted specs
- CI-friendly: can block merge if specs stale
**Tradeoff**: Hashes can become stale if spec file itself is edited. Regeneration recomputes, so it's self-healing.

### Decision 4: No Screen Definition Language
**Choice**: Extract screens from menu code, not from hand-written .yaml.
**Why**: 
- Menus are the source of truth (users see them)
- Avoids duplication (maintaining menu + screen definition = nightmare)
- Auto-detection is low-friction
**Tradeoff**: Detection heuristics can miss edge cases. Fallback: manual menu.yaml for apps where auto-detection fails.

### Decision 5: Skeleton Backend Mapping (for now)
**Choice**: Implement file-path heuristics; real backend resolution is future.
**Why**: 
- Scope creep risk. Real resolution requires deep language/framework knowledge
- Skeleton is enough to prove the concept
- Real resolution can be added incrementally
**Tradeoff**: Backend links in generated specs will be incomplete until Phase 1 is enhanced. Acceptable for MVP.

---

## Database Schema

### screens
Stores detected screens.
```sql
CREATE TABLE screens (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER REFERENCES scan_runs(id),
    screen_id TEXT,                  -- kebab-case
    menu_path_json TEXT,             -- ["root", "...", "leaf"]
    label TEXT,                      -- Display name
    path TEXT,                       -- Route: /customers/search
    fe_component TEXT,               -- src/pages/Customer/SearchPage.vue
    crud_profile TEXT,               -- Read-only, Create/Edit, Manage
    interaction_mode TEXT,           -- inquiry, monitoring, workflow_step
    permissions_json TEXT,           -- ["ROLE_USER", "ROLE_ADMIN"]
    related_screens_json TEXT,       -- [screen_id, ...]
    metadata_json TEXT,              -- Custom fields
    created_at TEXT
);
```

### screen_mappings
Links screen to backend components.
```sql
CREATE TABLE screen_mappings (
    id INTEGER PRIMARY KEY,
    screen_id INTEGER REFERENCES screens(id),
    fe_api_calls_json TEXT,          -- [{"method": "GET", "path": "/api/..."}]
    be_controllers_json TEXT,        -- [{"class": "...", "file": "..."}]
    be_services_json TEXT,           -- [{"class": "...", "file": "..."}]
    db_tables_json TEXT,             -- ["CUSTOMER", "ADDRESS"]
    batch_jobs_json TEXT,            -- ["ExportJob", "SyncJob"]
    external_interfaces_json TEXT,   -- ["partner-api", "message-queue"]
    source_files_json TEXT,          -- ["src/pages/...", "src/main/..."]
    created_at TEXT
);
```

### screen_source_hashes
Per-file hashes for drift detection.
```sql
CREATE TABLE screen_source_hashes (
    id INTEGER PRIMARY KEY,
    screen_id INTEGER REFERENCES screens(id),
    file_path TEXT,                  -- src/pages/Customer/SearchPage.vue
    sha256_hash TEXT,                -- abc123def456...
    created_at TEXT,
    UNIQUE(screen_id, file_path)
);
```

---

## CLI Interface

### detect-screens
```bash
discover detect-screens /path/to/repo --output ./data
```
Auto-detect menu and extract screens.
**Output**: 
- `data/screen_map.yaml` (all screens)
- `data/menu_tree.json` (menu hierarchy)

### verify-drift
```bash
discover verify-drift /path/to/repo --spec-dir ./docs/screens
```
Check if screen specs are out of sync with source.
**Output**: Drift report (which files changed, which specs affected)
**Exit code**: 0 if all in sync, 1 if drift detected (for CI)

---

## Integration with Existing Pipeline

Screen generation doesn't interfere with domain generation:

```
Phases 0–2 (NEW): Menu detection, backend mapping, screen specs
           ↓
Phases 5–19 (EXISTING): Domain parsing, Tier 1/2/3 LLM, domain docs
```

Both pipelines can run independently:
- Run `discover scan` alone → get domain docs (current behavior)
- Run `discover detect-screens` alone → get screen_map.yaml
- Run both → get screen specs + domain docs (complementary views)

**Cost model**: 
- Phase 0: ~1 second (static analysis)
- Phase 1: ~5 seconds per 100 screens (parsing + hashing)
- Phase 2: ~30 seconds per screen (1 Sonnet call per screen)

---

## Success Criteria

✅ **Foundation** (Completed):
- [x] Menu detection works on JSON/YAML/TypeScript/framework routing
- [x] Screen map building from menu items
- [x] Source file hash computation
- [x] Drift detection with clear reporting
- [x] CLI commands working
- [x] Database schema designed
- [x] Comprehensive tests passing

⏳ **Full Integration** (To do):
- [ ] Backend mapping enhanced (real controller/service resolution)
- [ ] LLM spec generation integrated into pipeline
- [ ] Cross-doc linking working
- [ ] Tested on 3+ real codebases (Java Spring, .NET, Node.js)
- [ ] Drift detection in CI/CD example

📊 **Metrics** (To measure):
- Menu detection accuracy: ≥ 90% on real apps
- Backend mapping coverage: ≥ 80% API links resolved
- Spec generation cost: ≤ $0.05/screen (Tier 2 Sonnet)
- Drift detection accuracy: 100% (deterministic hashing)

---

## Open Questions & Future Work

### Q1: Should screens link *into* domain docs or *out to* them?
Current design: Screens are entry points, they link OUT to supporting docs.
Alternative: Domain docs are primary, screens are just navigation aids.
**Decision**: Entry points are more intuitive for end users.

### Q2: How to handle screen renames/restructures?
Current: `screen_id` is stable (kebab-case from menu path). If menu changes, screen_id might change, breaking links.
Future: Version screen_ids with a stable hash, or maintain a screen alias table.

### Q3: Mobile screens?
Current: Designed for web. Mobile apps have different navigation patterns.
Future: Extend menu detector to support mobile routing (React Native, Flutter, etc.).

### Q4: Cross-codebase screens (microservices)?
Current: One screen → one codebase. Real systems have screens that call multiple services.
Future: Extend screen mapper to handle cross-service dependencies.

---

## References

- **CLAUDE.md** § Screen-Centric Spec Generation
- **Modules**: menu_detector.py, screen_mapper.py, drift_checker.py
- **Tests**: tests/test_screen_detection.py
- **Template**: src/ai_discovery/generators/templates/screen-spec.md.j2
- **Related**: ai-spec CLAUDE.md (GWMS example of screen-centric docs)

---

## Appendix: Example Screen Spec Structure

```markdown
---
doc_id: myapp-screen-customer-search
title: Customer Search
menu_path: [Customers, Search, Customer Search]
crud_profile: Read-only View
interaction_mode: inquiry
permissions: [ROLE_USER, ROLE_ADMIN]
source_hashes:
  src/pages/Customer/SearchPage.vue: abc123...
  src/main/java/.../CustomerController.java: def456...
  migration_001_customer.sql: ghi789...
---

# Customer Search

**Menu Path:** Customers > Search > Customer Search

## Purpose
Allows users to search for customers by name, email, or phone number.

## When This Screen Is Used
Product support agents use this to look up customer accounts when responding to inquiries.

## User Actions and System Responses
| Action | Response |
|--------|----------|
| Enter search term, click Search | Load matching customers (max 50) |
| Click customer row | Navigate to Customer Detail screen |

[...]

## Technical Reference
### APIs
- GET `/api/customers/search?q=...`
- GET `/api/customers/{id}`

### Database Tables
- [CUSTOMER](#/docs/database/CUSTOMER)
- [CUSTOMER_ADDRESS](#/docs/database/CUSTOMER_ADDRESS)

### Backend
- [CustomerController](#/docs/backend-specs/CustomerController)
- [CustomerService](#/docs/backend-specs/CustomerService)
```

---

**Status**: Ready for implementation feedback & code review.
