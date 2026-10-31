"""Score one fixture project against its ground_truth.json (shared by pytest and `python -m bench gate`)."""

from __future__ import annotations

from pathlib import Path

from .metrics import score_call_edges, score_entity_fields, score_forbidden_edges, score_sets
from .runner import run_fixture

DIMENSIONS = ("endpoints", "entities", "relationships", "call_edges", "di_resolution", "external_systems")


def score_project(repo_path: Path, gt: dict) -> dict:
    """{dimension: Score} for one fixture — deterministic, no LLM, no network."""
    res = run_fixture(repo_path, gt["language"])
    return {
        "endpoints": score_sets(res.endpoint_pairs(), {(e["method"], e["path"]) for e in gt["endpoints"]}),
        "entities": score_entity_fields(res.entity_fields(), gt["entities"]),
        "relationships": score_sets(res.relationship_pairs(),
                                    {(r["from_entity"], r["to_entity"]) for r in gt["relationships"]}),
        "call_edges": score_call_edges(res.edges, gt.get("key_call_edges", [])),
        "di_resolution": score_forbidden_edges(res.edges, gt.get("forbidden_call_edges", [])),
        "external_systems": score_sets(res.external_system_pairs(),
                                       {(s["name"], s["kind"]) for s in gt.get("external_systems", [])}),
    }
