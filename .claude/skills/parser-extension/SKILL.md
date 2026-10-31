---
name: parser-extension
description: Add or extend language support in ai-discovery (Go, Rust, Kotlin, VB.NET, ABAP, a new framework) by following the parser extension checklist — grammar pre-flight, AST mapping, name resolution, framework detection, tests, registration, accuracy baseline. Use when asked to add a language or fix a parser's coverage.
---

# parser-extension

Follow `docs/guides/parsers/extension-checklist.md` step by step; do not improvise a new pattern.

1. **Roadmap first.** Check `docs/roadmap/milestones/M6-graph-accuracy.md` (and M7 for SAP) for an item that covers the language; if one exists, run `/roadmap-next <ID>` instead.
2. **Tier choice.** For a quick on-ramp, the regex import-map tier (`src/ai_discovery/parsers/import_map.py`) gives imports, top-level symbols and conservative call sites. A real tree-sitter parser is needed for endpoints, entities and resolution.
3. **Checklist phases:** pre-flight (grammar on PyPI, test corpus) → AST mapping (function/class/call node types) → qualified names and imports → framework detection → tests (unit + fixture) → registration (`repo/lang_detector.py`, parser registry) → accuracy baseline.
4. **Edge contract.** New call sites must resolve through the closed origin vocabulary (ADR-0004, `graph/origins.py`); ambiguity produces no edge and an `unresolved_calls` row.
5. **Verify:** `pytest -q tests -k <language>` and `python -m bench run` on a corpus repo; record precision per origin in PROGRESS.md.

References: `docs/guides/parsers/architecture.md`, `language-patterns.md`, `docs/roadmap/design/graph-accuracy.md`.
