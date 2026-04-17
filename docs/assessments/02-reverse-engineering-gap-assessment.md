# Reverse Engineering Gap Assessment

Date: 2026-04-16

## Scope

Assessment of the current discovery application with focus on reverse engineering of enterprise web applications that have inter-dependencies across services, shared libraries, queues, databases, and UI/backend boundaries.

## Summary

The application has a solid base for single-repository structural analysis:

- AST parsing for Java, C#, Python, and JavaScript/TypeScript
- Static call graph construction
- Domain grouping
- LLM summarization, flow analysis, and document rollup
- Early scenario-flow and BPMN generation scaffolding

The main limitation is that the current system still behaves like a single-repo code summarizer with partial flow inference, not a dependency-aware enterprise reverse-engineering engine. The biggest gaps are around cross-application dependency modeling, symbol resolution quality, scenario ordering, and framework/config ingestion.

## Findings

### 1. Cross-application dependencies are mostly not modeled

The pipeline resolves a single repository, walks source code files, and only computes cross-domain adjacency for edges that resolve to local `code_nodes`. Unresolved calls are stored as text, but they do not become first-class external systems or cross-app interfaces.

Impact:

- Shared services, external APIs, queues, topics, webhooks, and external databases are underrepresented
- Cross-application architecture maps will be incomplete
- Generated docs will skew toward internal code structure instead of enterprise system topology

Key references:

- `app/repo/resolver.py`
- `app/repo/file_walker.py`
- `app/graph/call_graph.py`
- `app/pipeline.py`

### 2. Call graph resolution is too ambiguous for large enterprise repos

Parsers mostly emit short call names such as `save`, `handle`, `execute`, and `get`. The call graph then resolves by short-name matching and fans out to every candidate symbol when names collide.

Impact:

- False edges increase rapidly in larger codebases
- Cross-domain dependencies become noisy
- Flow inference and rollups are built on low-quality graph data

This is especially risky in Java/C# service layers and Python/JavaScript handler-heavy codebases where common method names are repeated across domains.

Key references:

- `app/parsers/javascript.py`
- `app/parsers/python_parser.py`
- `app/parsers/java.py`
- `app/parsers/csharp.py`
- `app/graph/call_graph.py`

### 3. Scenario flow reconstruction is heuristic, not execution-ordered

`ExecutionSliceBuilder` builds bounded BFS slices and later ranks nodes by confidence. The resulting `primary_path` is score-based, not execution-ordered. Unknown nodes are also defaulted to `EXTERNAL_API`.

Impact:

- Generated process flows can look plausible while being out of sequence
- Helper methods and unresolved internal symbols can be misclassified as integrations
- Async boundaries, branching, retries, and compensating behavior are not represented correctly

Key references:

- `app/graph/call_graph.py`
- `app/output/bpmn_generator.py`

### 4. Scenario flow generation currently loses domain fidelity

The pipeline creates execution slices before domain classification. Scenario and execution-node domain fields are copied from `node.domain` at slice-build time, which means scenario flows can end up with missing or incomplete domain metadata.

Impact:

- Scenario docs may not attach cleanly to the right domain
- Cross-domain flow reporting becomes weaker than intended

Key references:

- `app/pipeline.py`
- `app/graph/call_graph.py`
- `app/ai/flow_analyzer.py`

### 5. Domain inference is too naive for enterprise bounded contexts

Domain inference currently returns the first non-framework namespace/path segment. In enterprise repos this often maps to a technical folder or repo segment rather than a real business capability.

Impact:

- Domain grouping becomes unstable
- Cross-domain edges are less meaningful
- Rollups can mix technical modules with business domains

Key references:

- `app/graph/domain_classifier.py`
- `app/pipeline.py`

### 6. Framework and artifact coverage is too narrow

The code parses application source but mostly ignores the non-code artifacts that define enterprise dependencies:

- OpenAPI and Swagger specs
- GraphQL schemas
- gRPC proto files
- Docker Compose, Helm, Kubernetes manifests
- Terraform and infrastructure descriptors
- package manager and build metadata for workspace relationships
- API gateway or ingress routing configuration
- queue/topic declarations and consumer configuration

Impact:

- Interface reconstruction misses the contract layer
- Service topology is incomplete
- Runtime dependencies are hard to infer from code alone

Key references:

- `app/repo/lang_detector.py`
- `app/repo/file_walker.py`
- `app/parsers/javascript.py`
- `app/parsers/python_parser.py`

### 7. Rollups are domain-centric, not integration-centric

The rollup stage creates `as-is`, `as-is-detail`, and `as-is-schema` documents per domain. Cross-domain links are limited to linking `as-is` docs when enough local edges exist. There is no dedicated integration inventory, dependency matrix, interface catalog, or service interaction view.

Impact:

- Enterprise users still need manual synthesis for service maps
- Interdependencies are not surfaced as primary artifacts
- The output favors component summaries over system reconstruction

Key references:

- `app/ai/rollup.py`
- `app/output/doc_generator.py`
- `app/pipeline.py`

### 8. Test coverage is not in a reliable state for the parser/graph core

The current test run fails during collection because parser dependencies are not available in the environment and some tests are stale relative to the current code.

Impact:

- Core parser/graph changes are risky to implement
- Refactoring confidence is lower than it should be

Key references:

- `app/requirements.txt`
- `app/tests/test_doc_generator.py`
- `app/tests/test_self_review_regen.py`

## Highest-Value Improvements

### Priority 1

- Introduce first-class external dependency nodes: service, API, queue, topic, DB, file store, identity provider
- Support workspace or multi-repo scans rather than a single repo only
- Improve symbol resolution using imports, receivers, DI wiring, namespace context, and interface bindings

### Priority 2

- Replace score-sorted scenario paths with ordered execution graphs
- Preserve branch, async, and boundary semantics
- Distinguish unresolved internal nodes from confirmed external integrations

### Priority 3

- Ingest non-code architecture artifacts and deployment descriptors
- Add dedicated integration outputs: interface catalog, dependency matrix, event map, service sequence views

### Priority 4

- Stabilize parser and graph tests
- Add regression fixtures for multi-service enterprise layouts

## Recommended Direction

The right target architecture is:

- code graph plus dependency graph plus execution graph
- single repo plus multi-repo workspace context
- domain docs plus integration docs plus scenario docs

That combination would move the tool from code understanding to enterprise system reconstruction.
