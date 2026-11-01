"""Back-compat shim: language detection lives in ``repo/walker.py`` (M6.1)."""

from __future__ import annotations

from .walker import EXT_TO_LANG as _EXT_MAP
from .walker import LEGACY_SKIP_DIRS as _SKIP_DIRS   # pre-M6.1 bare-name set; prefer walker.excluded_path
from .walker import MANIFESTS as _MANIFEST_MAP
from .walker import detect_languages

__all__ = ["detect_languages", "_SKIP_DIRS", "_EXT_MAP", "_MANIFEST_MAP"]
