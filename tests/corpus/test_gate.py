"""Accuracy gates (roadmap item M6.13): split, offline comparison, external precision/recall logic."""

from __future__ import annotations

import json

import pytest

from bench import gate
from bench.repos import Source


def test_split_is_seeded_and_baselines_match_it() -> None:
    cases = sorted(p.parent.name for p in gate.HARD.glob("*/ground_truth.json"))
    assert len(cases) >= 6
    assert [gate.split_of(c) for c in cases] == [gate.split_of(c) for c in cases]       # deterministic
    dev = {c for c in cases if gate.split_of(c) == "dev"}
    sealed = set(cases) - dev
    assert dev and sealed, "both halves must be non-empty"
    assert set(json.loads(gate.HARD_BASELINES["dev"].read_text())) == dev
    assert set(json.loads(gate.HARD_BASELINES["sealed"].read_text())) == sealed       # nobody hand-moves a case


def test_hard_cases_are_labelled_from_source() -> None:
    for gt_path in gate.HARD.glob("*/ground_truth.json"):
        gt = json.loads(gt_path.read_text())
        assert gt["_README"].startswith("Labelled from source"), gt_path
        assert gt["hard_case"]["catalog"] and gt["hard_case"]["item"].startswith("M6.")
        assert gt.get("key_call_edges") or gt.get("forbidden_call_edges")


def test_hard_cases_are_not_saturated() -> None:
    """The point of M6.13: at least some hard cases must currently fail, or the gate cannot tell good from bad."""
    scores = [d["f1"] for f in (gate.HARD_BASELINES["dev"], gate.HARD_BASELINES["sealed"])
              for dims in json.loads(f.read_text()).values() for d in dims.values()]
    assert min(scores) < 1.0


def test_offline_gate_passes_on_this_tree() -> None:
    res = gate.offline_gate()
    assert res.ok, res.failures
    assert set(res.report) >= {"fixtures", "hard:dev", "split"}
    assert "hard:sealed" not in res.report                     # sealed only with --sealed


def test_compare_flags_regressions_and_improvements() -> None:
    base = {"p": {"call_edges": {"precision": 1, "recall": 1, "f1": 1.0}, "di_resolution": {"f1": 0.5}}}
    cur = {"p": {"call_edges": {"f1": 0.5, "missing": ["a -> b"], "extra": []},
                 "di_resolution": {"f1": 1.0, "missing": [], "extra": []}}}
    res = gate.compare(cur, base, label="fixtures")
    assert res.failures == ["fixtures/p/call_edges: F1 1.000 → 0.500 missing=['a -> b'] extra=[]"]
    assert res.improvements == ["fixtures/p/di_resolution: F1 0.500 → 1.000"]
    sealed = gate.compare(cur, base, label="hard:sealed", detail=False)
    assert sealed.failures == ["hard:sealed: F1 1.000 → 0.500"]          # no case name, no edges


def _row(lang, origin, caller, target, verdict):
    return {"language": lang, "origin": origin, "caller": caller, "resolved_to": target, "verdict": verdict}


def _edge(lang, origin, caller, target):
    return {"language": lang, "origin": origin, "caller": caller, "resolved_to": target}


def test_precision_check_regression_known_wrong_and_drift() -> None:
    graded = [_row("java", "receiver_typed", f"C{i}", f"T{i}", "correct") for i in range(18)] + \
             [_row("java", "receiver_typed", "W1", "X1", "wrong"), _row("java", "receiver_typed", "W2", "X2", "wrong")]
    still = [_edge("java", "receiver_typed", "C0", "T0"), _edge("java", "receiver_typed", "W1", "X1"),
             _edge("java", "receiver_typed", "W2", "X2")]
    report, failures, warnings = gate.precision_check(graded, still, {"receiver_typed": 20})
    s = report["java/receiver_typed"]
    assert s["baseline_precision"] == 0.9 and s["current_precision"] == 0.333 and s["correct_edges_lost"] == 17
    assert s["known_wrong_still_emitted"] == ["W1 → X1", "W2 → X2"]
    assert failures and "below the baseline 95% lower bound" in failures[0]
    assert any("no longer emitted" in w for w in warnings) and any("re-sample" in w for w in warnings)
    same = [_edge("java", "receiver_typed", r["caller"], r["resolved_to"]) for r in graded]
    report, failures, warnings = gate.precision_check(graded, same, {"receiver_typed": 20})
    assert failures == [] and warnings == [] and report["java/receiver_typed"]["current_precision"] == 0.9


def test_recall_check_allows_the_unresolved_tradeoff() -> None:
    rec = {"ground_truth": {"status": "ok", "call_edge_recall": 1.0}, "unresolved": {"unresolved_calls_rows": 10}}
    drop = {"ground_truth": {"status": "ok", "call_edge_recall": 0.5}, "unresolved": {"unresolved_calls_rows": 10}}
    _, failures = gate.recall_check("r", drop, rec)
    assert failures and "without a matching rise" in failures[0]
    traded = {**drop, "unresolved": {"unresolved_calls_rows": 14}}
    assert gate.recall_check("r", traded, rec)[1] == []
    assert gate.recall_check("r", {"ground_truth": {"status": "no_ground_truth"}}, rec)[1] == []


def test_external_gate_skips_missing_sources(monkeypatch) -> None:
    import bench.repos as repos

    monkeypatch.setattr(repos, "locate", lambda repo: Source(None, "missing", "not fetched"))
    res = gate.external_gate(fetch=False)
    assert res.ok and res.report and all(r["status"] == "skipped" for r in res.report.values())
    assert any("use --fetch" in w for w in res.warnings)


def test_cli_exit_code(monkeypatch, capsys) -> None:
    from bench.__main__ import main

    assert main(["gate"]) == 0
    assert "gate: ok" in capsys.readouterr().out
    bad = gate.GateResult(failures=["fixtures/x/call_edges: F1 1.000 → 0.000"])
    monkeypatch.setattr(gate, "offline_gate", lambda **kw: bad)
    assert main(["gate", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["failures"]


@pytest.mark.parametrize("flag", ["--sealed"])
def test_sealed_run_reports_counts_only(flag, capsys) -> None:
    from bench.__main__ import main

    assert main(["gate", flag]) == 0
    out = capsys.readouterr().out
    assert "details withheld" in out
    for case in (p.parent.name for p in gate.HARD.glob("*/ground_truth.json") if gate.split_of(p.parent.name) == "sealed"):
        assert case not in out


def test_current_sheets_name_existing_graded_samples() -> None:
    """CURRENT.json (M6.14) names one graded sheet + recorded run per repo; every graded row has a reason."""
    current = gate.current_sheets()
    assert {"eshop", "spring-petclinic", "microblog", "node-express-realworld"} <= set(current)
    for repo_id, (sheet, run) in current.items():
        assert sheet.name.startswith(repo_id + "-") and run.name.endswith(f"-{repo_id}.json")
        rows = gate.load_sheet(sheet)
        assert rows and all(r["verdict"] in ("correct", "wrong") and r["reason"].strip() for r in rows)


def test_current_sheets_rejects_a_missing_file(tmp_path) -> None:
    bad = tmp_path / "CURRENT.json"
    bad.write_text(json.dumps({"x": {"sheet": "nope.csv", "run": "nope.json"}}))
    with pytest.raises(FileNotFoundError):
        gate.current_sheets(bad)
