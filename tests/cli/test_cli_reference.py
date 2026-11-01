"""The README CLI reference is generated from the Typer app (roadmap item M2.7) — fail when it is stale."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _gen():
    spec = importlib.util.spec_from_file_location("gen_cli_reference", ROOT / "scripts" / "gen_cli_reference.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_readme_cli_reference_is_current() -> None:
    gen = _gen()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert gen.splice(readme, gen.render()) == readme, "run: python scripts/gen_cli_reference.py"


def test_cli_reference_lists_every_command_and_no_removed_one() -> None:
    block = _gen().render()
    for name in ("init", "scan", "status", "doctor", "serve", "export", "drift", "push", "ask", "impact", "mcp serve"):
        assert f"### `discover {name}`" in block
    for gone in ("chat", "ingest", "query", "federate", "view", "test-llm", "export-graph", "verify-drift",
                 "detect-screens"):
        assert f"### `discover {gone}`" not in block
