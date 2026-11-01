"""Back-compat shim: the walker lives in ``repo/walker.py`` (M6.1)."""

from __future__ import annotations

from .walker import LANG_EXTENSIONS as _LANG_EXTENSIONS
from .walker import SOURCE_EXTENSIONS as _ALL_EXTENSIONS
from .walker import RepoWalker, walk_repo

__all__ = ["walk_repo", "RepoWalker", "_LANG_EXTENSIONS", "_ALL_EXTENSIONS"]
