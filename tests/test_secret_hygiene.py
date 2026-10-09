"""Secret hygiene (roadmap item M0.8)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_discovery.config import DiscoveryConfig
from ai_discovery.core.redact import MASK, redact_mapping, redact_text, safe_message
from ai_discovery.db import get_conn, init_db, record_phase_error
from ai_discovery.repo import resolver

FAKE_KEY = "sk-test-0123456789abcdefABCDEF"
_SECRET_FIELD = re.compile(r"(key|token|secret|password)", re.I)


def _secret_config() -> DiscoveryConfig:
    cfg = DiscoveryConfig(provider="bedrock", budget_limit_usd=100.0, max_concurrent=1)
    cfg.openai.api_key = FAKE_KEY
    cfg.anthropic.api_key = "anthropic-secret-value-123"
    cfg.gemini.api_key = "AIza" + "x" * 35
    return cfg


def _secret_values(obj, path=""):
    """Yield (path, value) for non-empty string values under secret-named keys."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _SECRET_FIELD.search(k) and isinstance(v, str) and v and v != MASK:
                yield f"{path}.{k}", v
            yield from _secret_values(v, f"{path}.{k}")


# ── redaction primitives ──────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "https://x-access-token:ghp_abcdefghijklmnopqrstuvwxyz1234@github.com/o/r.git",
    "Authorization: Bearer abcdefghijklmnop1234",
    f"api_key={FAKE_KEY}",
    "OPENAI_API_KEY: 'whatever-secret-1234'",
    "aws AKIAABCDEFGHIJKLMNOP id",
])
def test_redact_text_masks_secrets(text: str) -> None:
    out = redact_text(text)
    assert MASK in out
    for secret in ("ghp_abcdefghijklmnopqrstuvwxyz1234", "abcdefghijklmnop1234", FAKE_KEY,
                   "whatever-secret-1234", "AKIAABCDEFGHIJKLMNOP"):
        assert secret not in out


def test_redact_text_keeps_ordinary_text() -> None:
    text = "Parse error in OrderService.cs line 42: unexpected token '}'"
    assert redact_text(text) == text


def test_safe_message_redacts_before_truncating() -> None:
    # The secret straddles the truncation boundary: truncate-then-redact would
    # leave its head in the stored message.
    msg = "x" * 490 + f" api_key={FAKE_KEY}"
    out = safe_message(msg, 500)
    assert len(out) <= 500 and FAKE_KEY[:8] not in out


def test_redact_mapping_blanks_config_secrets() -> None:
    redacted = redact_mapping(asdict(_secret_config()))
    assert list(_secret_values(redacted)) == []
    assert redacted["openai"]["api_key"] == MASK
    assert redacted["mlx_gemma"]["api_key"] == ""  # unset stays unset


# ── persistence ───────────────────────────────────────────────────────────

def test_scan_run_config_json_has_no_secrets(tmp_path: Path) -> None:
    from tests.golden.stub_llm import StubLLM
    from tests.golden.test_golden import GOLDEN_REPOS, _build_repo
    from ai_discovery.ai.self_review import ReviewClaim

    repo = _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])
    with (
        patch("ai_discovery.ai.llm_client.LLMClient", return_value=StubLLM()),
        patch("ai_discovery.rag.embedder.embed_chunks", return_value={"embedded": 0, "dim": 256}),
        patch("ai_discovery.rag.retriever.search", return_value=[]),
        patch("ai_discovery.ai.self_review.review_document",
              return_value=[ReviewClaim(claim_text="c", status="verified")]),
        patch("ai_discovery.ai.self_review.persist_claims"),
    ):
        from ai_discovery.pipeline import run_pipeline

        run_pipeline(repo=str(repo), branch="main", project_slug="s", output_dir=tmp_path / "out",
                     docs_root=tmp_path / "docs", config=_secret_config())
    db = tmp_path / "out" / "output-s" / "discovery-s.db"
    raw = sqlite3.connect(db).execute("SELECT config_json FROM scan_runs").fetchone()[0]
    assert FAKE_KEY not in raw and "anthropic-secret-value-123" not in raw
    assert list(_secret_values(json.loads(raw))) == []
    assert db.read_bytes().find(FAKE_KEY.encode()) == -1


def test_record_phase_error_redacts(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    init_db(db)
    conn = get_conn(db)
    conn.execute("INSERT INTO scan_runs (repo_path, started_at, status) VALUES ('r', 'now', 'running')")
    conn.commit()
    conn.close()
    record_phase_error(db, 1, 14, "tier3_doc_rollup", f"401 from provider, api_key={FAKE_KEY}")
    stored = sqlite3.connect(db).execute("SELECT error_msg FROM phase_checkpoints").fetchone()[0]
    assert FAKE_KEY not in stored and MASK in stored


def test_migration_scrubs_existing_rows(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    init_db(db)
    conn = get_conn(db)
    conn.execute(
        "INSERT INTO scan_runs (repo_path, started_at, status, config_json) VALUES ('r','now','completed',?)",
        (json.dumps(asdict(_secret_config())),),
    )
    conn.execute(
        "INSERT INTO phase_checkpoints (scan_id, phase_num, phase_name, status, error_msg) "
        "VALUES (1, 14, 'x', 'failed', ?)", (f"token={FAKE_KEY}",),
    )
    conn.execute("DELETE FROM schema_version WHERE version >= 15")  # pretend pre-M0.8 DB
    conn.commit()
    conn.close()

    init_db(db)  # runs the v15 migration

    conn = sqlite3.connect(db)
    assert FAKE_KEY not in conn.execute("SELECT config_json FROM scan_runs").fetchone()[0]
    assert FAKE_KEY not in conn.execute("SELECT error_msg FROM phase_checkpoints").fetchone()[0]


# ── git credentials ───────────────────────────────────────────────────────

def test_git_env_puts_token_in_header_not_url(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret_value_123456789012")
    env = resolver._git_env("https://github.com/o/r.git")
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "ghp_secret_value" not in env["GIT_CONFIG_VALUE_0"]  # base64, not raw


def test_git_env_without_token_has_no_header(monkeypatch) -> None:
    for var in ("GITHUB_TOKEN", "GH_TOKEN", "GITEA_TOKEN", "DISCOVERY_GITEA_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert "GIT_CONFIG_COUNT" not in resolver._git_env("https://github.com/o/r.git")


def test_clean_url_strips_credentials() -> None:
    assert resolver._clean_url("https://x-access-token:abc@github.com/o/r.git") == "https://github.com/o/r.git"
    assert resolver._clean_url("https://github.com/o/r.git") == "https://github.com/o/r.git"


def test_scrub_remote_removes_embedded_token(tmp_path: Path) -> None:
    repo = tmp_path / "clone"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://git:tok-123@gitea.example.com/o/r.git"],
                   cwd=repo, check=True)
    resolver._scrub_remote(repo)
    url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo, capture_output=True,
                         text=True, check=True).stdout.strip()
    assert url == "https://gitea.example.com/o/r.git"
    assert "tok-123" not in (repo / ".git" / "config").read_text()


def test_git_calls_have_timeout(monkeypatch) -> None:
    seen = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(resolver.subprocess, "run", fake_run)
    resolver._git(["status"])
    assert seen.get("timeout") == resolver._GIT_TIMEOUT
