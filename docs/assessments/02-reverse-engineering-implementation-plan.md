# Reverse Engineering Implementation Plan

Date: 2026-04-16

## Objective

Improve the discovery application so it can produce more accurate reverse-engineering results for enterprise web applications with inter-dependencies across repositories, services, shared components, and runtime integrations.

## Guiding Principles

- Improve graph quality before increasing LLM synthesis volume
- Make external systems first-class objects, not unresolved text
- Preserve traceability from every inferred dependency or flow step back to source evidence
- Add integration-focused outputs, not only domain summaries

## Phase 0: Stabilize the Baseline

### Goals

- Make parser and graph changes safe to ship
- Remove obvious drift between tests and current implementation

### Work

- Fix stale tests and imports
- Ensure parser dependencies are installed in the dev/test environment
- Add baseline fixtures for:
  - monorepo service-to-service calls
  - API client wrappers
  - queue producers and consumers
  - shared library usage

### Deliverables

- Green parser/graph test suite
- Enterprise fixture set for future regression testing

## Phase 1: Dependency Model Expansion

### Goals

- Model enterprise dependencies explicitly

### Work

- Extend graph models with first-class nodes and edges for:
  - `Repo`
  - `Service`
  - `ExternalAPI`
  - `QueueTopic`
  - `Database`
  - `SharedLibrary`
  - `ApiSpec`
- Add edge types for:
  - `HTTP_CALL`
  - `EVENT_PUBLISH`
  - `EVENT_CONSUME`
  - `DB_ACCESS`
  - `IMPORT_DEP`
  - `RUNTIME_DEP`
  - `SHARED_LIB_DEP`
- Separate `UNRESOLVED` from confirmed `EXTERNAL_API`

### Deliverables

- New dependency graph schema
- Persistence changes in SQLite
- Backward-compatible migration path

## Phase 2: Multi-Repo and Workspace Discovery

### Goals

- Analyze interdependent applications rather than one repo in isolation

### Work

- Add a workspace config that accepts multiple repos or local paths
- Add repo relationship metadata:
  - ownership
  - service name
  - runtime type
  - deployment unit
- Scan manifests and workspace descriptors:
  - `package.json`
  - `pom.xml`
  - `.csproj`
  - `pyproject.toml`
  - solution/workspace files

### Deliverables

- Workspace scan mode
- Repo inventory and repo-to-repo dependency output

## Phase 3: Better Symbol Resolution

### Goals

- Reduce false-positive and false-negative call edges

### Work

- Capture richer parser metadata:
  - import/module references
  - receiver object names
  - namespace/package context
  - DI constructor/property injection
  - interface and implementation bindings
- Add resolver stages:
  - exact symbol match
  - import-scoped match
  - receiver/type-aware match
  - DI-assisted match
  - interface implementation match

### Deliverables

- Confidence-aware resolver with evidence trail
- Lower-noise cross-domain and cross-repo graphs

## Phase 4: Runtime Boundary and Contract Ingestion

### Goals

- Reconstruct dependencies that source code alone does not reveal cleanly

### Work

- Add ingestion for:
  - OpenAPI and Swagger
  - GraphQL schema and client operations
  - gRPC proto files
  - Docker Compose
  - Helm and Kubernetes manifests
  - Terraform where available
  - gateway and ingress configuration
- Correlate runtime endpoints and infrastructure objects back to code nodes

### Deliverables

- Contract graph
- Runtime topology supplements
- Better external interface detection

## Phase 5: Ordered Scenario Graphs

### Goals

- Replace heuristic BFS slices with more faithful execution reconstruction

### Work

- Reorder pipeline so domains are classified before scenario construction
- Build scenario graphs from entrypoint to sink with:
  - ordered steps
  - branch edges
  - async handoff edges
  - DB and integration boundaries
  - stop conditions
- Preserve evidence for each step:
  - source node
  - edge type
  - confidence
  - boundary reason

### Deliverables

- Ordered scenario graph builder
- More reliable process-flow docs and diagrams

## Phase 6: Integration-Centric Outputs

### Goals

- Produce artifacts that enterprise reverse-engineering users actually need

### Work

- Add generated outputs for:
  - interface catalog
  - dependency matrix
  - event/topic catalog
  - cross-domain sequence diagrams
  - service context map
  - system landscape summary
- Link these outputs to existing `as-is` and `process-flow` documents

### Deliverables

- New output templates
- Cross-domain and cross-service documentation set

## Phase 7: Quality Gates and Evaluation

### Goals

- Measure whether improvements actually help

### Work

- Create evaluation fixtures with known service dependencies and flows
- Measure:
  - symbol resolution precision
  - external dependency recall
  - scenario ordering accuracy
  - domain classification accuracy
  - integration artifact completeness

### Deliverables

- Repeatable evaluation harness
- Quality thresholds for future releases

## Recommended Initial Execution Order

1. Stabilize tests and parser environment.
2. Fix pipeline ordering so scenarios are built after domain classification.
3. Introduce explicit external dependency nodes and `UNRESOLVED` node type.
4. Improve symbol resolution using import and receiver context.
5. Add workspace scan support and manifest-driven repo relationships.
6. Add integration-focused output documents.

## Immediate Next Sprint

### Sprint Goal

Improve correctness of dependency reconstruction before adding more generated documents.

### Sprint Tasks

- Fix test suite drift and parser dependency setup
- Refactor pipeline ordering for domain-aware scenarios
- Update graph models for external systems and unresolved nodes
- Upgrade call resolution to use parser context beyond short names
- Add initial manifest ingestion for workspace/package relationships

### Exit Criteria

- Test suite runs cleanly in the intended dev environment
- Scenario flows retain correct domain attribution
- Cross-domain dependency graph includes explicit external system nodes
- False-positive short-name edges are materially reduced on enterprise fixtures
