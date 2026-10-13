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
        # M1.4: run_pipeline is a lock wrapper; the phase body lives here.
        if isinstance(node, ast.FunctionDef) and node.name == "_run_pipeline_unlocked":
            return node
    raise AssertionError("_run_pipeline_unlocked not found in pipeline.py")


def test_llm_client_bound_before_first_use():
    """The LLM client must be on the RunContext before any stage runs.

    M1.8 (ADR-0003): stages run inside ``StageRunner(ctx).run()``; the screens
    stage (phase 2) is the first LLM consumer. Assert ``ctx.llm_client = ...``
    precedes the runner call.
    """
    func = _run_pipeline_funcdef()

    first_store = None
    runner_call = None
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Attribute) and t.attr == "llm_client" \
                        and isinstance(t.value, ast.Name) and t.value.id == "ctx":
                    first_store = node.lineno if first_store is None else min(first_store, node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "StageRunner":
            runner_call = node.lineno

    assert first_store is not None, "ctx.llm_client is never assigned in _run_pipeline_unlocked"
    assert runner_call is not None, "StageRunner is never called"
    assert first_store < runner_call, (
        f"CRIT-1 regression: ctx.llm_client is assigned at line {first_store}, after the "
        f"stages start at line {runner_call}; screen specs would see no LLM client."
    )


def test_screens_is_the_first_stage():
    from ai_discovery.stages.runner import STAGES

    assert STAGES[0].name == "screens"


def test_screens_stage_reads_llm_client_from_context():
    from ai_discovery.stages import screens

    source = Path(screens.__file__).read_text()
    assert "llm_client = ctx.llm_client" in source
