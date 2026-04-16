"""Detect programming languages from file extensions and manifest files."""

from __future__ import annotations

from pathlib import Path

_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", "venv", ".venv",
    "migrations", "dist", "build", ".tox", "bin", "obj",
    ".idea", ".vs", ".vscode", "target", "packages",
})

_EXT_MAP: dict[str, str] = {
    ".cs": "csharp",
    ".java": "java",
    ".py": "python",
    ".js": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".jsx": "javascript",
}

_MANIFEST_MAP: dict[str, str] = {
    "csproj": "csharp",
    "sln": "csharp",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "setup.py": "python",
    "Pipfile": "python",
    "package.json": "javascript",
}


def detect_languages(repo_path: Path) -> dict[str, dict]:
    """Detect languages present in a repository.

    Returns a dict keyed by language name, e.g.::

        {"csharp": {"file_count": 42, "manifests": ["csproj"], "extensions": [".cs"]}, ...}

    Walks the repo tree, skipping directories in ``_SKIP_DIRS``.
    Counts source files by extension and records any matching manifest files.
    """
    # lang -> {file_count, manifests set, extensions set}
    stats: dict[str, dict] = {}

    def _ensure(lang: str) -> dict:
        if lang not in stats:
            stats[lang] = {"file_count": 0, "manifests": set(), "extensions": set()}
        return stats[lang]

    for item in _walk(repo_path):
        name = item.name
        if item.is_file():
            # Check extension
            ext = item.suffix.lower()
            if ext in _EXT_MAP:
                lang = _EXT_MAP[ext]
                entry = _ensure(lang)
                entry["file_count"] += 1
                entry["extensions"].add(ext)

            # Check manifest match: exact name or ends with .{key}
            for key, lang in _MANIFEST_MAP.items():
                if name == key or name.endswith(f".{key}"):
                    entry = _ensure(lang)
                    entry["manifests"].add(key)

    # Convert sets to sorted lists for stable output
    for entry in stats.values():
        entry["manifests"] = sorted(entry["manifests"])
        entry["extensions"] = sorted(entry["extensions"])

    return stats


def _walk(root: Path):
    """Recursively yield all file Path objects under *root*, skipping _SKIP_DIRS."""
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except PermissionError:
        return
    for entry in entries:
        if entry.is_dir():
            if entry.name in _SKIP_DIRS:
                continue
            yield from _walk(entry)
        else:
            yield entry
