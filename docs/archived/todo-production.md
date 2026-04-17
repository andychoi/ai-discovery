# Production-Grade TODO: Reverse Engineering Quality

This document tracks what's needed to move from the current working prototype to a production-quality enterprise reverse-engineering engine. Items are ordered by expected impact.

---

## Database / Schema Modeling (read this first)

### Should database schema/model be in source code?

**Recommendation: yes, define it in `app/db.py` via a migrations list — but do NOT embed external target schemas as fixtures.**

The current `db.py` creates tables inline in `init_db()`. For production:

| Approach | Tradeoff |
|----------|----------|
| **Inline DDL in `init_db()` (current)** | Simple but hard to evolve safely in production |
| **Versioned migrations list in `db.py`** | Best for this app: explicit, auditable, no extra dependency |
| **Alembic / SQLAlchemy ORM** | Full power but overkill for a single-file SQLite app |
| **Separate schema.sql + migration scripts** | Readable but harder to test |

**Concrete action:** Replace the current monolithic `CREATE TABLE IF NOT EXISTS` block with a `MIGRATIONS: list[str]` in `db.py`. Each entry is a SQL statement keyed to a version. `init_db()` applies un-applied migrations and bumps `schema_version`. This is already partially supported (`schema_version` table exists) — wire it up.

For **target application schemas** (the repos being reverse-engineered), the tool should *infer* them from AST, not require them as input. If users want to provide schema hints (e.g. a `schema.sql` or OpenAPI spec), that belongs in Phase 4 of the implementation plan (contract ingestion), not in the tool's own DB definition.

---

## Priority 1 — Graph & Resolver Quality

### 1.1 Import-scoped symbol resolution

**Problem:** Call resolution currently matches by name alone. `save()` in `UserService` resolves to every `save()` in the codebase.

**Fix:** Parsers should emit `imports[]` on `CodeNode`. Resolver should use import origin to scope candidates before falling back to short-name index.

```python
# CodeNode gains:
imports: list[str] = field(default_factory=list)  # e.g. ["com.corp.repo.UserRepository"]

# Resolver: filter candidates by imported modules first
```

**Files:** `app/parsers/*.py`, `app/graph/models.py`, `app/graph/call_graph.py`

### 1.2 Receiver/type-aware resolution

**Problem:** `this.repo.save(user)` — the receiver is `repo`, type is `UserRepository`. Currently ignored.

**Fix:** Parsers emit `receiver` on call references. Resolver uses receiver name to narrow candidates.

**Files:** `app/parsers/*.py`, `app/graph/call_graph.py`

### 1.3 DI injection mapping (Java/C# / Spring / ASP.NET)

**Problem:** Constructor-injected `IUserRepository userRepo` → call `userRepo.find(id)` can't be resolved without knowing the binding.

**Fix:** Add DI scan pass: detect constructor injection patterns, build interface→implementation map, apply during call resolution.

**Files:** new `app/graph/di_resolver.py`

### 1.4 Interface/implementation binding

**Problem:** Calls to interface types (`IOrderService.submit`) don't resolve to concrete implementations.

**Fix:** Build interface→impl map from class declarations. Substitute during resolution.

**Files:** `app/graph/call_graph.py`, `app/parsers/*.py`

---

## Priority 2 — Scenario & Flow Reconstruction

### 2.1 Replace score-sorted primary_path with execution-ordered path

**Problem:** `primary_path` is currently sorted by confidence score, not execution order. The resulting BPMN can be logically correct but temporally wrong.

**Fix:** Track BFS visit order alongside score. Sort by (BFS_order, -score) so execution sequence is preserved while high-confidence nodes within a step are preferred.

**Files:** `app/graph/call_graph.py` → `ExecutionSliceBuilder.build_scenario()`

### 2.2 Async edge detection

**Problem:** Fire-and-forget calls (message queue publish, `asyncio.create_task`, `CompletableFuture.runAsync`) are treated as synchronous calls. This mis-orders flows.

**Fix:** Detect async patterns in parsers (framework_hints `async_boundary`). Add `ASYNC` edge type. BPMN generator emits intermediate throw events.

**Files:** `app/parsers/*.py`, `app/graph/models.py`, `app/output/bpmn_generator.py`

### 2.3 Branch / conditional edges

**Problem:** `if/else`, `switch`, `try/catch` branching is not captured. BPMN diagrams lack gateways.

**Fix:** Parsers detect conditional blocks and emit `condition` hints. `ExecutionSliceBuilder` creates `CONDITIONAL` edges with condition text. BPMN generator emits `exclusiveGateway`.

**Files:** `app/parsers/*.py`, `app/graph/call_graph.py`, `app/output/bpmn_generator.py`

### 2.4 Domain classification before scenario building

**Problem:** `ExecutionSliceBuilder` runs before `classify_domains` updates `CodeNode.domain`, so execution nodes may have `domain=None`.

**Fix:** Pipeline order is already correct (classify first, build slices second). Verify `node.domain` is populated before slice build and assert no null domains in `_create_execution_node`.

**Files:** `app/pipeline.py`, `app/graph/call_graph.py`

---

## Priority 3 — External Dependency Modeling

### 3.1 First-class external node types

**Problem:** Unresolved calls (HTTP clients, queue producers) are stored as `UNRESOLVED` edges with a raw text callee. They don't become graph citizens.

**Fix:** Add node types to `models.py`:
```python
# New graph node types
ExternalAPINode   # confirmed HTTP integration
QueueTopicNode    # Kafka/SQS topic
DatabaseNode      # persistence target
SharedLibraryNode # cross-repo library
```
Detect patterns in parsers (`requests.get`, `boto3.client`, `KafkaProducer`) and emit these nodes instead of raw unresolved edges.

**Files:** `app/graph/models.py`, `app/parsers/*.py`, `app/graph/call_graph.py`

### 3.2 Workspace / multi-repo scan

**Problem:** Single repo at a time. Cross-service calls are always `UNRESOLVED`.

**Fix:** Add workspace config:
```yaml
workspace:
  repos:
    - path: ./services/orders
      service: orders-service
      runtime: spring-boot
    - path: ./services/payments
      service: payments-service
      runtime: dotnet
```
Pipeline resolves all repos, builds a unified qualified_name namespace, then resolves cross-repo calls. Service name becomes part of qualified_name prefix.

**Files:** `app/config.py`, `app/pipeline.py`, new `app/repo/workspace.py`

### 3.3 Manifest-driven service relationships

**Problem:** `pom.xml`, `package.json`, `.csproj` contain dependency declarations that reveal service-to-service relationships even before call graph analysis.

**Fix:** Parse manifests in `lang_detector` or a new `manifest_reader`. Emit `SharedLibraryEdge` and `ServiceDependencyEdge` before parsing source code.

**Files:** `app/repo/lang_detector.py`, new `app/repo/manifest_reader.py`

---

## Priority 4 — Contract & Infrastructure Ingestion

### 4.1 OpenAPI / Swagger ingestion

Parse `openapi.yaml` / `swagger.json` and emit `ExternalAPINode` objects with endpoint paths, methods, and request/response schemas. Correlate with parsed endpoints in code.

**Files:** new `app/parsers/openapi_parser.py`

### 4.2 gRPC / Protobuf ingestion

Parse `.proto` files → emit service, RPC, message types as CodeNodes. Resolve gRPC client calls to these nodes.

**Files:** new `app/parsers/proto_parser.py`

### 4.3 GraphQL schema ingestion

Parse `.graphql` / `schema.graphql` → emit type and resolver nodes.

**Files:** new `app/parsers/graphql_parser.py`

### 4.4 Docker Compose / Kubernetes / Helm ingestion

Parse service definitions → emit `ServiceNode` with port mappings, env vars, dependency links. Correlates with code-level service names.

**Files:** new `app/parsers/infra_parser.py`

### 4.5 Database DDL / migration ingestion

If SQL migration files or ORM model files exist in the repo, parse them to produce a verified schema model rather than inferring from AST annotations alone. This is the preferred approach: **ingest DDL from the target repo** rather than hard-coding schema knowledge in the tool.

Supported sources:
- Flyway/Liquibase migration SQL files
- Django/Alembic/EF Core migration files
- SQLAlchemy declarative models
- JPA / Hibernate entity classes (already partially parsed as `db_model`)
- Prisma schema files

**Files:** new `app/parsers/migration_parser.py`

---

## Priority 5 — Output Quality

### 5.1 Integration-centric document types

Current output is domain-centric (one doc per business domain). Add:

| Doc Type | Content |
|----------|---------|
| `interface-catalog` | All external interfaces: API endpoints, queue topics, DB schemas |
| `dependency-matrix` | Service-to-service dependency table with edge confidence |
| `event-catalog` | All produce/consume pairs across services |
| `service-context-map` | DDD context map: bounded contexts + relationships |
| `sequence-diagram` | Cross-domain interaction diagrams |

**Files:** `app/ai/rollup.py`, `app/output/doc_generator.py`, new templates

### 5.2 Sequence diagram quality

Current Mermaid output is structurally correct but all flow descriptions are generic. Improve by:
- Using actual method names and parameter types from summaries
- Including data objects passed between participants
- Adding `loop`/`alt`/`opt` blocks where conditional edges exist

**Files:** `app/output/bpmn_generator.py`

### 5.3 Confidence and traceability

Every business claim in generated docs should link to the exact source functions/files that support it. This is partially done via self-review annotations. Make it first-class:
- Add `<!-- source: orders/OrderService.java:42 -->` annotations inline
- Expose a `discover trace` command: given a doc sentence, show which code lines it came from

---

## Priority 6 — Operational Quality

### 6.1 Structured logging

Replace `logger.warning(f"...")` with structured JSON log lines including scan_id, phase, qualified_name, elapsed_ms. Makes pipeline debugging tractable for large repos.

### 6.2 Incremental re-scan

**Problem:** Any code change triggers re-scan of all files in that commit.

**Fix:** Track which files changed between commits (`git diff --name-only`). Only re-parse changed files; reload unaffected nodes from DB. Re-run call graph and downstream phases.

**Files:** `app/pipeline.py`, `app/repo/resolver.py`

### 6.3 Parallel parsing

Parsing is currently sequential per file. Parallelize with `ThreadPoolExecutor` (tree-sitter is thread-safe). Expected 3–5× speedup on large repos.

**Files:** `app/pipeline.py` (parse phase)

### 6.4 Embedding model consistency check

**Problem:** Switching Bedrock → Ollama changes embedding dimensionality. Current check re-embeds if dim mismatches but doesn't warn until after the pipeline starts.

**Fix:** Check embedding dim on startup and fail fast with a clear error message before any LLM calls.

**Files:** `app/rag/embedder.py`

### 6.5 Test stability for parser/graph core

Tree-sitter parser tests currently fail to collect because `tree_sitter_python` etc. are not installed in the dev environment. Fix:

1. Add `pip install tree-sitter-python tree-sitter-java tree-sitter-c-sharp tree-sitter-javascript` to `requirements.txt` (or a `requirements-dev.txt`)
2. Add `pytest.mark.skipif` guards using `importorskip` so CI doesn't block on missing packages
3. Add enterprise fixture set (monorepo service-to-service, shared library, queue producer/consumer) for regression testing

**Files:** `app/requirements.txt`, `app/tests/test_python_parser.py`, etc.

### 6.6 DB schema versioned migrations

Replace the monolithic `CREATE TABLE IF NOT EXISTS` block with an explicit migrations list:

```python
MIGRATIONS: list[tuple[int, str]] = [
    (1, "CREATE TABLE scan_runs ..."),
    (2, "CREATE TABLE code_nodes ..."),
    (3, "ALTER TABLE code_nodes ADD COLUMN imports TEXT"),  # future
]
```

`init_db()` reads current `schema_version` and applies only un-applied migrations. This makes schema evolution safe for users with existing DBs.

**Files:** `app/db.py`

---

## Priority 7 — Evaluation Harness

Without measurement, it's impossible to know if graph or flow quality improves. Build:

| Metric | Measurement |
|--------|-------------|
| Symbol resolution precision | Known call targets in fixture repos; measure % correctly resolved |
| External dependency recall | Known external services in fixture; measure % detected |
| Scenario ordering accuracy | Hand-labelled expected flow sequences vs. generated primary_path |
| Domain classification accuracy | Known bounded contexts vs. inferred domains |
| Integration artifact completeness | Known interfaces vs. generated interface catalog |

**Files:** new `app/eval/`, new `app/tests/fixtures/enterprise/`

---

## Summary: Recommended Execution Order

1. **DB versioned migrations** — safe, no risk, unblocks future schema changes
2. **Import-scoped resolver** — single highest-impact graph quality fix
3. **Execution-ordered primary_path** — fixes scenario flow correctness
4. **External node types (UNRESOLVED → ExternalAPINode)** — stops misclassification
5. **Async + conditional edge detection** — enables proper BPMN gateways
6. **Parallel parsing** — operational win on large repos
7. **DDL/migration ingestion** — verified schema vs. inferred
8. **Workspace / multi-repo config** — unlocks cross-service analysis
9. **Integration-centric doc types** — delivers enterprise value
10. **Evaluation harness** — gates all further quality improvements
