"""Regression guard for CRIT-1: screen-spec Phase 2 must not reference
``llm_client`` before it is constructed.

Background
----------
``run_pipeline`` builds ``llm_client = LLMClient(config)`` for the embedding
phase, but Phase 2 (screen-centric spec generation) also needs it. If the
construction sits *after* Phase 2 in the function body, Python treats
``llm_client`` as a function-local everywhere in ``run_pipeline`` and Phase 2
raises ``UnboundLocalError: local variable 'llm_client' referenced before
assignment`` the moment a menu is detected (``screens`` non-empty). Backend-only
repos with no menu mask the bug because the screen branch never executes.

This is a use-before-assignment ordering invariant, so we assert it statically
against the source AST — fast, deterministic, and provider-agnostic (the crash
fires at the ``_budget_ok(llm_client, ...)`` name lookup, before any LLM call).
The complementary end-to-end check is a live ``discover scan`` on a
menu-bearing fixture; see ``tests/fixtures/projects/spring-boot-app``.
"""

import ast
from pathlib import Path

import ai_discovery.pipeline as pipeline_mod


def _run_pipeline_funcdef() -> ast.FunctionDef:
    source = Path(pipeline_mod.__file__).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_pipeline":
            return node
    raise AssertionError("run_pipeline not found in pipeline.py")


def test_llm_client_bound_before_first_use():
    """llm_client's first assignment must precede its first use in run_pipeline."""
    func = _run_pipeline_funcdef()

    first_store = None
    first_load = None
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and node.id == "llm_client":
            if isinstance(node.ctx, ast.Store):
                first_store = node.lineno if first_store is None else min(first_store, node.lineno)
            elif isinstance(node.ctx, ast.Load):
                first_load = node.lineno if first_load is None else min(first_load, node.lineno)

    assert first_store is not None, "llm_client is never assigned in run_pipeline"
    assert first_load is not None, "llm_client is never used in run_pipeline"
    assert first_store <= first_load, (
        f"CRIT-1 regression: llm_client is used at line {first_load} but not "
        f"constructed until line {first_store}. Phase 2 (screen specs) will raise "
        f"UnboundLocalError on any repo with a detectable menu. Construct "
        f"llm_client before Phase 2."
    )
