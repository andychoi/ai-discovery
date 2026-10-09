"""Tree-sitter floor test (roadmap item M0.3).

Guards against a grammar/binding version that makes a language *silently*
extract nothing. Every module-level ``Query`` in the tree-sitter parsers must
compile, every grammar must load, and every parser must extract at least one
node from a minimal sample.

To check the pinned minimum versions in ``pyproject.toml`` themselves, run this
file in a venv with the floor versions installed (see docs/roadmap/PROGRESS.md,
M0.3 entry).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from tree_sitter import Query

TREE_SITTER_PARSER_MODULES = [
    "ai_discovery.parsers.java",
    "ai_discovery.parsers.csharp",
    "ai_discovery.parsers.python_parser",
    "ai_discovery.parsers.javascript",
]

# (parser class path, file name, minimal source) — each must yield >= 1 node.
SAMPLES = [
    ("ai_discovery.parsers.java:JavaParser", "Foo.java",
     "package a; public class Foo { public void bar() { baz(); } void baz() {} }\n"),
    ("ai_discovery.parsers.csharp:CSharpParser", "Foo.cs",
     "namespace A.B { public class Foo { public void Bar() { Baz(); } void Baz() {} } }\n"),
    ("ai_discovery.parsers.python_parser:PythonParser", "foo.py",
     "def bar():\n    return baz()\n\ndef baz():\n    return 1\n"),
    ("ai_discovery.parsers.javascript:JavaScriptParser", "foo.js",
     "function bar() { return baz(); }\nfunction baz() { return 1; }\n"),
    ("ai_discovery.parsers.javascript:JavaScriptParser", "foo.ts",
     "export function bar(): number { return baz(); }\nfunction baz(): number { return 1; }\n"),
]


@pytest.mark.parametrize("module_name", TREE_SITTER_PARSER_MODULES)
def test_every_module_query_compiles(module_name: str) -> None:
    # Queries are built at import time; a grammar/binding mismatch raises here.
    module = importlib.import_module(module_name)
    queries = [v for v in vars(module).values() if isinstance(v, Query)]
    assert queries, f"{module_name} defines no module-level tree-sitter Query"


@pytest.mark.parametrize("target,filename,source", SAMPLES, ids=[s[1] for s in SAMPLES])
def test_parser_extracts_nodes(target: str, filename: str, source: str, tmp_path: Path) -> None:
    module_name, cls_name = target.split(":")
    parser = getattr(importlib.import_module(module_name), cls_name)()
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    nodes = parser.parse_file(path)
    assert nodes, f"{cls_name} extracted 0 nodes from {filename} — grammar/query floor broken"


def test_route_parser_grammars_load() -> None:
    # route_parser disables itself silently when grammars fail to load.
    route_parser = importlib.import_module("ai_discovery.route_parser")
    assert route_parser._TS_AVAILABLE, "route_parser could not load JS/TS grammars (silent screen loss)"


CRASH_FIXTURES = Path(__file__).parent / "fixtures" / "regressions"


@pytest.mark.parametrize(
    "path", sorted(p for p in CRASH_FIXTURES.rglob("*") if p.suffix in {".cs", ".java", ".js", ".ts", ".py"}),
    ids=lambda p: p.parent.name + "/" + p.name,
)
def test_known_crash_fixtures_parse(path: Path) -> None:
    """Files that crashed a tree-sitter release must parse cleanly.

    Run in a subprocess: a native segfault would otherwise kill pytest.
    """
    import subprocess
    import sys

    code = (
        "import sys; from pathlib import Path\n"
        "from ai_discovery.parsers.csharp import CSharpParser\n"
        "from ai_discovery.parsers.java import JavaParser\n"
        "from ai_discovery.parsers.javascript import JavaScriptParser\n"
        "from ai_discovery.parsers.python_parser import PythonParser\n"
        "f = Path(sys.argv[1])\n"
        "for p in (CSharpParser(), JavaParser(), JavaScriptParser(), PythonParser()):\n"
        "    if p.can_parse(f):\n"
        "        for _ in range(3): assert p.parse_file(f)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, f"parser crashed (rc={proc.returncode}) on {path.name}: {proc.stderr[-500:]}"
