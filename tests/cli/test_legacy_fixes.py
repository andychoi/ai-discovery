"""Regression tests for the interim legacy-CLI fixes (roadmap item M0.9).

`verify-drift` must not pass vacuously in CI, and `ingest-docs` must not
crash on its own option validation. Both commands are replaced in M2.6.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app

runner = CliRunner()


def _spec(path: Path, rel: str, digest: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ndoc_id: x\nsource_hashes:\n  {rel}: {digest}\n---\n\n# spec\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "shop"
    (r / "src").mkdir(parents=True)
    (r / "src" / "Page.vue").write_text("<template/>\n")
    return r


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_verify_drift_missing_spec_dir_fails(repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # no ./data either
    res = runner.invoke(app, ["verify-drift", str(repo)])
    assert res.exit_code == 5, res.output
    assert ".discovery/docs/screens" in res.output.replace("\n", "")


def test_verify_drift_empty_spec_dir_fails(repo: Path, tmp_path: Path) -> None:
    empty = tmp_path / "specs"
    empty.mkdir()
    res = runner.invoke(app, ["verify-drift", str(repo), "--spec-dir", str(empty)])
    assert res.exit_code == 5 and "nothing to check" in res.output


def test_verify_drift_defaults_to_the_v1_screens_dir(repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    page = repo / "src" / "Page.vue"
    spec = repo / ".discovery" / "docs" / "screens" / "page.md"
    _spec(spec, "src/Page.vue", _sha(page))
    ok = runner.invoke(app, ["verify-drift", str(repo)])
    assert ok.exit_code == 0 and "in sync" in ok.output
    page.write_text("<template>changed</template>\n")
    drifted = runner.invoke(app, ["verify-drift", str(repo)])
    assert drifted.exit_code == 1


def test_verify_drift_falls_back_to_the_legacy_screens_dir(repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _spec(tmp_path / "data" / "output-shop" / "screens" / "page.md", "src/Page.vue", _sha(repo / "src" / "Page.vue"))
    res = runner.invoke(app, ["verify-drift", str(repo)])
    assert res.exit_code == 0, res.output


def test_ingest_docs_validates_push_mode_without_crashing(tmp_path: Path) -> None:
    bad = runner.invoke(app, ["ingest-docs", str(tmp_path), "-p", "x", "--push", "ftp"])
    assert bad.exit_code == 1 and "Invalid --push" in bad.output
    assert not isinstance(bad.exception, NameError)


def test_ingest_docs_dry_run_runs(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# Orders as-is\n\nCurrent state of orders.\n")
    res = runner.invoke(app, ["ingest-docs", str(tmp_path), "-p", "x", "--dry-run"])
    assert not isinstance(res.exception, NameError), res.output
    assert res.exit_code == 0, res.output
