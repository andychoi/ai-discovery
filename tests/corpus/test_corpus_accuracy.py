"""Corpus accuracy gate (HIGH-10, Phase 1).

Runs the real, deterministic extraction pipeline over each fixture that ships a
human-labeled ground_truth.json and checks:

1. **Absolute target** (>= 0.80, from CLAUDE.md's parser-accuracy goal) for the
   dimensions/fixtures expected to meet it.
2. **No regression** vs the recorded baseline (tests/corpus/baseline.json) for
   EVERY dimension — so even a dimension below the absolute target (a documented
   extraction gap, e.g. express Mongoose entities) can only get better, never
   silently worse.

No LLM, no network, no mocks — this is the layer the verified-facts machinery
depends on, so it must be measured directly. To refresh the baseline after an
intentional accuracy improvement, run:  python -m tests.corpus.test_corpus_accuracy
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .metrics import (
    score_call_edges,
    score_entity_fields,
    score_forbidden_edges,
    score_sets,
)
from .runner import run_fixture

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "projects"
BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"

_HARD_TARGET = 0.80
_CALL_EDGE_TARGET = 0.85  # CLAUDE.md call-resolution goal

# (project, dimension) pairs held to the absolute bar. Dimensions with a
# documented extraction gap (see each ground_truth.json's *_note) are omitted
# here — they are still regression-guarded against the baseline. Call-edge
# dimensions use _CALL_EDGE_TARGET (0.85); the rest use _HARD_TARGET (0.80).
HARD_TARGETS = {
    ("spring-boot-app", "endpoints"),
    ("spring-boot-app", "entities"),
    ("spring-boot-app", "relationships"),
    ("spring-boot-app", "call_edges"),
    ("aspnet-core-app", "endpoints"),
    ("aspnet-core-app", "entities"),
    ("aspnet-core-app", "call_edges"),
    ("express-app", "endpoints"),
    ("express-app", "entities"),  # JS Mongoose entity extraction shipped
    ("express-app", "call_edges"),
    # HIGH-3 shipped (Java + C# + Python): DI/receiver-type resolution eliminates
    # the fan-out, so the no-false-edge guarantee is a hard target on every
    # collision fixture, not just a baseline gauge.
    ("java-di-collision", "di_resolution"),
    ("java-di-collision", "call_edges"),
    ("csharp-di-collision", "di_resolution"),
    ("csharp-di-collision", "call_edges"),
    ("python-di-collision", "di_resolution"),
    ("python-di-collision", "call_edges"),
    ("js-di-collision", "di_resolution"),
    ("js-di-collision", "call_edges"),
    # HIGH-8: external-client calls become first-class typed external-system nodes.
    ("external-systems", "external_systems"),
}


def _load_fixtures():
    out = []
    for gt_path in sorted(FIXTURES_DIR.glob("*/ground_truth.json")):
        out.append((gt_path.parent, json.loads(gt_path.read_text())))
    return out


_FIXTURES = _load_fixtures()


def _score_project(repo_path: Path, gt: dict) -> dict:
    res = run_fixture(repo_path, gt["language"])
    return {
        "endpoints": score_sets(
            res.endpoint_pairs(), {(e["method"], e["path"]) for e in gt["endpoints"]}
        ),
        "entities": score_entity_fields(res.entity_fields(), gt["entities"]),
        "relationships": score_sets(
            res.relationship_pairs(),
            {(r["from_entity"], r["to_entity"]) for r in gt["relationships"]},
        ),
        "call_edges": score_call_edges(res.edges, gt.get("key_call_edges", [])),
        "di_resolution": score_forbidden_edges(res.edges, gt.get("forbidden_call_edges", [])),
        "external_systems": score_sets(
            res.external_system_pairs(),
            {(s["name"], s["kind"]) for s in gt.get("external_systems", [])},
        ),
    }


def _compute_report() -> dict:
    report = {}
    for repo_path, gt in _FIXTURES:
        report[gt["project"]] = {
            dim: sc.as_dict() for dim, sc in _score_project(repo_path, gt).items()
        }
    return report


@pytest.mark.accuracy
@pytest.mark.parametrize(
    "repo_path,gt", _FIXTURES, ids=[gt["project"] for _, gt in _FIXTURES]
)
def test_fixture_accuracy(repo_path: Path, gt: dict):
    project = gt["project"]
    scores = _score_project(repo_path, gt)
    baseline = json.loads(BASELINE_PATH.read_text()) if BASELINE_PATH.exists() else {}
    base = baseline.get(project, {})

    for dim, sc in scores.items():
        if (project, dim) in HARD_TARGETS:
            target = _CALL_EDGE_TARGET if dim == "call_edges" else _HARD_TARGET
            assert sc.f1 >= target, (
                f"{project}/{dim}: F1 {sc.f1:.2f} < absolute target {target}. "
                f"missing={sc.missing} extra={sc.extra}"
            )
        if dim in base:
            assert sc.f1 >= base[dim]["f1"] - 1e-3, (
                f"{project}/{dim}: F1 regressed {base[dim]['f1']:.3f} -> {sc.f1:.3f}. "
                f"missing={sc.missing} extra={sc.extra}"
            )


if __name__ == "__main__":
    # Refresh the baseline (run intentionally after an accuracy improvement).
    BASELINE_PATH.write_text(json.dumps(_compute_report(), indent=2) + "\n")
    print(f"Wrote baseline to {BASELINE_PATH}")
    print(json.dumps(_compute_report(), indent=2))
