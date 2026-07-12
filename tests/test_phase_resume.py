"""Tests for the pipeline phase-number / resume-skip helpers.

These drive resume correctness (which phase to restart from, which to skip) and
had no direct coverage. They also pin the int phase-number convention (W2-1):
the old float + abs()<0.01 tolerance is gone.
"""

from __future__ import annotations

import pytest

from ai_discovery.pipeline import (
    _parse_phase_spec,
    _next_phase,
    _should_skip_phase,
    _phase_should_run,
    _PHASE_SPECS,
)


# ── _parse_phase_spec ────────────────────────────────────────────────────────


def test_parse_phase_spec_number():
    v = _parse_phase_spec("14")
    assert v == 14 and isinstance(v, int)


def test_parse_phase_spec_legacy_float_string():
    v = _parse_phase_spec("14.0")
    assert v == 14 and isinstance(v, int)


def test_parse_phase_spec_by_name():
    assert _parse_phase_spec("self_review") == 17
    assert _parse_phase_spec("parse") == 6
    assert _parse_phase_spec("SELF_REVIEW") == 17  # case-insensitive


def test_parse_phase_spec_invalid_raises():
    with pytest.raises(ValueError):
        _parse_phase_spec("not_a_phase")


# ── _next_phase ──────────────────────────────────────────────────────────────


def test_next_phase_walks_contiguous_ints():
    assert _next_phase(8) == 9
    assert _next_phase(5) == 6
    # 2 is the screen phase; next registered is 5.
    assert _next_phase(2) == 5


def test_next_phase_past_last_is_none():
    last = max(_PHASE_SPECS)
    assert _next_phase(last) is None


# ── _should_skip_phase ───────────────────────────────────────────────────────


def test_should_skip_phase_by_number_and_name():
    assert _should_skip_phase(14, ["14"]) is True
    assert _should_skip_phase(17, ["self_review"]) is True
    assert _should_skip_phase(13, ["14"]) is False


def test_should_skip_phase_empty_list():
    assert _should_skip_phase(14, []) is False


def test_should_skip_phase_ignores_bad_specs():
    # An unparseable skip spec is ignored, not fatal.
    assert _should_skip_phase(14, ["bogus", "14"]) is True
    assert _should_skip_phase(13, ["bogus"]) is False


# ── _phase_should_run ────────────────────────────────────────────────────────


def test_phase_should_run_gates_on_start_phase():
    # Resuming from phase 11: earlier phases are skipped, 11+ run.
    assert _phase_should_run(9, 11, []) is False
    assert _phase_should_run(11, 11, []) is True
    assert _phase_should_run(14, 11, []) is True


def test_phase_should_run_gates_on_skip_list():
    assert _phase_should_run(14, None, ["14"]) is False
    assert _phase_should_run(17, None, ["self_review"]) is False
    assert _phase_should_run(13, None, ["14"]) is True


def test_phase_should_run_no_constraints():
    assert _phase_should_run(6, None, []) is True


# ── _phase_lang_detect (extracted phase body, W2-1) ──────────────────────────
# The lang-detect phase is now a self-contained module function. These isolate
# its run-vs-resume branch selection without a full pipeline run.


def test_phase_lang_detect_run_branch(monkeypatch, tmp_path):
    import contextlib
    import ai_discovery.pipeline as pipe
    import ai_discovery.repo.lang_detector as ld

    monkeypatch.setattr(ld, "detect_languages", lambda p: {"python": 3, "java": 1})
    # Neutralize the checkpoint context (no DB needed for this unit).
    monkeypatch.setattr(pipe, "_with_checkpoint",
                        lambda *a, **k: contextlib.nullcontext())

    stats = pipe._phase_lang_detect(tmp_path, tmp_path / "x.db", 1,
                                    start_phase=None, skip_phases=[])
    assert stats == {"python": 3, "java": 1}


def test_phase_lang_detect_resume_branch_recomputes(monkeypatch, tmp_path):
    import ai_discovery.pipeline as pipe
    import ai_discovery.repo.lang_detector as ld

    calls = {"n": 0}

    def _fake(p):
        calls["n"] += 1
        return {"go": 2}

    monkeypatch.setattr(ld, "detect_languages", _fake)

    # start_phase=6 → phase 5 is already complete; the resume branch re-detects
    # (cheap) without entering the checkpoint context.
    stats = pipe._phase_lang_detect(tmp_path, tmp_path / "x.db", 1,
                                    start_phase=6, skip_phases=[])
    assert stats == {"go": 2}
    assert calls["n"] == 1
