# ADR-0006: Clean-room rule for repowise-derived knowledge

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related items:** all; especially M0.12, M5.*, M6.*

## Context
repowise (`../repowise`) is AGPL-3.0-or-later with a commercial dual licence. ai-discovery is licensed AGPL-3.0 (owner decision 2026-10-08; `LICENSE`, `pyproject.toml`), so copying repowise code would now be license-compatible with attribution. The no-copy rule below is kept as an **engineering policy**, per the owner's choice of "own engine, adopt patterns": it keeps the codebase coherent and avoids tracking a fast-moving upstream. Lifting it requires an explicit owner decision recorded in this ADR.

The owner wants repowise's **handled edge cases and UX patterns** used as a benchmark and as design input, without copying code.

## Decision
1. **No repowise source code**, tests, templates, prompts, query files (`*.scm`) or UI components are copied, translated line-by-line, or pasted into this repo or into prompts used to write this repo's code.
2. **Implementers work from prose:** `docs/roadmap/research/edge-case-catalog.md` and `docs/roadmap/research/repowise-benchmark.md`. These describe *what* case is handled and *why*, at the level of behaviour, citing repowise docs and test names as evidence.
3. **Implementing sessions do not open `../repowise/packages/**` source.** If a behaviour question is not answered by the catalog, a research task (lane Q) answers it in prose, and the catalog is updated.
4. **Benchmarks run repowise as a black box** (CLI and MCP outputs only) in lane Q (M0.12, M5.7).
5. **PR attestation line**, included verbatim in every roadmap PR description:
   `No repowise code was copied or translated.`
   (This covers code, tests, templates, prompts and query files, per rule 1.)

## Consequences
- This adds some reimplementation effort in exchange for a coherent, independently maintained engine.
- Catalog quality matters, so research rows must state the behaviour precisely: inputs, expected outcome, caps.
