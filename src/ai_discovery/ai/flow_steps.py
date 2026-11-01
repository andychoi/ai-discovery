"""Scenario-flow step vocabulary and normalization (ADR-0007, roadmap item M3.11).

A scenario flow is an LLM narration of one execution slice. It may **group** steps (``PHASE``), mark
iteration over a collection (``LOOP``) and record a step's ``condition`` when that condition is a guard shown
to the model. It may **not** claim process structure that static analysis cannot observe:

- ``GATEWAY``: either-or branching. A call graph cannot tell calls made one after another from alternatives.
- ``USER_TASK``: human or manual steps. People are not in the code.

Older stores, cached model answers and a model that ignores the prompt can still produce those types.
``normalize_structured_steps`` rewrites them. A gateway's arms are inlined and each step keeps its arm's
condition. Manual tasks are dropped, because no code node backs them. Every reader runs it (the scan, MCP
``get_flow``), so retired types never reach a renderer.
"""

from __future__ import annotations

from typing import Any

LEAF_TYPES = frozenset({"PROCESS", "DB", "EXTERNAL", "EXTERNAL_API", "QUEUE", "ENTRY"})
WRAPPER_TYPES = frozenset({"PHASE", "LOOP"})
RETIRED_TYPES = frozenset({"GATEWAY", "USER_TASK"})
OTHERWISE = "otherwise"


def _condition(raw: Any) -> str:
    cond = str(raw or "").strip()
    return OTHERWISE if cond.lower() in ("else", "otherwise", "default") else cond


def normalize_structured_steps(nodes: Any) -> list[dict]:
    """Hierarchical steps with only the allowed types (see module doc). Input is never mutated."""
    out: list[dict] = []
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("type") or "PROCESS").upper()
        if ntype == "USER_TASK":
            continue
        if ntype == "GATEWAY":
            for branch in node.get("branches") or []:
                if not isinstance(branch, dict):
                    continue
                cond = _condition(branch.get("condition"))
                for step in normalize_structured_steps(branch.get("steps")):
                    if cond and not step.get("condition"):
                        step["condition"] = cond
                    out.append(step)
            out.extend(normalize_structured_steps(node.get("children")))
            continue
        clean = {k: v for k, v in node.items() if k not in ("branches", "children", "type")}
        clean["type"] = ntype if ntype in LEAF_TYPES | WRAPPER_TYPES else "PROCESS"
        if clean.get("condition"):
            clean["condition"] = _condition(clean["condition"])
        children = normalize_structured_steps(node.get("children"))
        if children:
            clean["children"] = children
        out.append(clean)
    return out


def normalize_flat_steps(steps: Any) -> list[dict]:
    """Flat steps (``[{step, name, type, description}]``) without retired types, renumbered."""
    out: list[dict] = []
    for s in steps if isinstance(steps, list) else []:
        if not isinstance(s, dict):
            continue
        stype = str(s.get("type") or "PROCESS").upper()
        if stype == "USER_TASK":
            continue
        out.append({**s, "step": len(out) + 1,
                    "type": stype if stype in LEAF_TYPES else "PROCESS"})
    return out


def flatten_structured_steps(structured: Any) -> list[dict]:
    """Depth-first flat view of normalized steps; ``PHASE``/``LOOP`` wrappers are not emitted."""
    flat: list[dict] = []

    def walk(nodes: list[dict]) -> None:
        for node in nodes:
            if node["type"] in LEAF_TYPES:
                step = {"step": len(flat) + 1, "name": node.get("name", "Step"), "type": node["type"],
                        "description": node.get("description", "")}
                if node.get("condition"):
                    step["condition"] = node["condition"]
                flat.append(step)
            walk(node.get("children", []))

    walk(normalize_structured_steps(structured))
    return flat
