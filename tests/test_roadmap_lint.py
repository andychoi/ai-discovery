"""Keep docs/roadmap/milestones parseable so sessions can rely on roadmap_lint --next."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("roadmap_lint", ROOT / "scripts" / "roadmap_lint.py")
roadmap_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(roadmap_lint)


def test_milestones_are_valid():
    paths = sorted(roadmap_lint.MILESTONES.glob("M*.md"))
    assert paths, "no milestone files"
    items, errors = roadmap_lint.parse(paths)
    errors += roadmap_lint.validate(items)
    assert not errors, "\n".join(errors)


def test_ready_excludes_blocked_and_exploring(tmp_path):
    md = tmp_path / "M9-x.md"
    md.write_text(
        "### M9.1 A\n- **Status:** Done (abc)\n- **Size:** S\n- **Lane:** A\n- **Depends on:** —\n"
        "- **Owns:** x\n- **Acceptance:** y\n- **Verify:** z\n\n"
        "### M9.2 B\n- **Status:** Planned\n- **Size:** S\n- **Lane:** A\n- **Depends on:** M9.1\n"
        "- **Owns:** x\n- **Acceptance:** y\n- **Verify:** z\n\n"
        "### M9.3 C\n- **Status:** Planned\n- **Size:** S\n- **Lane:** C\n- **Depends on:** M9.2\n"
        "- **Owns:** x\n- **Acceptance:** y\n- **Verify:** z\n\n"
        "### M9.4 D\n- **Status:** Exploring\n- **Size:** S\n- **Lane:** A\n- **Depends on:** —\n"
        "- **Owns:** x\n- **Acceptance:** y\n- **Verify:** z\n",
        encoding="utf-8",
    )
    items, errors = roadmap_lint.parse([md])
    assert not errors and not roadmap_lint.validate(items)
    assert [i["id"] for i in roadmap_lint.ready(items, None)] == ["M9.2"]
    assert roadmap_lint.ready(items, "C") == []


def test_unknown_dependency_is_an_error(tmp_path):
    md = tmp_path / "M9-x.md"
    md.write_text(
        "### M9.1 A\n- **Status:** Planned\n- **Size:** S\n- **Lane:** A\n- **Depends on:** M9.9\n"
        "- **Owns:** x\n- **Acceptance:** y\n- **Verify:** z\n",
        encoding="utf-8",
    )
    items, _ = roadmap_lint.parse([md])
    assert any("unknown dependency M9.9" in e for e in roadmap_lint.validate(items))
