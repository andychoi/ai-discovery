"""Corpus Phase 3 — accuracy gates over REAL repos pinned at fixed SHAs.

Same dimensions/scorers as the authored-fixture harness, run against the
repos in manifest.json. Ground truth is human-labeled from the source AT THE
PINNED SHA (a curated sample — real repos are too big to label exhaustively)
and committed; the repos themselves live in .cache/ (gitignored).

Tests SKIP (don't fail) when a repo isn't fetched or drifted off its pin, so
default/offline runs stay green. Enable with:

    python tests/corpus/external/fetch.py
    pytest tests/corpus/external -v

Refresh baselines after intentional improvements (only fetched repos are
rewritten; entries for unfetched repos are preserved):

    python -m tests.corpus.external.test_external_corpus
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.corpus.test_corpus_accuracy import _score_project

ROOT = Path(__file__).parent
CACHE = ROOT / ".cache"
GT_DIR = ROOT / "ground_truth"
BASELINE_PATH = ROOT / "external_baseline.json"

_REPOS = json.loads((ROOT / "manifest.json").read_text())["repos"]


def _head(dest: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()


def _checked_out(repo: dict) -> Path | None:
    """Cache dir when present AND at the pinned SHA, else None."""
    dest = CACHE / repo["name"]
    if not dest.is_dir():
        return None
    return dest if _head(dest) == repo["sha"] else None


@pytest.mark.accuracy
@pytest.mark.parametrize("repo", _REPOS, ids=[r["name"] for r in _REPOS])
def test_external_repo_accuracy(repo: dict):
    dest = CACHE / repo["name"]
    if not dest.is_dir():
        pytest.skip(
            f"{repo['name']} not fetched — run `python tests/corpus/external/fetch.py`"
        )
    head = _head(dest)
    if head != repo["sha"]:
        pytest.skip(
            f"{repo['name']} at {head[:12]} but pinned to {repo['sha'][:12]} — "
            f"re-run fetch.py"
        )

    gt = json.loads((GT_DIR / f"{repo['name']}.json").read_text())
    scores = _score_project(dest, gt)

    # Absolute floors come from the GT file: real repos legitimately score
    # below 1.0 on some dimensions; the floor records the minimum we accept.
    for dim, floor in (gt.get("_floors") or {}).items():
        sc = scores[dim]
        assert sc.f1 >= floor, (
            f"{gt['project']}/{dim}: F1 {sc.f1:.2f} < floor {floor}. "
            f"missing={sc.missing} extra={sc.extra}"
        )

    # Regression gate against the recorded external baseline.
    baseline = json.loads(BASELINE_PATH.read_text()) if BASELINE_PATH.exists() else {}
    base = baseline.get(gt["project"], {})
    for dim, sc in scores.items():
        if dim in base:
            assert sc.f1 >= base[dim]["f1"] - 1e-3, (
                f"{gt['project']}/{dim}: F1 regressed "
                f"{base[dim]['f1']:.3f} -> {sc.f1:.3f}. "
                f"missing={sc.missing} extra={sc.extra}"
            )


def _refresh_baseline() -> dict:
    baseline = json.loads(BASELINE_PATH.read_text()) if BASELINE_PATH.exists() else {}
    for repo in _REPOS:
        dest = _checked_out(repo)
        if dest is None:
            print(f"  skip {repo['name']} (not fetched at pin)")
            continue
        gt = json.loads((GT_DIR / f"{repo['name']}.json").read_text())
        baseline[gt["project"]] = {
            dim: sc.as_dict() for dim, sc in _score_project(dest, gt).items()
        }
        print(f"  scored {repo['name']}")
    BASELINE_PATH.write_text(json.dumps(baseline, indent=1) + "\n")
    return baseline


if __name__ == "__main__":
    _refresh_baseline()
    print(f"wrote {BASELINE_PATH}")
