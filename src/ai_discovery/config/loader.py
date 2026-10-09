"""Layered, validated config loading (roadmap item M1.3).

Precedence (later wins): dataclass defaults < user file < project file <
environment < CLI flags (applied by the caller). Files are deep-merged as plain
dicts, then the merged dict is validated once with pydantic against the
existing ``DiscoveryConfig`` dataclasses — unknown keys and wrong types are
errors, never silently ignored.
"""

from __future__ import annotations

import difflib
import os
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ConfigDict, TypeAdapter, ValidationError

from ..core.paths import STATE_DIRNAME, find_project
from . import (
    AdvisorConfig, AIGWConfig, AnthropicConfig, BedrockConfig, DiscoveryConfig, GeminiConfig,
    MLXGemmaConfig, MLXQwenConfig, OllamaConfig, OMLXConfig, OpenAIConfig, OpenRouterConfig,
    ProcessMiningConfig,
    RagConfig, _apply_env_overrides,
)

PROJECT_FILE = "config.yaml"          # inside .discovery/
LEGACY_PROJECT_FILE = "discovery.yaml"  # in the working directory

# Keys removed in earlier versions → explanation shown instead of a generic error.
REMOVED_KEYS: dict[str, str] = {
    "process_mining.miner_variant": "pm4py was removed (13e3010); delete this key",
    "process_mining.fitness_threshold": "pm4py was removed (13e3010); delete this key",
    "process_mining.precision_threshold": "pm4py was removed (13e3010); delete this key",
    "process_mining.generalization_threshold": "pm4py was removed (13e3010); delete this key",
}

_SECTIONS = (
    BedrockConfig, OllamaConfig, MLXGemmaConfig, MLXQwenConfig, OpenAIConfig,
    GeminiConfig, AnthropicConfig, AIGWConfig, OMLXConfig, OpenRouterConfig, RagConfig, ProcessMiningConfig, AdvisorConfig,
    DiscoveryConfig,
)
for _cls in _SECTIONS:
    # Strict validation for stdlib dataclasses (pydantic honours this attribute).
    _cls.__pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[attr-defined]

_ADAPTER = TypeAdapter(DiscoveryConfig)


class ConfigError(ValueError):
    """Invalid configuration; message names the file, key and a fix."""


def user_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "ai-discovery" / "config.yaml"


def project_config_path(cwd: Optional[Path] = None) -> Optional[Path]:
    """``<project>/.discovery/config.yaml`` (walking up), else ``./discovery.yaml``."""
    here = Path(cwd or Path.cwd())
    project = find_project(here)
    if project is not None and (project / STATE_DIRNAME / PROJECT_FILE).is_file():
        return project / STATE_DIRNAME / PROJECT_FILE
    legacy = here / LEGACY_PROJECT_FILE
    return legacy if legacy.is_file() else None


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(data).__name__}")
    return data


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _known_keys(loc: tuple) -> list[str]:
    """Field names valid at *loc* (a pydantic error location, minus the bad key)."""
    cls: Any = DiscoveryConfig
    for part in loc:
        if not is_dataclass(cls):
            return []
        match = next((f for f in fields(cls) if f.name == part), None)
        if match is None:
            return []
        cls = match.default_factory() if callable(match.default_factory) else match.default  # type: ignore[misc]
        cls = type(cls)
    return [f.name for f in fields(cls)] if is_dataclass(cls) else []


def _explain(exc: ValidationError, sources: list[str]) -> str:
    lines = [f"Invalid configuration (from {', '.join(sources) or 'defaults'}):"]
    for err in exc.errors():
        loc = tuple(str(p) for p in err["loc"])
        dotted = ".".join(loc)
        if err["type"] in ("extra_forbidden", "unexpected_keyword_argument"):
            if dotted in REMOVED_KEYS:
                lines.append(f"  - {dotted}: {REMOVED_KEYS[dotted]}")
                continue
            hint = difflib.get_close_matches(loc[-1], _known_keys(loc[:-1]), n=1)
            lines.append(f"  - unknown key '{dotted}'" + (f" — did you mean '{hint[0]}'?" if hint else ""))
        else:
            lines.append(f"  - {dotted}: {err['msg']} (got {err.get('input')!r})")
    return "\n".join(lines)


def load_config(config_path: Optional[str | Path] = None, *, cwd: Optional[Path] = None,
                include_user: bool = True) -> DiscoveryConfig:
    """Load, merge and validate configuration (see module docstring)."""
    merged: dict = {}
    sources: list[str] = []

    layers: list[Path] = []
    if include_user and user_config_path().is_file():
        layers.append(user_config_path())
    if config_path is not None:
        explicit = Path(config_path)
        if not explicit.is_file():
            raise ConfigError(f"Config file not found: {explicit}")
        layers.append(explicit)
    else:
        found = project_config_path(cwd)
        if found is not None:
            layers.append(found)

    for path in layers:
        merged = _deep_merge(merged, _read_yaml(path))
        sources.append(str(path))

    try:
        cfg = _ADAPTER.validate_python(merged) if merged else DiscoveryConfig()
    except ValidationError as exc:
        raise ConfigError(_explain(exc, sources)) from None

    cfg = _apply_env_overrides(cfg, yaml_has_bedrock=isinstance(merged.get("bedrock"), dict))
    cfg.__dict__["_sources"] = sources  # for status/doctor; not a config key
    return cfg


def config_sources(cfg: DiscoveryConfig) -> list[str]:
    """Files that contributed to *cfg*, lowest precedence first."""
    return list(cfg.__dict__.get("_sources", []))
