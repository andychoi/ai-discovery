"""Claude Code plugin + skills (roadmap item M5.4).

Checks the manifest and marketplace, the bundled MCP entry, skill frontmatter,
that every skill CLAUDE.md names resolves to a file, and that every SQL block
in a plugin skill runs against a real (keyless) scan.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from ai_discovery.mcp import targets

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
SKILL_DIRS = (PLUGIN / "skills", ROOT / ".claude" / "skills")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SQL = re.compile(r"```sql\n(.*?)```", re.S)


def _skills() -> dict[str, Path]:
    return {p.parent.name: p for d in SKILL_DIRS if d.exists() for p in sorted(d.glob("*/SKILL.md"))}


def _frontmatter(path: Path) -> dict[str, str]:
    m = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
    assert m, f"{path}: missing YAML frontmatter"
    import yaml

    return yaml.safe_load(m.group(1))


def test_plugin_manifest() -> None:
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == targets.PLUGIN_NAME == "ai-discovery"      # the M5.14 plugin guard matches on it
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", manifest["name"])
    assert manifest["version"] and manifest["description"] and manifest["author"]["name"]
    assert manifest["license"] == "AGPL-3.0-only"


def test_marketplace_lists_the_plugin() -> None:
    market = json.loads((PLUGIN / ".claude-plugin" / "marketplace.json").read_text())
    assert market["owner"]["name"] and market["plugins"][0]["name"] == "ai-discovery"
    assert market["plugins"][0]["source"] == "./" and ".." not in market["plugins"][0]["source"]


def test_bundled_mcp_server_matches_install_entry(tmp_path) -> None:
    servers = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]
    expected = targets.entry(targets.placement("claude", "project", tmp_path))
    assert servers == {"ai-discovery": expected}


def test_skill_frontmatter() -> None:
    skills = _skills()
    assert {"discover-triage", "discover-consistency", "roadmap-next"} <= set(skills)
    for name, path in skills.items():
        fm = _frontmatter(path)
        assert fm.get("name") == name, f"{path}: frontmatter name must equal the folder name"
        assert len(fm.get("description", "")) >= 60, f"{path}: description too short to trigger reliably"


def test_every_skill_claude_md_names_exists() -> None:
    named = set(re.findall(r"^### `/([a-z0-9-]+)`", (ROOT / "CLAUDE.md").read_text(), re.M))
    assert {"parser-extension", "call-graph-debug", "pipeline-analyze", "discover-triage",
            "discover-consistency"} <= named
    missing = sorted(named - set(_skills()))
    assert not missing, f"CLAUDE.md names skills with no SKILL.md: {missing}"


def test_plugin_skills_are_read_only_and_cite_untrusted_rule() -> None:
    for name in ("discover-triage", "discover-consistency"):
        text = (PLUGIN / "skills" / name / "SKILL.md").read_text()
        assert "mode=ro" in text and "data, not instructions" in text
        assert not re.search(r"\b(UPDATE|DELETE|INSERT|DROP)\b", "\n".join(_SQL.findall(text)))


@pytest.fixture(scope="module")
def index_db(tmp_path_factory) -> Path:
    import os
    import shutil
    import subprocess

    from typer.testing import CliRunner

    from ai_discovery.cli import app
    from tests.golden.test_golden import _GIT_ENV

    repo = tmp_path_factory.mktemp("plugin") / "repo"
    shutil.copytree(ROOT / "tests" / "fixtures" / "projects" / "spring-boot-app", repo)
    env = {**os.environ, **_GIT_ENV}
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "i"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True)
    res = CliRunner().invoke(app, ["-q", "-C", str(repo), "scan"])
    assert res.exit_code == 0, res.output
    return repo / ".discovery" / "discovery.db"


@pytest.mark.parametrize("skill", ["discover-triage", "discover-consistency"])
def test_skill_sql_runs_on_a_real_index(index_db, skill) -> None:
    queries = _SQL.findall((PLUGIN / "skills" / skill / "SKILL.md").read_text())
    assert queries
    conn = sqlite3.connect(f"file:{index_db}?mode=ro", uri=True)
    sid = conn.execute("SELECT scan_id FROM current_scan WHERE id = 1").fetchone()[0]
    for q in queries:
        conn.execute(q, {"sid": sid}).fetchall()          # raises on a bad table/column name
    conn.close()


def test_tools_named_in_plugin_skills_exist() -> None:
    from ai_discovery.mcp.registry import discover

    tools = set(discover())
    for path in sorted((PLUGIN / "skills").glob("*/SKILL.md")):
        named = set(re.findall(r"`(get_[a-z_]+|search)`", path.read_text()))
        unknown = named - tools
        assert not unknown, f"{path.parent.name} names tools that do not exist: {sorted(unknown)}"


def test_impact_skill_protocol() -> None:
    text = (PLUGIN / "skills" / "discover-impact" / "SKILL.md").read_text()
    for needle in ("Stop rules", "confirmed", "refuted", "unclear", "never drop", "ask the user which one",
                   "truncated", "data, not instructions", "never edits code"):
        assert needle in text, needle
    assert (PLUGIN / "skills" / "discover-impact" / "examples" / "fixture-dry-run.md").exists()


def test_change_plan_skill_and_examples() -> None:
    root = PLUGIN / "skills" / "discover-change-plan"
    text = (root / "SKILL.md").read_text()
    for needle in ("IMPACT_REPORT.md", "IMPLEMENTATION_PLAN.md", "Pinned target files", "Style exemplars",
                   "Backward compatibility", "Do not edit any source file", "discover-impact"):
        assert needle in text, needle
    report = (root / "examples" / "IMPACT_REPORT.md").read_text()
    plan = (root / "examples" / "IMPLEMENTATION_PLAN.md").read_text()
    for heading in ("## Targets", "## Confirmed impact", "## Entry points reached", "## Data impact", "## Unknowns"):
        assert heading in report, heading
    for heading in ("## Pinned target files", "## Style exemplars", "## Steps", "## Backward compatibility",
                    "## Verification", "## Rollback"):
        assert heading in plan, heading
    # every file:line cited in the examples exists in the fixture at that line
    fixture = ROOT / "tests" / "fixtures" / "projects" / "spring-boot-app"
    for name, line in re.findall(r"`?([A-Za-z]+\.java):(\d+)", report + plan):
        matches = list(fixture.rglob(name))
        assert matches, name
        assert int(line) <= len(matches[0].read_text().splitlines()), f"{name}:{line}"
