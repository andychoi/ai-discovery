"""Tests for lang_detector and file_walker modules."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_discovery.repo.lang_detector import detect_languages
from ai_discovery.repo.file_walker import walk_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")


# ---------------------------------------------------------------------------
# detect_languages tests
# ---------------------------------------------------------------------------

def test_detect_csharp_from_csproj(tmp_path: Path) -> None:
    """A .csproj manifest + .cs files should detect csharp with manifest."""
    _touch(tmp_path / "MyApp" / "MyApp.csproj")
    _touch(tmp_path / "MyApp" / "Program.cs")
    _touch(tmp_path / "MyApp" / "Startup.cs")

    result = detect_languages(tmp_path)

    assert "csharp" in result
    assert result["csharp"]["file_count"] == 2
    assert "csproj" in result["csharp"]["manifests"]
    assert ".cs" in result["csharp"]["extensions"]


def test_detect_java_from_pom(tmp_path: Path) -> None:
    """pom.xml + .java files should detect java."""
    _touch(tmp_path / "pom.xml")
    _touch(tmp_path / "src" / "main" / "App.java")
    _touch(tmp_path / "src" / "main" / "Util.java")

    result = detect_languages(tmp_path)

    assert "java" in result
    assert result["java"]["file_count"] == 2
    assert "pom.xml" in result["java"]["manifests"]
    assert ".java" in result["java"]["extensions"]


def test_detect_python_from_requirements(tmp_path: Path) -> None:
    """requirements.txt + .py files should detect python."""
    _touch(tmp_path / "requirements.txt")
    _touch(tmp_path / "app" / "main.py")

    result = detect_languages(tmp_path)

    assert "python" in result
    assert result["python"]["file_count"] == 1
    assert "requirements.txt" in result["python"]["manifests"]
    assert ".py" in result["python"]["extensions"]


def test_detect_javascript_from_package_json(tmp_path: Path) -> None:
    """package.json + .js files should detect javascript."""
    _touch(tmp_path / "package.json")
    _touch(tmp_path / "src" / "index.js")
    _touch(tmp_path / "src" / "utils.ts")

    result = detect_languages(tmp_path)

    assert "javascript" in result
    assert result["javascript"]["file_count"] == 2
    assert "package.json" in result["javascript"]["manifests"]
    assert ".js" in result["javascript"]["extensions"]
    assert ".ts" in result["javascript"]["extensions"]


def test_file_walker_respects_gitignore(tmp_path: Path) -> None:
    """Files inside node_modules (a skip dir) should NOT be yielded."""
    _touch(tmp_path / "src" / "index.js")
    _touch(tmp_path / "node_modules" / "lib" / "foo.js")

    files = list(walk_repo(tmp_path))

    names = [f.name for f in files]
    assert "index.js" in names
    assert "foo.js" not in names
