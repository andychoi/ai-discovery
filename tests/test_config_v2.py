"""Layered, validated configuration (roadmap item M1.3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ai_discovery.config.loader import ConfigError, config_sources, load_config, user_config_path
from ai_discovery.config.template import render_template

ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = ["bedrock", "ollama", "mlx-gemma", "mlx-qwen", "openai", "gemini", "anthropic"]


@pytest.fixture
def no_env(monkeypatch):
    for var in ("DISCOVERY_LLM_PROVIDER", "OLLAMA_URL", "OPENAI_API_KEY", "DISCOVERY_TIER3D_MODEL"):
        monkeypatch.delenv(var, raising=False)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── precedence: defaults < user < project < env < CLI ─────────────────────

def test_precedence_user_project_env(tmp_path: Path, monkeypatch, no_env) -> None:
    _write(user_config_path(), "provider: openai\nmax_concurrent: 3\nbudget_limit_usd: 7\n")
    project = tmp_path / "proj"
    _write(project / ".discovery" / "config.yaml", "max_concurrent: 5\n")

    cfg = load_config(cwd=project)
    assert (cfg.provider, cfg.max_concurrent, cfg.budget_limit_usd) == ("openai", 5, 7.0)
    assert config_sources(cfg) == [str(user_config_path()), str(project / ".discovery" / "config.yaml")]

    monkeypatch.setenv("DISCOVERY_LLM_PROVIDER", "gemini")
    assert load_config(cwd=project).provider == "gemini"


def test_defaults_when_no_files(tmp_path: Path, no_env) -> None:
    cfg = load_config(cwd=tmp_path)
    assert cfg.provider == "ollama" and config_sources(cfg) == []


def test_cli_flags_override_config(tmp_path: Path, no_env) -> None:
    from ai_discovery.cli import app

    cfg_file = _write(tmp_path / "c.yaml", "provider: openai\nbudget_limit_usd: 9\nprod: true\n")
    seen = {}

    def fake_run(**kwargs):
        seen["cfg"] = kwargs["config"]
        return "completed"

    with patch("ai_discovery.pipeline.run_pipeline", side_effect=fake_run):
        result = CliRunner().invoke(app, ["scan", str(tmp_path), "-p", "x", "-o", str(tmp_path / "o"),
                                          "-c", str(cfg_file), "--provider", "ollama", "--budget", "2"])
    assert result.exit_code == 0, result.output
    cfg = seen["cfg"]
    assert (cfg.provider, cfg.budget_limit_usd) == ("ollama", 2.0)
    assert cfg.prod is True  # YAML prod: honoured, not reset by the absent --prod flag


# ── discovery of the project file ─────────────────────────────────────────

def test_project_file_found_walking_up(tmp_path: Path, no_env) -> None:
    _write(tmp_path / "repo" / ".discovery" / "config.yaml", "provider: anthropic\n")
    deep = tmp_path / "repo" / "src" / "x"
    deep.mkdir(parents=True)
    assert load_config(cwd=deep).provider == "anthropic"


def test_legacy_discovery_yaml_in_cwd(tmp_path: Path, no_env) -> None:
    _write(tmp_path / "discovery.yaml", "provider: bedrock\n")
    assert load_config(cwd=tmp_path).provider == "bedrock"


def test_explicit_path_replaces_project_file(tmp_path: Path, no_env) -> None:
    _write(tmp_path / "discovery.yaml", "provider: bedrock\n")
    explicit = _write(tmp_path / "other.yaml", "provider: gemini\n")
    assert load_config(explicit, cwd=tmp_path).provider == "gemini"


def test_missing_explicit_path_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


# ── strict validation ─────────────────────────────────────────────────────

def test_unknown_key_suggests_fix(tmp_path: Path) -> None:
    f = _write(tmp_path / "c.yaml", "provder: openai\nollama:\n  base_ur: x\n")
    with pytest.raises(ConfigError) as exc:
        load_config(f)
    msg = str(exc.value)
    assert "unknown key 'provder' — did you mean 'provider'?" in msg
    assert "unknown key 'ollama.base_ur' — did you mean 'base_url'?" in msg


def test_removed_pm4py_keys_explained(tmp_path: Path) -> None:
    f = _write(tmp_path / "c.yaml", "process_mining:\n  miner_variant: inductive\n")
    with pytest.raises(ConfigError, match="pm4py was removed"):
        load_config(f)


def test_wrong_type_rejected(tmp_path: Path) -> None:
    f = _write(tmp_path / "c.yaml", "max_concurrent: lots\n")
    with pytest.raises(ConfigError, match="max_concurrent"):
        load_config(f)


def test_invalid_yaml_rejected(tmp_path: Path) -> None:
    f = _write(tmp_path / "c.yaml", "provider: [unclosed\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(f)


def test_cli_reports_bad_config_with_exit_2(tmp_path: Path) -> None:
    from ai_discovery.cli import app

    f = _write(tmp_path / "c.yaml", "provder: openai\n")
    result = CliRunner().invoke(app, ["scan", str(tmp_path), "-p", "x", "-c", str(f)])
    assert result.exit_code == 2
    assert "did you mean 'provider'" in " ".join(result.output.split())


# ── one template ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("provider", PROVIDERS)
def test_every_template_loads_cleanly(provider: str, tmp_path: Path) -> None:
    f = _write(tmp_path / "c.yaml", render_template(provider))
    assert load_config(f, include_user=False).provider == provider
    assert "api_key: \"sk" not in f.read_text()


@pytest.mark.parametrize("path,provider", [
    (ROOT / "discovery.yaml", "ollama"),
    (ROOT / "src" / "ai_discovery" / "discovery.yaml.example", "bedrock"),
])
def test_checked_in_templates_are_generated(path: Path, provider: str) -> None:
    assert path.read_text(encoding="utf-8") == render_template(provider), (
        f"{path.name} drifted from config/template.py; regenerate it with render_template({provider!r})"
    )
