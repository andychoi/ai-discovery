---
name: call-graph-debug
description: Trace why a specific call in ai-discovery resolved to the wrong target, to a guessed target, or not at all — find the call site, follow the resolver, read the origin and confidence, validate against source, and decide whether to change a heuristic or accept the result. Use for "why does X call Y in the graph?" or "why is this call unresolved?".
---

# call-graph-debug

Follow `docs/guides/call-graph/debugging-workflow.md`, with the v1 edge contract in mind:

1. **Locate the call.** File, line, caller qualified name. Use MCP `get_context` on the caller (callees carry `origin`, `confidence`, `followable`) or query `call_edges` / `unresolved_calls` in `.discovery/discovery.db` (read-only).
2. **Read the origin.** Every edge has exactly one origin with a fixed confidence (ADR-0004, `src/ai_discovery/graph/origins.py`). Unresolved calls carry a reason (`ambiguous`, `not_found`, `dynamic`, `builtin`, `external`, `dispatch_cap`, `di_ambiguous`).
3. **Trace the resolver** in `src/ai_discovery/graph/call_graph.py` (`resolve_calls`) for that call: which tier matched or why none did.
4. **Validate against source**: imports, receiver type, DI registration, inheritance.
5. **Decide:** accept; fix a parser gap (missing imports/call sites → lane C item); adjust a resolver rule (update ADR-0004 / `docs/architecture/decisions.md` in the same change); or leave unresolved (dynamic code).
6. **Measure:** any resolver change re-runs `python -m bench run` and reports precision per origin vs the last baseline (`docs/roadmap/benchmarks/`).

References: `docs/guides/call-graph/resolution-heuristics.md`, `test-strategy.md`.
