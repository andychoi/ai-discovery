"""Walk a repository yielding source files filtered by language."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .lang_detector import _SKIP_DIRS

_LANG_EXTENSIONS: dict[str, frozenset[str]] = {
    "csharp": frozenset({".cs"}),
    "java": frozenset({".java"}),
    "python": frozenset({".py"}),
    "javascript": frozenset({".js", ".ts", ".tsx", ".jsx"}),
    "webforms": frozenset({".aspx", ".ascx"}),
    "jsp": frozenset({".jsp", ".jspx", ".tag", ".tagx"}),
    # A-6 import-map tier
    "go": frozenset({".go"}),
    "rust": frozenset({".rs"}),
    "ruby": frozenset({".rb"}),
    "php": frozenset({".php"}),
}

# Union of all recognized extensions for the "yield all" case.
_ALL_EXTENSIONS: frozenset[str] = frozenset().union(*_LANG_EXTENSIONS.values())


def walk_repo(
    repo_path: Path,
    languages: set[str] | None = None,
) -> Iterator[Path]:
    """Yield source files under *repo_path*, skipping ``_SKIP_DIRS``.

    Parameters
    ----------
    repo_path:
        Root of the repository to walk.
    languages:
        If provided, only yield files whose extension belongs to one of the
        listed languages.  If ``None``, yield all recognised source files.

    Yields paths in sorted order (directories and filenames are sorted
    alphabetically at each level).
    """
    if languages is not None:
        allowed: frozenset[str] = frozenset().union(
            *(
                _LANG_EXTENSIONS[lang]
                for lang in languages
                if lang in _LANG_EXTENSIONS
            )
        )
    else:
        allowed = _ALL_EXTENSIONS

    yield from _walk(repo_path, allowed)


def _walk(root: Path, allowed: frozenset[str]) -> Iterator[Path]:
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except PermissionError:
        return
    for entry in entries:
        if entry.is_dir():
            if entry.name in _SKIP_DIRS:
                continue
            yield from _walk(entry, allowed)
        elif entry.suffix.lower() in allowed:
            yield entry
